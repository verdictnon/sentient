"""P0 replay/evidence integration and independent, versioned human annotations."""

import json
import hashlib
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from app.contracts import ContractValidator
from app.p1.geometry import load_geometry
from app.p1.source_profile import source_profile, interpret_summary
from app.replay import replay
from app.storage import SQLiteStore, canonical, processing_time, stable_id

ROOT = Path(__file__).resolve().parents[2]
LABELS = {"take", "put", "touch", "background", "unknown", "inward", "outward", "zone_visit",
          "queue_abandoned", "service_completed", "other"}


def run_analysis(catalog, input_path, video_id=None, geometry_id=None, isolated_run=False, source_kind=None, analytics_config_override=None):
    video = catalog.video(video_id) if video_id else None
    geometry = load_geometry(catalog, video_id, geometry_id) if video else None
    config = geometry["p0_config"] if geometry else json.loads((ROOT / "configs/app.example.json").read_text())
    if analytics_config_override is not None:
        config=json.loads(canonical(analytics_config_override))
    if video:
        wanted = (video["store_id"], video["camera_id"], video["stream_session_id"])
        records = ContractValidator().read_jsonl(input_path)
        matched = False
        for _, row in records:
            if (row["store_id"], row["camera_id"], row["stream_session_id"]) != wanted:
                continue
            matched = True
            if row["type"] == "track.observed":
                if row["frame_dims_px"] != video["frame_dims_px"]:
                    raise ValueError("Tracking dimensions do not match the selected video; import records independently instead")
                frame = catalog.frame(video, row["frame_index"])
                if not math.isclose(frame["media_ms"], row["media_time_ms"], abs_tol=1e-5, rel_tol=0):
                    raise ValueError("Tracking frame PTS does not match the selected video; no automatic FPS/time alignment is assumed")
        if not matched:
            raise ValueError("No records match the selected store/camera/session; import independently or select the correct video")
    scope = [video_id, geometry["id"] if geometry else None, config]
    if isolated_run:
        # Explicit offline snapshots may reuse a retired camera/session with a
        # different detector/model. Keep legacy append behavior as the default.
        digest = hashlib.sha256()
        with Path(input_path).open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
        scope.append({'mode':'isolated-jsonl/1','input_sha256':digest.hexdigest()})
    analysis_id = stable_id(scope)
    config_path = catalog.root / "analyses" / f"{analysis_id}.config.json"
    config_path.write_text(canonical(config), encoding="utf-8")
    db_path = catalog.root / "analyses" / f"{analysis_id}.sqlite"
    summary = replay(input_path, config_path, db_path)
    if video:
        summary=interpret_summary(summary, source_kind or source_profile(video)['source_kind'])
    with catalog.connect(write=True) as db:
        db.execute("INSERT INTO analyses VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) "
                   "DO UPDATE SET summary=excluded.summary",
                   (analysis_id, video_id, geometry["id"] if geometry else None, str(db_path),
                    canonical(config), canonical(summary), processing_time()))
    return catalog.analysis(analysis_id)


def evidence(catalog, analysis_id):
    analysis = catalog.analysis(analysis_id)
    if not Path(analysis["path"]).is_file():
        raise FileNotFoundError("Analytics database is missing; restore it or replay the original records")
    video = catalog.video(analysis["video_id"]) if analysis["video_id"] else None
    source = [video["store_id"], video["camera_id"], video["stream_session_id"]] if video else None
    with SQLiteStore(analysis["path"]) as store:
        events, inputs = store.events(), store.inputs()
    if source:
        events = [event for event in events if event["track_key"][:3] == source]
        inputs = [row for row in inputs if [row["store"], row["camera"], row["session"]] == source]
    rows = []
    for event in events:
        payload = event["payload"]
        rows.append({"target_id": "event:" + event["event_id"], "kind": event["kind"],
                     "media_time_ms": event["media_time_ms"],
                     "start_media_ms": payload.get("start_ms", event["media_time_ms"]),
                     "end_media_ms": payload.get("end_ms", event["media_time_ms"]),
                     "track_key": event["track_key"], "payload": payload,
                     "quality": "censored" if payload.get("complete") is False else "observed"})
    for row in inputs:
        p = row["payload"]
        if row["kind"] == "action.scored":
            rows.append({"target_id": "record:" + p["record_id"], "kind": "action.scored",
                         "media_time_ms": p["media_time_ms"], "start_media_ms": p["start_media_ms"],
                         "end_media_ms": p["end_media_ms"],
                         "track_key": [p[k] for k in ("store_id", "camera_id", "stream_session_id", "local_track_id")],
                         "payload": p, "quality": p["quality"]})
    diagnostic_events=[]
    if analysis['summary'].get('physical_analytics_supported') is False:
        diagnostic_events=[row for row in rows if row['kind']!='action.scored']
        rows=[row for row in rows if row['kind']=='action.scored']
    with catalog.connect() as db:
        reviewed = db.execute("SELECT target_id,payload FROM annotations WHERE analysis_id=? ORDER BY revision", (analysis_id,)).fetchall()
    latest = {r["target_id"]: json.loads(r["payload"]) for r in reviewed}
    for row in rows:
        row["review"] = latest.get(row["target_id"])
        if video:
            row["evidence_start_ms"] = max(video["first_media_time_ms"], row["start_media_ms"] - 1000)
            row["evidence_end_ms"] = min(video["last_media_time_ms"], row["end_media_ms"] + 1000)
    return {"analysis": analysis, "diagnostic_geometry_events": diagnostic_events, "events": sorted(rows, key=lambda r: (r["media_time_ms"], r["target_id"])),
            "health": [r["payload"] for r in inputs if r["kind"] == "health"],
            "clock_statuses": sorted({r["payload"]["clock_status"] for r in inputs}),
            "capture_utc_known": any(r["capture_time_utc"] is not None for r in inputs)}


def review_history(catalog, analysis_id, target_id):
    with catalog.connect() as db:
        rows = db.execute("SELECT payload FROM annotations WHERE analysis_id=? AND target_id=? ORDER BY revision",
                          (analysis_id, target_id)).fetchall()
    return [json.loads(row[0]) for row in rows]


def save_review(catalog, video_id, body):
    video = catalog.video(video_id)
    analysis = catalog.analysis(body["analysis_id"])
    if analysis["video_id"] != video_id:
        raise ValueError("The selected analysis is not bound to this video")
    rows = evidence(catalog, analysis["id"])["events"]
    target = next((r for r in rows if r["target_id"] == body["target_id"]), None)
    if target is None:
        raise KeyError("Review target not found in this source/session")
    if body["label"] not in LABELS:
        raise ValueError("Unknown human annotation label")
    start, end = body["start_media_ms"], body["end_media_ms"]
    if not video["first_media_time_ms"] <= start <= end <= video["last_media_time_ms"]:
        raise ValueError("Annotation span must lie within indexed source PTS")
    with catalog.connect(write=True) as db:
        old = db.execute("SELECT * FROM annotations WHERE request_id=?", (body["request_id"],)).fetchone()
        if old is not None:
            if old["request_payload"] != canonical(body):
                raise RuntimeError("Review request ID conflicts with a previous payload")
            return json.loads(old["payload"])
        revision = db.execute("SELECT COALESCE(MAX(revision),0) FROM annotations "
                              "WHERE video_id=? AND analysis_id=? AND target_id=?",
                              (video_id, analysis["id"], body["target_id"])).fetchone()[0]
        if body["expected_revision"] != revision:
            raise RuntimeError("Review changed since it was loaded; reload the latest revision")
        frames = [dict(row) for row in db.execute("SELECT frame_index,pts,tb_num,tb_den,media_ms FROM frames "
                                                 "WHERE sha=? AND media_ms BETWEEN ? AND ? ORDER BY frame_index",
                                                 (video["media_sha256"], start, end))]
        if not frames:
            raise ValueError("The annotation span contains no indexed frame; select actual frame times")
        with SQLiteStore(analysis["path"]) as source_db:
            source_rows = source_db.inputs()
        key = target["track_key"]
        relevant = [r["payload"] for r in source_rows if [r["store"], r["camera"], r["session"], r["track"]] == key
                    and (start <= r["media_time_ms"] <= end or
                         (target["kind"] == "action.scored" and r["payload"].get("candidate_id") == target["payload"]["candidate_id"]))]
        identity = stable_id([video_id, analysis["id"], body["target_id"], revision + 1])
        result = {
            "annotation_schema_version": "sentient.review/1", "annotation_id": identity,
            "revision": revision + 1, "created_at_utc": processing_time(),
            "video_id": video_id, "media_sha256": video["media_sha256"],
            "store_id": key[0], "camera_id": key[1], "stream_session_id": key[2],
            "local_track_id": key[3], "global_visit_id": next((r.get("global_visit_id") for r in relevant if "global_visit_id" in r), None),
            "subject_track_key": key,
            "collection_session": video["collection_session"], "collection_day": video["collection_day"],
            "capture_time_utc": None, "clock_status": video["clock_status"],
            "start_media_ms": start, "end_media_ms": end,
            "frame_indices": [f["frame_index"] for f in frames],
            "original_media_times_ms": [f["media_ms"] for f in frames],
            "source_frame_pts": [{"frame_index": f["frame_index"], "pts": f["pts"],
                                  "time_base": [f["tb_num"], f["tb_den"]]} for f in frames],
            "human": {k: body[k] for k in ("reviewer", "decision", "label", "visibility", "uncertainty", "notes")},
            "model_outputs": [r for r in relevant if r["type"] == "action.scored"],
            "source_contract_version": "1.0", "source_records": relevant,
            "provenance": {"analysis_id": analysis["id"], "geometry_id": analysis["geometry_id"],
                           "target_id": body["target_id"], "source_kind": "human_review",
                           "media_manifest": f"{video_id}.json"},
        }
        schema = json.loads((Path(__file__).parent / "annotation.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(result)
        db.execute("INSERT INTO annotations VALUES (?,?,?,?,?,?,?,?)",
                   (identity, video_id, analysis["id"], body["target_id"], revision + 1,
                    body["request_id"], canonical(body), canonical(result)))
    return result


def export_annotations(catalog, video_id=None, latest_only=False):
    # Cursor-backed export: one JSONL review record at a time.
    with catalog.connect() as db:
        query = "SELECT a.payload FROM annotations a WHERE 1=1"
        args = []
        if video_id:
            catalog.video(video_id)
            query += " AND a.video_id=?"
            args.append(video_id)
        if latest_only:
            query += (" AND a.revision=(SELECT MAX(b.revision) FROM annotations b WHERE "
                      "b.video_id=a.video_id AND b.analysis_id=a.analysis_id AND b.target_id=a.target_id)")
        query += " ORDER BY a.video_id,a.analysis_id,a.target_id,a.revision"
        for row in db.execute(query, args):
            yield row[0] + "\n"
