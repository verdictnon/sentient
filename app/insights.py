"""Camera-local analytics over sampled observations; no identities or sales inferred."""

from collections import Counter, defaultdict
from math import hypot
from statistics import median

from app.geometry import Line, Polygon
from app.journeys import (
    CROSSCHECK_PRIORITY,
    cross_check,
    cross_check_totals,
    footfall,
    funnel,
    queue_outcomes,
    routes,
)
from app.shelves import ShelfStats


def analyse(rows, geometry, pickups=(), reviews=None, rules=None, physical_time=True):
    reviews = reviews or {}
    rules = rules or {}
    stats = ShelfStats(geometry, physical_time)
    shapes = {s["id"]: s for s in geometry["shapes"] if s["kind"] != "line"}
    polygons = {k: Polygon(tuple(map(tuple, s["points"]))) for k, s in shapes.items()}
    lines = {
        s["id"]: (s, Line(*map(tuple, s["points"]), s.get("hysteresis_px", 5)))
        for s in geometry["shapes"]
        if s["kind"] == "line"
    }
    tracks = {}
    previous = {}
    visits = []
    open_visits = {}
    open_areas = defaultdict(set)
    transitions = Counter()
    crossings = []
    sides = {}
    timeline = []
    queue_waits = []
    queue_state = {}
    crowds = {}
    incidents = []
    total_samples = 0
    observed_samples = 0
    coverage = 0
    last_ms = None
    pickup_map = defaultdict(list)
    for e in pickups:
        pickup_map[e.get("detected_media_ms", e["end_media_ms"])].append(e)

    def close_visit(key, end, reason):
        v = open_visits.pop(key)
        open_areas[key[0]].discard(key[1])
        v["end_ms"] = end
        v["end_observed"] = reason == "left"
        v["reason"] = reason
        visits.append(v)

    for row in rows:
        ms = row["media_ms"]
        frame = row["frame_index"]
        observed = [
            t for t in row.get("tracks", []) if t["position_status"] == "observed"
        ]
        if last_ms is not None and 0 < ms - last_ms <= 1000:
            coverage += (ms - last_ms) / 1000
        elif last_ms is not None:
            crowds.clear()
        last_ms = ms
        inside_by_track = {}
        for t in observed:
            box = t["box"]
            point = ((box[0] + box[2]) / 2, box[3])
            inside_by_track[t["local_track_id"]] = {
                k for k, p in polygons.items() if p.contains(point)
            }
        stats.update(ms, observed, inside_by_track)
        area_ids = defaultdict(set)
        for t in row.get("tracks", []):
            identity = t["local_track_id"]
            box = t["box"]
            total_samples += 1
            track = tracks.setdefault(
                identity,
                dict(
                    id=identity,
                    first_ms=ms,
                    last_ms=ms,
                    observed_seconds=0.0,
                    observed_samples=0,
                    predicted_samples=0,
                    path_pixels=0.0,
                    segments=0,
                    area_ids=set(),
                    pickups=0,
                ),
            )
            if t["position_status"] != "observed":
                track["predicted_samples"] += 1
                continue
            if not track["observed_samples"]:
                track["first_ms"] = ms
            observed_samples += 1
            track["observed_samples"] += 1
            track["last_ms"] = ms
            point = ((box[0] + box[2]) / 2, box[3])
            inside = inside_by_track[identity]
            track["area_ids"].update(inside)
            old = previous.get(identity)
            contiguous = old is not None and 0 < ms - old[0] <= 1000
            if contiguous:
                if physical_time:
                    track["observed_seconds"] += (ms - old[0]) / 1000
                track["path_pixels"] += hypot(
                    point[0] - old[2][0], point[1] - old[2][1]
                )
                if physical_time:
                    for a in old[1] - inside:
                        for b in inside - old[1]:
                            transitions[a, b] += 1
            else:
                track["segments"] += 1
            for k in inside:
                area_ids[k].add(identity)
                key = (identity, k)
                if key not in open_visits or not contiguous:
                    if key in open_visits:
                        close_visit(key, open_visits[key]["last_ms"], "gap")
                    open_visits[key] = dict(
                        track_id=identity,
                        area_id=k,
                        name=shapes[k]["name"],
                        kind=shapes[k]["kind"],
                        start_ms=ms,
                        last_ms=ms,
                        observed_seconds=0.0,
                        entry_observed=contiguous,
                        end_observed=False,
                    )
                    open_areas[identity].add(k)
                v = open_visits[key]
                if contiguous and k in old[1] and physical_time:
                    v["observed_seconds"] += (ms - old[0]) / 1000
                v["last_ms"] = ms
            for area in [k for k in open_areas[identity] if k not in inside]:
                key = (identity, area)
                close_visit(
                    key,
                    ms if contiguous else open_visits[key]["last_ms"],
                    "left" if contiguous else "gap",
                )
            if physical_time:
                for k, (shape, line) in lines.items():
                    side = line.side(point)
                    prior = sides.get((identity, k))
                    if side:
                        if (
                            prior
                            and 0 < ms - prior[0] <= 1000
                            and side != prior[1]
                            and line.crossed_segment(prior[2], point)
                        ):
                            crossings.append(
                                dict(
                                    id=f"cross:{k}:{identity}:{frame}",
                                    kind="crossing",
                                    track_id=identity,
                                    area_id=k,
                                    name=shape["name"],
                                    media_ms=ms,
                                    direction="positive" if side > 0 else "negative",
                                )
                            )
                        sides[identity, k] = (ms, side, point)
                paired = bool(
                    geometry["active"].get("queue")
                    and geometry["active"].get("checkout")
                )
                waiting = paired and geometry["active"]["queue"] in inside
                service = paired and geometry["active"]["checkout"] in inside
                if not contiguous:
                    queue_state.pop(identity, None)
                if waiting and not service and identity not in queue_state:
                    queue_state[identity] = (ms, contiguous)
                if service and identity in queue_state:
                    start, entry = queue_state.pop(identity)
                    queue_waits.append(
                        dict(
                            track_id=identity,
                            start_ms=start,
                            end_ms=ms,
                            observed_wait_seconds=(ms - start) / 1000,
                            complete_entry=entry,
                        )
                    )
                elif not waiting:
                    queue_state.pop(identity, None)
            previous[identity] = (ms, inside, point)
            for event in pickup_map.get(ms, []):
                if event["local_track_id"] == identity:
                    stats.pickup(dict(event), t)
        # Expire missing observations; predicted boxes never extend area dwell.
        for key in list(open_visits):
            if ms - open_visits[key]["last_ms"] > 1000:
                close_visit(key, open_visits[key]["last_ms"], "gap")
        timeline.append(
            dict(
                media_ms=ms,
                frame_index=frame,
                people=len(observed),
                areas={k: len(area_ids[k]) for k in shapes},
                queue=sum(
                    len(area_ids[k]) for k in shapes if shapes[k]["kind"] == "queue"
                ),
            )
        )
        if physical_time:
            for k, s in shapes.items():
                limit = (
                    rules.get("queue_limit", 3)
                    if s["kind"] == "queue"
                    else rules.get("crowd_limit", 4)
                )
                above = len(area_ids[k]) >= limit
                if above and k not in crowds:
                    kind = "queue" if s["kind"] == "queue" else "crowding"
                    crowds[k] = dict(
                        id=f"{kind}:{k}:{frame}",
                        kind=kind,
                        media_ms=ms,
                        end_ms=ms,
                        area_id=k,
                        name=s["name"],
                        count=len(area_ids[k]),
                        track_id=None,
                    )
                    incidents.append(crowds[k])
                elif above:
                    crowds[k]["end_ms"] = ms
                    crowds[k]["count"] = max(crowds[k]["count"], len(area_ids[k]))
                else:
                    crowds.pop(k, None)
    for key in list(open_visits):
        close_visit(key, open_visits[key]["last_ms"], "end_of_run")
    for v in visits:
        v["observed_seconds"] = (
            round(v["observed_seconds"], 2) if physical_time else None
        )
        # Standing in the queue or at the till is what those areas are for.
        if (
            physical_time
            and v["kind"] not in ("queue", "checkout")
            and v["observed_seconds"] >= rules.get("dwell_limit_s", 20)
        ):
            incidents.append(
                dict(
                    id=f"dwell:{v['track_id']}:{v['area_id']}:{v['start_ms']}",
                    kind="dwell",
                    media_ms=v["start_ms"],
                    end_ms=v["end_ms"],
                    area_id=v["area_id"],
                    name=v["name"],
                    track_id=v["track_id"],
                    seconds=v["observed_seconds"],
                )
            )
    for event in pickups:
        identity = event["local_track_id"]
        if identity in tracks:
            tracks[identity]["pickups"] += 1
        incidents.append(
            dict(
                id=event["id"],
                kind="pickup",
                track_id=identity,
                media_ms=event.get("detected_media_ms", event["start_media_ms"]),
                start_ms=event["start_media_ms"],
                end_ms=event["end_media_ms"],
                score=event["score"],
                name="Pickup candidate",
            )
        )
    output_tracks = []
    for t in tracks.values():
        if not t["observed_samples"]:
            continue
        t["area_ids"] = sorted(t["area_ids"])
        t["path_pixels"] = round(t["path_pixels"], 1) if physical_time else None
        t["observed_seconds"] = (
            round(t["observed_seconds"], 2) if physical_time else None
        )
        t["observed_fraction"] = round(
            t["observed_samples"]
            / max(1, t["observed_samples"] + t["predicted_samples"]),
            3,
        )
        output_tracks.append(t)
    run_end_ms = rows[-1]["media_ms"] if rows else 0
    crosscheck = cross_check(
        pickups, output_tracks, visits, crossings, geometry, run_end_ms, physical_time
    )
    for e in incidents:
        e["review"] = reviews.get(e["id"], dict(status="pending", notes="", revision=0))
        if e["kind"] == "pickup":
            e["crosscheck"] = crosscheck[e["id"]]
            e["priority"] = CROSSCHECK_PRIORITY[crosscheck[e["id"]]["status"]]
    counts = Counter(e["review"]["status"] for e in incidents)
    snapshot = stats.snapshot()
    for area in snapshot["areas"]:
        area["dwell_per_visit_seconds"] = (
            round(area["observed_dwell_seconds"] / area["visits"], 1)
            if physical_time and area["visits"]
            else None
        )
        area["pickup_rate"] = (
            round(area["pickup_alerts"] / area["visits"], 3) if area["visits"] else None
        )
    doors = footfall(crossings, geometry) if physical_time else dict(
        configured=False, entries=None, exits=None, lines=0, method="Elapsed time is unknown for this footage."
    )
    journey_routes = (
        routes(visits) if physical_time else dict(tracks=0, routes=[], flows=[])
    )
    return dict(
        summary=dict(
            tracks=len(output_tracks),
            pickups=len(pickups),
            peak_people=max((r["people"] for r in timeline), default=0),
            observed_seconds=round(coverage, 2) if physical_time else None,
            observed_fraction=round(observed_samples / max(1, total_samples), 3),
            area_visits=sum(a["visits"] for a in snapshot["areas"])
            if physical_time and polygons
            else None,
            entries=doors["entries"],
            exits=doors["exits"],
            needs_review=cross_check_totals(crosscheck)["needs_review"],
            pending=counts["pending"],
            accepted=counts["accepted"],
            rejected=counts["rejected"],
            physical_time_supported=physical_time,
        ),
        areas=[
            a if physical_time else a | {"visits": None} for a in snapshot["areas"]
        ],
        line_counts=snapshot["crossings"],
        footfall=doors,
        routes=journey_routes,
        funnel=funnel(output_tracks, visits, pickups, crossings, geometry),
        crosscheck=dict(
            totals=cross_check_totals(crosscheck), events=list(crosscheck.values())
        ),
        tracks=sorted(output_tracks, key=lambda t: t["first_ms"]),
        visits=visits,
        crossings=crossings,
        transitions=[
            dict(
                from_id=a,
                to_id=b,
                from_name=shapes[a]["name"],
                to_name=shapes[b]["name"],
                count=n,
            )
            for (a, b), n in transitions.most_common()
        ],
        timeline=timeline,
        incidents=sorted(incidents, key=lambda e: e["media_ms"]),
        queue=dict(
            observed_waits=queue_waits,
            median_complete_wait_seconds=median(
                [w["observed_wait_seconds"] for w in queue_waits if w["complete_entry"]]
            )
            if any(w["complete_entry"] for w in queue_waits)
            else None,
            peak_people=max((r["queue"] for r in timeline), default=0),
            longest_wait_seconds=max(
                (w["observed_wait_seconds"] for w in queue_waits), default=None
            ),
            limit=rules.get("queue_limit", 3),
            **queue_outcomes(
                visits, timeline, rules.get("queue_limit", 3), physical_time
            ),
            method="Observed transition from waiting area to checkout area; payment and actual service are not inferred.",
        ),
        methods=dict(
            identity="camera-local IDs; not verified unique customers",
            dwell="Observed footpoints only; gaps over one second are not credited",
            distance="Image pixels; no physical calibration",
            traffic="Observed track presence; directional crossings require drawn lines",
        ),
    )
