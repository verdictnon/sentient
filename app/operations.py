"""Persistent operations, track investigation and report scheduling for the existing app."""

from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, RLock, Thread
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import csv
import io
import json
import shutil
import subprocess
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.insights import analyse
from app.journeys import track_journey
from app.p1.geometry import load_geometry
from app.p2.persistence import read_database
from app.rollups import ROLLUP_VERSION, day_rollups, payload as period_payload
from app.simulation import RULES as SIMULATED_RULES
from app.storage import canonical, stable_id

UTC = timezone.utc
SIMULATED_WARNING = "Simulated data. The demo store generated these numbers; nothing here was measured in a shop."
DEFAULTS = dict(
    workspace_name="Sentient Retail",
    timezone="Asia/Almaty",
    dwell_limit_s=20,
    crowd_limit=4,
    queue_limit=3,
    shift_start="09:00",
    shift_end="18:00",
    currency="KZT",
    avg_item_value=0,
)


def utcnow():
    return datetime.now(UTC)


def timestamp(value):
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("Use an ISO timestamp with a timezone")
    if d.tzinfo is None:
        raise ValueError("Recording/report times must include a timezone")
    return d.astimezone(UTC)


def zone(value):
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown IANA timezone")


def clock(value):
    try:
        d = datetime.strptime(value, "%H:%M")
        return d.hour, d.minute
    except ValueError:
        raise ValueError("Use a 24-hour HH:MM time")


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_name: str = Field(default="Sentient Retail", min_length=1, max_length=100)
    timezone: str = "Asia/Almaty"
    dwell_limit_s: float = Field(default=20, ge=1, le=7200)
    crowd_limit: int = Field(default=4, ge=2, le=100)
    queue_limit: int = Field(default=3, ge=1, le=100)
    shift_start: str = "09:00"
    shift_end: str = "18:00"
    currency: str = Field(default="KZT", min_length=1, max_length=8)
    avg_item_value: float = Field(default=0, ge=0, le=100000000)


class RecordingContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="", max_length=120)
    started_at_utc: str | None = None
    shift_label: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=3000)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^(pending|accepted|rejected)$")
    notes: str = Field(default="", max_length=3000)
    reviewer: str = Field(default="Local operator", min_length=1, max_length=100)
    expected_revision: int = Field(default=0, ge=0)


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Retail operations report", min_length=1, max_length=160)
    scope: str = Field(
        default="collection", pattern="^(collection|shift|day|month|custom)$"
    )
    start: str | None = None
    end: str | None = None
    video_ids: list[str] = Field(default_factory=list, max_length=500)
    store_id: str | None = None
    notes: str = Field(default="", max_length=5000)
    lang: str = Field(default="en", pattern="^(en|ru)$")


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    cadence: str = Field(pattern="^(shift|daily|monthly)$")
    timezone: str = "Asia/Almaty"
    shift_start: str = "09:00"
    shift_end: str = "18:00"
    store_id: str | None = None
    enabled: bool = True


class Operations:
    def __init__(self, catalog, tracking, actions, live):
        self.catalog, self.tracking, self.actions, self.live = (
            catalog,
            tracking,
            actions,
            live,
        )
        self.lock = RLock()
        self.cache = OrderedDict()
        self.stop = Event()
        self.thread = None
        self.root = catalog.root / "reports"
        self.root.mkdir(exist_ok=True)
        with catalog.connect(write=True) as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS ops_settings(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ops_context(video_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ops_reviews(run_key TEXT NOT NULL,event_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_key,event_id));
            CREATE TABLE IF NOT EXISTS ops_reports(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,payload TEXT NOT NULL,schedule_token TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS ops_schedules(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ops_simulated_days(store_id TEXT NOT NULL,day TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(store_id,day));
            CREATE TABLE IF NOT EXISTS ops_rollup_cache(key TEXT PRIMARY KEY,video_id TEXT NOT NULL,payload TEXT NOT NULL);""")
        self.demo = dict(status="idle", progress=0.0, stage=None, error=None)

    def settings(self):
        with self.catalog.connect() as db:
            r = db.execute(
                "SELECT payload FROM ops_settings WHERE id='workspace'"
            ).fetchone()
        return DEFAULTS | (json.loads(r[0]) if r else {})

    def save_settings(self, value):
        zone(value["timezone"])
        clock(value["shift_start"])
        clock(value["shift_end"])
        if value["shift_start"] == value["shift_end"]:
            raise ValueError("Shift start and end must differ")
        with self.catalog.connect(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO ops_settings VALUES ('workspace',?)",
                (canonical(value),),
            )
        with self.lock:
            self.cache.clear()
        return value

    def contexts(self):
        with self.catalog.connect() as db:
            return {
                r[0]: json.loads(r[1]) for r in db.execute("SELECT * FROM ops_context")
            }

    def save_context(self, key, value):
        self.catalog.video(key)
        if value["started_at_utc"]:
            value["started_at_utc"] = timestamp(value["started_at_utc"]).isoformat()
        value["time_basis"] = (
            "operator_assigned" if value["started_at_utc"] else "unknown"
        )
        with self.catalog.connect(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO ops_context VALUES (?,?)",
                (key, canonical(value)),
            )
        return value

    def runs(self, video_id=None):
        with self.catalog.connect() as db:
            actions = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM action_analysis_jobs"
                    + (" WHERE video_id=?" if video_id else ""),
                    (video_id,) if video_id else (),
                )
            ]
            tracking = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM tracking_jobs"
                    + (" WHERE video_id=?" if video_id else ""),
                    (video_id,) if video_id else (),
                )
            ]
        rows = []
        represented = set()
        available_tracking = {
            j["id"]
            for j in tracking
            if j["status"] == "completed" and Path(j["database_path"]).is_file()
        }
        for j in actions:
            if (
                j["status"] == "completed"
                and j.get("tracking_job_id") in available_tracking
            ):
                rows.append(
                    dict(
                        key="offline:" + j["id"],
                        video_id=j["video_id"],
                        kind="offline",
                        status=j["status"],
                        geometry_id=j["geometry_id"],
                        created_at=j.get("finished_at_utc", j["created_at_utc"]),
                        tracking_job_id=j["tracking_job_id"],
                        model_id=j.get("model_id"),
                    )
                )
                represented.add(j["tracking_job_id"])
        for j in tracking:
            if j["id"] in available_tracking and j["id"] not in represented:
                rows.append(
                    dict(
                        key="track:" + j["id"],
                        video_id=j["video_id"],
                        kind="track",
                        status=j["status"],
                        geometry_id=j["geometry_id"],
                        created_at=j.get("finished_at_utc", j["created_at_utc"]),
                        tracking_job_id=j["id"],
                    )
                )
        for path in self.live.root.glob("*/summary.json"):
            try:
                p = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if (
                p.get("status") not in ("completed", "stopped")
                or not p.get("processed_frames")
                or not (path.parent / "observations.jsonl").is_file()
                or (video_id and p["video_id"] != video_id)
            ):
                continue
            rows.append(
                dict(
                    key="live:" + p["id"],
                    video_id=p["video_id"],
                    kind="live",
                    status=p["status"],
                    geometry_id=p["geometry_id"],
                    created_at=datetime.fromtimestamp(
                        path.stat().st_mtime, UTC
                    ).isoformat(),
                    model_id=p.get("model_id"),
                )
            )
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    def run(self, key):
        try:
            kind, identity = key.split(":", 1)
        except ValueError:
            raise KeyError("Run not found")
        if kind == "live":
            s = self.live.get(identity)
            if not s["done"].is_set():
                raise ValueError(
                    "Stop live analysis before investigating the saved run"
                )
            p = s["payload"]
            path = s["folder"] / "observations.jsonl"
            if not path.is_file():
                raise KeyError("Run has no observations")
            return dict(
                key=key,
                video_id=s["video_id"],
                kind=kind,
                geometry_id=s["geometry_id"],
                path=path,
                pickups=p.get("pickups", []),
                model_id=p.get("model_id"),
                threshold=p.get("threshold"),
            )
        if kind not in ("offline", "track"):
            raise KeyError("Run not found")
        j = (
            self.actions.get(identity)
            if kind == "offline"
            else self.tracking.get(identity)
        )
        if j["status"] != "completed":
            raise ValueError("Wait for analysis to finish")
        t = self.tracking.get(j["tracking_job_id"]) if kind == "offline" else j
        pickups = []
        if kind == "offline":
            with self.catalog.connect() as db:
                r = db.execute(
                    "SELECT payload FROM pickup_event_sets WHERE analysis_id=?",
                    (j["analysis_id"],),
                ).fetchone()
            if r:
                pickups = json.loads(r[0]).get("events", [])
        return dict(
            key=key,
            video_id=j["video_id"],
            kind=kind,
            geometry_id=j["geometry_id"],
            path=Path(t["database_path"]),
            pickups=pickups,
            model_id=j.get("model_id"),
            tracking_job_id=t["id"],
            threshold=j.get("pickup_policy", {}).get("threshold"),
        )

    def observations(self, run):
        if run["kind"] == "live":
            with run["path"].open() as source:
                return [json.loads(line) for line in source if line.strip()]
        video = self.catalog.video(run["video_id"])
        db = read_database(run["path"])
        try:
            with self.catalog.connect() as catalog:
                times = {
                    r[0]: r[1]
                    for r in catalog.execute(
                        "SELECT frame_index,media_ms FROM frames WHERE sha=?",
                        (video["media_sha256"],),
                    )
                }
            rows = {
                r[0]: dict(frame_index=r[0], media_ms=times[r[0]], tracks=[])
                for r in db.execute(
                    "SELECT frame_index FROM p2_frame_info WHERE detector_ran=1"
                )
                if r[0] in times
            }
            for r in db.execute(
                "SELECT payload FROM inputs WHERE kind='observation' ORDER BY media_time_ms,track"
            ):
                p = json.loads(r[0])
                frame = p["frame_index"]
                if frame in rows:
                    rows[frame]["tracks"].append(
                        dict(
                            local_track_id=p["local_track_id"],
                            position_status=p["position_status"],
                            box=p["bbox_xyxy_px"],
                            score=p.get("score"),
                        )
                    )
            return sorted(rows.values(), key=lambda r: r["media_ms"])
        finally:
            db.close()

    def reviews(self, key):
        with self.catalog.connect() as db:
            return {
                r[0]: json.loads(r[1])
                for r in db.execute(
                    "SELECT event_id,payload FROM ops_reviews WHERE run_key=?", (key,)
                )
            }

    def insight(self, key):
        run = self.run(key)
        saved = load_geometry(self.catalog, run["video_id"])
        settings = self.settings()
        cache_key = (
            key,
            str(run["path"].stat().st_mtime_ns),
            saved["id"],
            canonical(settings),
        )
        with self.lock:
            cached = self.cache.get(cache_key)
        if cached is None:
            rows = self.observations(run)
            video = self.catalog.video(run["video_id"])
            result = analyse(
                rows,
                saved["geometry"],
                run["pickups"],
                rules=rules_for(video, settings),
                physical_time=video["source_profile"]["source_kind"] != "sparse_clip",
            )
            result["summary"]["pickup_scoring_available"] = run["kind"] != "track"
            if run["kind"] == "track":
                result["summary"]["pickups"] = None
                for track in result["tracks"]:
                    track["pickups"] = None
            result.update(
                run={k: v for k, v in run.items() if k not in ("path", "pickups")},
                geometry=saved,
                video=dict(
                    id=video["id"],
                    name=video["display_name"],
                    camera_id=video["camera_id"],
                    store_id=video["store_id"],
                    source_profile=video["source_profile"],
                ),
            )
            cached = (result, rows)
            with self.lock:
                self.cache[cache_key] = cached
                while len(self.cache) > 4:
                    self.cache.popitem(last=False)
        # Review state is always fresh, while geometric analytics can be cached.
        result = json.loads(json.dumps(cached[0]))
        reviews = self.reviews(key)
        for e in result["incidents"]:
            e["review"] = reviews.get(
                e["id"], dict(status="pending", notes="", revision=0)
            )
        for status in ("pending", "accepted", "rejected"):
            result["summary"][status] = sum(
                e["review"]["status"] == status for e in result["incidents"]
            )
        return result, cached[1]

    def review(self, key, event_id, value):
        result, _ = self.insight(key)
        if not any(e["id"] == event_id for e in result["incidents"]):
            raise KeyError("Incident not found in this run")
        with self.catalog.connect(write=True) as db:
            r = db.execute(
                "SELECT payload FROM ops_reviews WHERE run_key=? AND event_id=?",
                (key, event_id),
            ).fetchone()
            revision = json.loads(r[0])["revision"] if r else 0
            if revision != value.pop("expected_revision"):
                raise RuntimeError(
                    "This review changed in another view; refresh and retry"
                )
            value.update(revision=revision + 1, updated_at=utcnow().isoformat())
            db.execute(
                "INSERT OR REPLACE INTO ops_reviews VALUES (?,?,?)",
                (key, event_id, canonical(value)),
            )
        return value

    def workspace(self):
        contexts = self.contexts()
        runs = self.runs()
        by_video = defaultdict(list)
        for r in runs:
            by_video[r["video_id"]].append(r)
        recordings = [
            dict(
                id=v["id"],
                name=v["display_name"],
                camera_id=v["camera_id"],
                store_id=v["store_id"],
                duration_seconds=round(
                    (v.get("declared_duration_ms") or v["indexed_span_ms"]) / 1000, 2
                ),
                dimensions=v["frame_dims_px"],
                source_kind=v["source_profile"]["source_kind"],
                simulated=v["source_profile"].get("simulated", False),
                context=contexts.get(v["id"], {}),
                runs=by_video[v["id"]],
            )
            for v in self.catalog.videos()
        ]
        return dict(
            settings=self.settings(),
            recordings=recordings,
            reports=self.reports(),
            schedules=self.schedules(),
            run_count=len(runs),
            analysed_recordings=len(by_video),
        )

    def preview(self, request):
        videos = {v["id"]: v for v in self.catalog.videos()}
        contexts = self.contexts()
        scoped = request["scope"] != "collection"
        start = timestamp(request["start"]) if request.get("start") else None
        end = timestamp(request["end"]) if request.get("end") else None
        if scoped and (not start or not end or end <= start):
            raise ValueError(
                "Select a valid report start and end with timezone offsets"
            )
        if scoped and end - start > timedelta(days=370):
            raise ValueError("Split reporting periods longer than one year")
        selected = set(request.get("video_ids") or videos.keys())
        if selected - videos.keys():
            raise KeyError("A selected recording no longer exists")
        selected = separate_simulated(selected, videos, request)
        simulated = any(videos[i]["source_profile"].get("simulated") for i in selected)
        all_runs = defaultdict(list)
        for run in self.runs():
            all_runs[run["video_id"]].append(run)
        items = []
        excluded = []
        totals = dict(
            tracks=0,
            pickups=0,
            peak_people=0,
            area_visits=0,
            pending=0,
            accepted=0,
            rejected=0,
            observed_seconds=0.0,
        )
        daily = defaultdict(lambda: dict(tracks=0, pickups=0, recordings=0))
        checks = Counter()
        doors = dict(entries=0, exits=0, configured=False)
        area_rows = []
        incidents = []
        sources = []
        period_intervals = defaultdict(list)
        for video_id in sorted(selected):
            v = videos[video_id]
            if request.get("store_id") and v["store_id"] != request["store_id"]:
                continue
            context = contexts.get(video_id, {})
            assigned = (
                timestamp(context["started_at_utc"])
                if context.get("started_at_utc")
                else None
            )
            if scoped and (
                not assigned or v["source_profile"]["source_kind"] == "sparse_clip"
            ):
                excluded.append(
                    dict(
                        video_id=video_id,
                        name=v["display_name"],
                        reason="Recording time is unknown"
                        if not assigned
                        else "Physical time is unknown for sparse footage",
                    )
                )
                continue
            if scoped and (
                assigned >= end
                or assigned
                + timedelta(
                    milliseconds=v["last_media_time_ms"] - v["first_media_time_ms"]
                )
                < start
            ):
                continue
            candidates = all_runs[video_id]
            if not candidates:
                excluded.append(
                    dict(
                        video_id=video_id,
                        name=v["display_name"],
                        reason="No completed analysis",
                    )
                )
                continue
            run, rows = self.best_run(candidates)
            saved = load_geometry(self.catalog, video_id)
            if scoped:
                low = (
                    v["first_media_time_ms"] + (start - assigned).total_seconds() * 1000
                )
                high = (
                    v["first_media_time_ms"] + (end - assigned).total_seconds() * 1000
                )
                rows = [r for r in rows if low <= r["media_ms"] < high]
                pickups = [
                    e
                    for e in run["pickups"]
                    if low <= e.get("detected_media_ms", e["end_media_ms"]) < high
                ]
            else:
                pickups = run["pickups"]
            if not rows:
                excluded.append(
                    dict(
                        video_id=video_id,
                        name=v["display_name"],
                        reason="No analysed samples in this period",
                    )
                )
                continue
            data = analyse(
                rows,
                saved["geometry"],
                pickups,
                self.reviews(run["key"]),
                rules_for(v, self.settings()),
                v["source_profile"]["source_kind"] != "sparse_clip",
            )
            summary = data["summary"]
            summary["pickup_scoring_available"] = run["kind"] != "track"
            if run["kind"] == "track":
                summary["pickups"] = None
            for k in totals:
                if k == "peak_people":
                    totals[k] = max(totals[k], summary[k])
                elif summary[k] is not None:
                    totals[k] += summary[k]
            checks.update(data["crosscheck"]["totals"])
            if data["footfall"]["configured"]:
                doors["configured"] = True
                doors["entries"] += data["footfall"]["entries"]
                doors["exits"] += data["footfall"]["exits"]
            for a in data["areas"]:
                area_rows.append(a | dict(video_id=video_id, camera_id=v["camera_id"]))
            for e in data["incidents"]:
                incidents.append(
                    e
                    | dict(
                        video_id=video_id, camera_id=v["camera_id"], run_key=run["key"]
                    )
                )
            # Union analysed intervals within each camera; no double-credit for overlapping clips.
            if assigned and summary["physical_time_supported"]:
                for a, b in zip(rows, rows[1:]):
                    if 0 < b["media_ms"] - a["media_ms"] <= 1000:
                        period_intervals[v["store_id"], v["camera_id"]].append(
                            (
                                assigned.timestamp()
                                + (a["media_ms"] - v["first_media_time_ms"]) / 1000,
                                assigned.timestamp()
                                + (b["media_ms"] - v["first_media_time_ms"]) / 1000,
                            )
                        )
            day_tracks = defaultdict(set)
            day_pickups = defaultdict(int)

            def sample_date(ms):
                return (
                    (assigned + timedelta(milliseconds=ms - v["first_media_time_ms"]))
                    .astimezone(zone(self.settings()["timezone"]))
                    .date()
                    .isoformat()
                    if assigned
                    else "Undated"
                )

            for row in rows:
                day = sample_date(row["media_ms"])
                day_tracks[day].update(
                    t["local_track_id"]
                    for t in row.get("tracks", [])
                    if t["position_status"] == "observed"
                )
            for event in pickups:
                day_pickups[
                    sample_date(event.get("detected_media_ms", event["end_media_ms"]))
                ] += 1
            for day, identities in day_tracks.items():
                daily[day]["tracks"] += len(identities)
                daily[day]["pickups"] += day_pickups[day]
                daily[day]["recordings"] += 1
            items.append(
                dict(
                    video_id=video_id,
                    name=context.get("label") or v["display_name"],
                    camera_id=v["camera_id"],
                    store_id=v["store_id"],
                    run_key=run["key"],
                    started_at_utc=context.get("started_at_utc"),
                    time_basis=context.get("time_basis", "unknown"),
                    summary=summary,
                    geometry_id=saved["id"],
                    queue=data["queue"],
                    line_counts=data["line_counts"],
                )
            )
            sources.append(
                dict(
                    video_id=video_id,
                    run_key=run["key"],
                    geometry_id=saved["id"],
                    geometry=saved["geometry"],
                    model_id=run.get("model_id"),
                    source_kind=v["source_profile"]["source_kind"],
                )
            )
        coverage = 0
        for intervals in period_intervals.values():
            merged = []
            for a, b in sorted(intervals):
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            coverage += sum(b - a for a, b in merged)
        totals["observed_seconds"] = round(totals["observed_seconds"], 2)
        totals["entries"] = doors["entries"] if doors["configured"] else None
        totals["exits"] = doors["exits"] if doors["configured"] else None
        if items and not any(r["summary"]["pickup_scoring_available"] for r in items):
            totals["pickups"] = None
            for day in daily.values():
                day["pickups"] = None
        if items and not any(r["summary"]["physical_time_supported"] for r in items):
            totals["observed_seconds"] = None
            totals["area_visits"] = None
        warnings = [
            "Track counts are summed camera-local IDs, not deduplicated customers.",
            "Pickup candidates require review and do not establish purchases, payment or theft.",
            "One completed run per recording is used; area metrics use the specified saved geometry.",
        ]
        unscored = sum(not r["summary"]["pickup_scoring_available"] for r in items)
        if unscored:
            warnings.append(
                f"{unscored} recordings have person tracking only. Their pickup scores are unavailable; totals include scored recordings only."
            )
        if not scoped:
            warnings.append(
                "Collection report: footage is selected explicitly and is not attributed to a calendar shift or month."
            )
        if scoped:
            warnings.append(
                "Period selection uses operator-assigned recording times; samples outside the selected interval are excluded."
            )
        if not items:
            warnings.append(
                "No analysed recordings match this scope. This is an empty coverage report, not evidence of zero store activity."
            )
        if simulated:
            warnings.insert(0, SIMULATED_WARNING)
        return dict(
            title=request["title"],
            lang=request.get("lang", "en"),
            simulated=simulated,
            crosscheck=dict(checks),
            scope=request["scope"],
            period=dict(
                start=request.get("start"),
                end=request.get("end"),
                timezone=self.settings()["timezone"],
            ),
            created_at=utcnow().isoformat(),
            notes=request.get("notes", ""),
            summary=totals,
            recordings=items,
            areas=area_rows,
            incidents=incidents,
            daily=[dict(date=k, **v) for k, v in sorted(daily.items())],
            excluded=excluded,
            sources=sources,
            warnings=warnings,
            dated_camera_coverage_seconds=round(coverage, 2),
            coverage_status="available" if items else "no_matching_recordings",
        )

    def best_run(self, candidates):
        """One run per recording: most coverage, then scored runs, then the latest."""
        ranked = []
        for candidate in candidates:
            run = self.run(candidate["key"])
            rows = self.observations(run)
            span = rows[-1]["media_ms"] - rows[0]["media_ms"] if len(rows) > 1 else 0
            ranked.append(
                (span, int(run["kind"] != "track"), candidate["created_at"], run, rows)
            )
        _, _, _, run, rows = max(ranked, key=lambda x: (x[0], x[1], x[2]))
        return run, rows

    def recording_days(self, video, context, candidates):
        """Day rollups for one real recording, cached until its inputs change."""
        settings = self.settings()
        saved = load_geometry(self.catalog, video["id"])
        rules = {
            k: settings[k]
            for k in ("timezone", "dwell_limit_s", "crowd_limit", "queue_limit")
        }
        key = stable_id(
            [
                ROLLUP_VERSION,
                sorted(c["key"] + c["created_at"] for c in candidates),
                saved["id"],
                context.get("started_at_utc"),
                rules,
            ]
        )
        with self.catalog.connect() as db:
            row = db.execute(
                "SELECT payload FROM ops_rollup_cache WHERE key=?", (key,)
            ).fetchone()
        if row:
            return json.loads(row[0])
        run, rows = self.best_run(candidates)
        physical = video["source_profile"]["source_kind"] != "sparse_clip"
        result = analyse(
            rows, saved["geometry"], run["pickups"], rules=settings, physical_time=physical
        )
        result["summary"]["pickup_scoring_available"] = run["kind"] != "track"
        started = (
            timestamp(context["started_at_utc"])
            if context.get("started_at_utc") and physical
            else None
        )
        days = list(
            day_rollups(
                result,
                rows,
                saved["geometry"],
                started,
                zone(settings["timezone"]),
                video["first_media_time_ms"],
                video["frame_dims_px"],
                basis="measured",
                camera_id=video["camera_id"],
            ).values()
        )
        with self.catalog.connect(write=True) as db:
            db.execute("DELETE FROM ops_rollup_cache WHERE video_id=?", (video["id"],))
            db.execute(
                "INSERT INTO ops_rollup_cache VALUES (?,?,?)",
                (key, video["id"], json.dumps(days)),
            )
        return days

    def stores(self):
        """Every store in the catalogue, plus whether its data is simulated."""
        contexts = self.contexts()
        analysed = defaultdict(list)
        for run in self.runs():
            analysed[run["video_id"]].append(run)
        rows = {}
        for video in self.catalog.videos():
            row = rows.setdefault(
                video["store_id"],
                dict(
                    id=video["store_id"],
                    simulated=False,
                    recordings=0,
                    analysed=0,
                    dated=0,
                    cameras=[],
                ),
            )
            row["recordings"] += 1
            row["analysed"] += bool(analysed[video["id"]])
            row["dated"] += bool(contexts.get(video["id"], {}).get("started_at_utc"))
            row["simulated"] = row["simulated"] or video["source_profile"].get("simulated", False)
            if video["camera_id"] not in row["cameras"]:
                row["cameras"].append(video["camera_id"])
        with self.catalog.connect() as db:
            for store, first, last, count in db.execute(
                "SELECT store_id,MIN(day),MAX(day),COUNT(*) FROM ops_simulated_days GROUP BY store_id"
            ):
                if store in rows:
                    rows[store].update(first_day=first, last_day=last, days=count)
        # A store with a calendar has the most to show, so it opens first.
        return sorted(
            rows.values(),
            key=lambda r: (not (r["dated"] or r.get("days")), not r["analysed"], r["id"]),
        )

    def store_days(self, store):
        """All day rollups for a store. Real and simulated data are never combined."""
        if store["simulated"]:
            with self.catalog.connect() as db:
                return [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT payload FROM ops_simulated_days WHERE store_id=? ORDER BY day",
                        (store["id"],),
                    )
                ]
        contexts = self.contexts()
        analysed = defaultdict(list)
        for run in self.runs():
            analysed[run["video_id"]].append(run)
        days = []
        for video in self.catalog.videos():
            if video["store_id"] == store["id"] and analysed[video["id"]]:
                days.extend(
                    self.recording_days(
                        video, contexts.get(video["id"], {}), analysed[video["id"]]
                    )
                )
        return sorted(days, key=lambda d: d["date"])

    def overview(self, store_id=None, start=None, end=None, camera_id=None):
        """The store view for one day or period, or for undated recordings."""
        stores = self.stores()
        store = next((s for s in stores if s["id"] == store_id), None)
        if store_id and store is None:
            raise KeyError("Store not found")
        store = store or (stores[0] if stores else None)
        if store is None:
            return dict(store=None, stores=[], mode="empty")
        settings = self.settings() | (SIMULATED_RULES if store["simulated"] else {})
        days = self.store_days(store)
        dated = sorted({d["date"] for d in days if d["date"] != "undated"})
        if dated:
            mode = "calendar"
            last = end or dated[-1]
            first = start or last
            if first > last:
                raise ValueError("The period must start before it ends")
            span = (
                datetime.fromisoformat(last) - datetime.fromisoformat(first)
            ).days + 1
            earlier_end = (datetime.fromisoformat(first) - timedelta(days=1)).date().isoformat()
            earlier_start = (datetime.fromisoformat(first) - timedelta(days=span)).date().isoformat()
            current = [d for d in days if first <= d["date"] <= last]
            previous = [d for d in days if earlier_start <= d["date"] <= earlier_end]
        else:
            mode, first, last, span = "collection", None, None, None
            current = days
            previous = []
        data = period_payload(current, previous, settings)
        cameras = [
            dict(
                video_id=v["id"],
                camera_id=v["camera_id"],
                name=v["display_name"],
                dimensions=v["frame_dims_px"],
            )
            for v in self.catalog.videos()
            if v["store_id"] == store["id"]
        ]
        camera = next((c for c in cameras if c["camera_id"] == camera_id), None) or (
            next(
                (c for c in cameras if any(d["camera_id"] == c["camera_id"] for d in current)),
                cameras[0] if cameras else None,
            )
        )
        if camera:
            own = [d for d in current if d["camera_id"] == camera["camera_id"]]
            if len(own) != len(current):
                data["heat"] = period_payload(own, [], settings)["heat"]
            camera["shapes"] = load_geometry(self.catalog, camera["video_id"])["geometry"]["shapes"]
            camera["frame_url"] = (
                "/api/ops/demo-store/floor.png"
                if store["simulated"]
                else f"/api/videos/{camera['video_id']}/frame/0"
            )
        recent = [
            d
            for d in days
            if mode == "calendar"
            and (datetime.fromisoformat(last) - timedelta(days=6)).date().isoformat() <= d["date"] <= last
        ]
        data["week_grid"] = period_payload(recent, [], settings)["grid"] if recent else []
        data.update(
            store=store,
            stores=stores,
            mode=mode,
            start=first,
            end=last,
            span_days=span,
            camera=camera,
            cameras=cameras,
            available_days=dated,
            basis="simulated" if store["simulated"] else "measured",
            settings={k: settings[k] for k in ("timezone", "queue_limit", "currency", "avg_item_value")},
        )
        return data

    def evidence_clip(self, key, identity, media_ms, before_ms=3000, after_ms=6000):
        """A short annotated clip around a moment; rendered once, then reused."""
        from app.evidence import render_clip

        data, rows = self.insight(key)
        video = self.catalog.video(data["video"]["id"])
        samples = [
            (r["media_ms"], t["box"], t["position_status"])
            for r in rows
            for t in r.get("tracks", [])
            if t["local_track_id"] == identity
        ]
        if not samples:
            raise KeyError("Track not found")
        start = max(video["first_media_time_ms"], media_ms - before_ms)
        end = min(video["last_media_time_ms"], media_ms + after_ms)
        digest = stable_id(["clip/1", video["media_sha256"], key, identity, start, end])
        folder = self.catalog.root / "clips"
        folder.mkdir(exist_ok=True)
        path = folder / f"{digest}.mp4"
        if not path.is_file():
            temporary = folder / f"{digest}.{uuid.uuid4().hex}.partial.mp4"
            try:
                render_clip(video, samples, identity, start, end, media_ms, temporary)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return path

    def demo_state(self):
        with self.catalog.connect() as db:
            days = db.execute("SELECT COUNT(*) FROM ops_simulated_days").fetchone()[0]
        state = dict(self.demo)
        if state["status"] == "idle":
            state["status"] = "ready" if days else "absent"
        return state | dict(days=days)

    def build_demo(self, days=14):
        """Build the simulated store in the background; the UI polls demo_state."""
        from app.demo_store import seed_store

        with self.lock:
            if self.demo["status"] == "building":
                return self.demo_state()
            self.demo = dict(status="building", progress=0.0, stage="starting", error=None)

        def progress(stage, fraction):
            self.demo.update(stage=stage, progress=round(fraction, 3))

        def work():
            try:
                seed_store(self, days=days, progress=progress)
                self.demo = dict(status="idle", progress=1.0, stage=None, error=None)
            except Exception as error:
                self.demo = dict(status="error", progress=0.0, stage=None, error=str(error))

        Thread(target=work, name="sentient-demo-store", daemon=True).start()
        return self.demo_state()

    def reports(self):
        with self.catalog.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM ops_reports ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def create_report(self, request, token=None):
        with self.lock:
            if token:
                with self.catalog.connect() as db:
                    r = db.execute(
                        "SELECT payload FROM ops_reports WHERE schedule_token=?",
                        (token,),
                    ).fetchone()
                if r:
                    return json.loads(r[0])
            report = self.preview(request)
            key = uuid.uuid4().hex
            folder = self.root / key
            temp = self.root / (key + ".tmp")
            temp.mkdir()
            report["id"] = key
            try:
                (temp / "report.json").write_text(json.dumps(report, indent=2))
                from app.report_documents import build_documents

                build_documents(report, temp)
                # The byte order mark lets Excel read Cyrillic area names.
                with (temp / "areas.csv").open("w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    cols = [
                        "camera_id",
                        "name",
                        "kind",
                        "unique_tracks",
                        "visits",
                        "observed_dwell_seconds",
                        "pickup_alerts",
                    ]
                    basis = "simulated" if report["simulated"] else "measured"
                    writer.writerow(cols + ["basis"])
                    for a in report["areas"]:
                        writer.writerow([safe_csv(a.get(c, "")) for c in cols] + [basis])
                temp.rename(folder)
                saved = dict(
                    id=key,
                    title=report["title"],
                    scope=report["scope"],
                    created_at=report["created_at"],
                    period=report["period"],
                    summary=report["summary"],
                    recordings=len(report["recordings"]),
                    coverage_status=report["coverage_status"],
                    simulated=report["simulated"],
                )
                with self.catalog.connect(write=True) as db:
                    db.execute(
                        "INSERT INTO ops_reports VALUES (?,?,?,?)",
                        (key, report["created_at"], canonical(saved), token),
                    )
                return saved
            except BaseException:
                shutil.rmtree(temp, ignore_errors=True)
                shutil.rmtree(folder, ignore_errors=True)
                raise

    def report_path(self, key, format):
        if format not in ("pdf", "docx", "json", "csv"):
            raise KeyError("Unknown report format")
        with self.catalog.connect() as db:
            r = db.execute("SELECT id FROM ops_reports WHERE id=?", (key,)).fetchone()
        if not r:
            raise KeyError("Report not found")
        return (
            self.root / key / ("areas.csv" if format == "csv" else "report." + format)
        )

    def schedules(self):
        with self.catalog.connect() as db:
            return [
                dict(id=r[0], **json.loads(r[1]))
                for r in db.execute("SELECT * FROM ops_schedules")
            ]

    def save_schedule(self, value):
        zone(value["timezone"])
        clock(value["shift_start"])
        clock(value["shift_end"])
        if value["shift_start"] == value["shift_end"]:
            raise ValueError("Shift start and end must differ")
        key = uuid.uuid4().hex
        value.update(
            next_due=next_due(value, utcnow()).isoformat(),
            last_report=None,
            last_error=None,
        )
        with self.catalog.connect(write=True) as db:
            db.execute(
                "INSERT INTO ops_schedules VALUES (?,?)", (key, canonical(value))
            )
        return dict(id=key, **value)

    def tick(self, now=None):
        now = now or utcnow()
        for value in self.schedules():
            if (
                not value["enabled"]
                or timestamp(value["next_due"]) > now
                or (value.get("retry_after") and timestamp(value["retry_after"]) > now)
            ):
                continue
            key = value.pop("id")
            end = timestamp(value["next_due"])
            start = period_start(value, end)
            try:
                request = ReportRequest(
                    title=value["name"],
                    scope="month"
                    if value["cadence"] == "monthly"
                    else "shift"
                    if value["cadence"] == "shift"
                    else "day",
                    start=start.isoformat(),
                    end=end.isoformat(),
                    store_id=value.get("store_id"),
                ).model_dump()
                report = self.create_report(request, token=key + ":" + end.isoformat())
                value["last_report"] = report["id"]
                value["last_error"] = None
                value["retry_after"] = None
                value["next_due"] = next_due(
                    value, end + timedelta(milliseconds=1)
                ).isoformat()
            except Exception as e:
                value["last_error"] = str(e)
                value["retry_after"] = (now + timedelta(minutes=5)).isoformat()
            with self.catalog.connect(write=True) as db:
                db.execute(
                    "UPDATE ops_schedules SET payload=? WHERE id=?",
                    (canonical(value), key),
                )

    def start(self):
        def work():
            while not self.stop.wait(15):
                try:
                    self.tick()
                except Exception:
                    pass

        self.thread = Thread(target=work, name="sentient-reports", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=15)


def rules_for(video, settings):
    """A simulated floor is busier than the clips the default limits were set on."""
    return settings | SIMULATED_RULES if video["source_profile"].get("simulated") else settings


def separate_simulated(selected, videos, request):
    """Simulated and real recordings never share a report."""
    store = request.get("store_id")
    chosen = {i for i in selected if not store or videos[i]["store_id"] == store}
    fake = {i for i in chosen if videos[i]["source_profile"].get("simulated")}
    if not fake or fake == chosen:
        return chosen
    # An explicit choice is a mistake to report; an open scope, like a schedule, means the real shop.
    if request.get("video_ids"):
        raise ValueError("Report simulated and real recordings separately.")
    return chosen - fake


def safe_csv(value):
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def next_due(value, now):
    tz = zone(value["timezone"])
    local = now.astimezone(tz)
    if value["cadence"] == "monthly":
        return local.replace(
            year=local.year + (local.month == 12),
            month=local.month % 12 + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ).astimezone(UTC)
    h, m = clock(value["shift_end"] if value["cadence"] == "shift" else "00:00")
    due = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if due <= local:
        due += timedelta(days=1)
    return due.astimezone(UTC)


def period_start(value, end):
    local = end.astimezone(zone(value["timezone"]))
    if value["cadence"] == "monthly":
        return local.replace(
            year=local.year - (local.month == 1),
            month=(local.month - 2) % 12 + 1,
            day=1,
        ).astimezone(UTC)
    if value["cadence"] == "daily":
        return (local - timedelta(days=1)).astimezone(UTC)
    h, m = clock(value["shift_start"])
    start = local.replace(hour=h, minute=m)
    if start >= local:
        start -= timedelta(days=1)
    return start.astimezone(UTC)


def router(ops):
    r = APIRouter(prefix="/api/ops")

    @r.get("/workspace")
    def workspace():
        return ops.workspace()

    @r.get("/settings")
    def settings():
        return ops.settings()

    @r.put("/settings")
    def settings_save(body: Settings):
        return ops.save_settings(body.model_dump())

    @r.put("/recordings/{video_id}")
    def recording_save(video_id: str, body: RecordingContext):
        return ops.save_context(video_id, body.model_dump())

    @r.get("/stores")
    def stores():
        return ops.stores()

    @r.get("/overview")
    def overview(
        store_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        camera_id: str | None = None,
    ):
        return ops.overview(store_id, start, end, camera_id)

    @r.get("/z-report.pdf")
    def z_report(
        store_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        lang: str = Query("en", pattern="^(en|ru)$"),
    ):
        from app.report_documents import build_z_report

        data = ops.overview(store_id, start, end)
        if data["mode"] == "empty":
            raise KeyError("No store to report on")
        buffer = io.BytesIO()
        build_z_report(data, lang, buffer)
        name = "sentient-z-report-" + (data.get("end") or "recordings") + ".pdf"
        return Response(
            buffer.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @r.get("/demo-store")
    def demo_state():
        return ops.demo_state()

    @r.post("/demo-store")
    def demo_build():
        return ops.build_demo()

    @r.get("/demo-store/floor.png")
    def demo_floor():
        path = ops.catalog.root / "simulation/floor.png"
        if not path.is_file():
            raise KeyError("The demo store has not been built")
        return FileResponse(path, media_type="image/png")

    @r.get("/runs")
    def runs(video_id: str | None = None):
        return ops.runs(video_id)

    @r.get("/runs/{key}/insights")
    def insights(key: str):
        return ops.insight(key)[0]

    @r.get("/runs/{key}/tracks/{identity}")
    def trajectory(key: str, identity: str):
        data, rows = ops.insight(key)
        track = next((t for t in data["tracks"] if t["id"] == identity), None)
        if not track:
            raise KeyError("Track not found")
        samples = [
            dict(frame_index=r["frame_index"], media_ms=r["media_ms"], **t)
            for r in rows
            for t in r.get("tracks", [])
            if t["local_track_id"] == identity
        ]
        return dict(
            track=track,
            samples=samples,
            visits=[v for v in data["visits"] if v["track_id"] == identity],
            incidents=[e for e in data["incidents"] if e["track_id"] == identity],
            journey=track_journey(identity, data),
        )

    @r.get("/runs/{key}/tracks/{identity}/clip")
    def evidence_clip(
        key: str, identity: str, media_ms: float = Query(0, allow_inf_nan=False)
    ):
        return FileResponse(
            ops.evidence_clip(key, identity, media_ms),
            media_type="video/mp4",
            filename="sentient-evidence.mp4",
        )

    @r.get("/runs/{key}/tracks/{identity}/evidence")
    def evidence_image(
        key: str,
        identity: str,
        media_ms: float = Query(0, allow_inf_nan=False),
        crop: bool = False,
    ):
        from PIL import Image, ImageDraw
        from app.p1.media import frame_png

        data, rows = ops.insight(key)
        candidates = [
            (r, t)
            for r in rows
            for t in r.get("tracks", [])
            if t["local_track_id"] == identity and t["position_status"] == "observed"
        ]
        if not candidates:
            raise KeyError("No observed image for this track")
        row, track = min(
            candidates, key=lambda pair: abs(pair[0]["media_ms"] - media_ms)
        )
        video = ops.catalog.video(data["video"]["id"])
        png, _ = frame_png(ops.catalog, video, row["frame_index"])
        image = Image.open(io.BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle(track["box"], outline="#FFD21F", width=3)
        if crop:
            # Thumbnails show the person, not a whole store at 116 pixels wide.
            x1, y1, x2, y2 = track["box"]
            side = max(x2 - x1, y2 - y1) * 2.4
            width = side * 16 / 9
            left = min(max(0, (x1 + x2) / 2 - width / 2), max(0, image.width - width))
            top = min(max(0, (y1 + y2) / 2 - side / 2), max(0, image.height - side))
            image = image.crop((int(left), int(top), int(left + width), int(top + side)))
        else:
            caption = f"{identity} | {row['media_ms'] / 1000:.2f}s | observed frame {row['frame_index']}"
            draw.rectangle((0, 0, image.width, 28), fill="#0C1424")
            draw.text((10, 8), caption, fill="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Response(
            buffer.getvalue(),
            media_type="image/png",
            headers={
                "Content-Disposition": 'attachment; filename="sentient-track-evidence.png"'
            },
        )

    @r.put("/runs/{key}/reviews/{event_id:path}")
    def review(key: str, event_id: str, body: Review):
        return ops.review(key, event_id, body.model_dump())

    @r.post("/reports/preview")
    def preview(body: ReportRequest):
        return ops.preview(body.model_dump())

    @r.post("/reports")
    def report(body: ReportRequest):
        return ops.create_report(body.model_dump())

    @r.get("/reports")
    def reports():
        return ops.reports()

    @r.get("/review-dataset")
    def review_dataset():
        with ops.catalog.connect() as db:
            rows = [
                dict(run_key=r[0], event_id=r[1], review=json.loads(r[2]))
                for r in db.execute("SELECT * FROM ops_reviews")
            ]
        return Response(
            json.dumps(
                dict(
                    format="sentient.operator-reviews/1",
                    purpose="Operator annotations for evaluation; not independently verified accuracy",
                    reviews=rows,
                ),
                indent=2,
            ),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="sentient-review-dataset.json"'
            },
        )

    @r.get("/reports/{key}/{format}")
    def download(key: str, format: str):
        return FileResponse(
            ops.report_path(key, format), filename="sentient-" + key[:8] + "." + format
        )

    @r.post("/schedules")
    def schedule(body: ScheduleRequest):
        return ops.save_schedule(body.model_dump())

    @r.delete("/schedules/{key}")
    def delete_schedule(key: str):
        with ops.catalog.connect(write=True) as db:
            if not db.execute("DELETE FROM ops_schedules WHERE id=?", (key,)).rowcount:
                raise KeyError("Schedule not found")
        return dict(deleted=True)

    @r.get("/health")
    def health():
        import psutil

        gpu = None
        try:
            p = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if p.returncode == 0:
                name, used, total, util = [
                    s.strip() for s in p.stdout.splitlines()[0].split(",")
                ]
                gpu = dict(
                    name=name,
                    memory_used_mb=int(used),
                    memory_total_mb=int(total),
                    utilization=int(util),
                )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        model = ops.actions.readiness()
        memory = psutil.virtual_memory()
        manifest = {}
        if ops.actions.model and (ops.actions.model / "manifest.json").is_file():
            manifest = json.loads((ops.actions.model / "manifest.json").read_text())
        return dict(
            cpu_percent=psutil.cpu_percent(interval=0.05),
            ram_used_gb=round(memory.used / 1024**3, 1),
            ram_total_gb=round(memory.total / 1024**3, 1),
            gpu=gpu,
            action_model=model,
            model_id=manifest.get("model_id"),
            pickup_policy=manifest.get("pickup_policy"),
            detector_cached=ops.live.detector is not None,
            action_cached=ops.live.runtime is not None,
            scheduler_running=bool(ops.thread and ops.thread.is_alive()),
            data_root=str(ops.catalog.root),
            limitations=[
                "Camera-local tracking; no appearance re-identification",
                "Pickup transfer accuracy is unmeasured on continuous store footage",
                "Recording replay; no connected RTSP ingestion",
                "No payment, product inventory or theft verification",
            ],
        )

    return r
