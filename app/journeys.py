"""Customer journeys inside one camera view.

This module answers the questions in plan.txt that sit on top of the observed
tracks: how many people entered and left, which routes they took, and whether a
pickup candidate was followed by a visit to the checkout.

Every result is derived from observed area visits and directional crossings.
Identity stays camera-local, so a fragmented track can end a journey early. For
that reason a cross-check produces a review priority and never a theft verdict.
"""

from collections import Counter, defaultdict

# Ordered from "look at this first" to "nothing to review".
CROSSCHECK_STATUSES = (
    "left_without_checkout",
    "lost_before_checkout",
    "still_in_view",
    "checkout_not_mapped",
    "reached_checkout",
    "unavailable",
)
CROSSCHECK_PRIORITY = {status: i for i, status in enumerate(CROSSCHECK_STATUSES)}
NEEDS_REVIEW = ("left_without_checkout", "lost_before_checkout")

# A track seen this close to the last sample may simply still be shopping.
TAIL_MS = 3000
# Walking through an area on the way somewhere else is not a stop on a route.
ROUTE_STOP_SECONDS = 6.0
# Shelf visits shorter than this are treated as passing by, not as engagement.
ENGAGED_SECONDS = 8.0
ROUTE_LIMIT = 12


def line_flow(shape, direction):
    """Translate a positive/negative crossing into entering or leaving."""
    meaning = shape.get("positive_means")
    if meaning not in ("enter", "leave"):
        return None
    if direction == "positive":
        return meaning
    return "leave" if meaning == "enter" else "enter"


def footfall(crossings, geometry):
    """Entries and exits from doorway lines whose direction has been declared."""
    shapes = {s["id"]: s for s in geometry["shapes"] if s["kind"] == "line"}
    declared = [s for s in shapes.values() if s.get("positive_means") in ("enter", "leave")]
    if not declared:
        return dict(
            configured=False,
            entries=None,
            exits=None,
            lines=len(shapes),
            method="Declare which way a doorway line points to count entries and exits.",
        )
    counts = Counter()
    for crossing in crossings:
        shape = shapes.get(crossing["area_id"])
        if shape is None:
            continue
        flow = line_flow(shape, crossing["direction"])
        if flow:
            counts[flow] += 1
    return dict(
        configured=True,
        entries=counts["enter"],
        exits=counts["leave"],
        lines=len(declared),
        method="Crossings of doorway lines by observed footpoints. A person who returns is counted again.",
    )


def _ordered_visits(visits):
    by_track = defaultdict(list)
    for visit in sorted(visits, key=lambda v: (v["start_ms"], v["area_id"])):
        by_track[visit["track_id"]].append(visit)
    return by_track


def _exit_time(identity, after_ms, by_track, crossings, shapes):
    times = [
        v["start_ms"]
        for v in by_track.get(identity, ())
        if v["kind"] == "exit" and v["start_ms"] >= after_ms
    ]
    for crossing in crossings:
        if crossing["track_id"] != identity or crossing["media_ms"] < after_ms:
            continue
        shape = shapes.get(crossing["area_id"])
        if shape and line_flow(shape, crossing["direction"]) == "leave":
            times.append(crossing["media_ms"])
    return min(times) if times else None


def cross_check(pickups, tracks, visits, crossings, geometry, run_end_ms, physical_time=True):
    """Follow each pickup candidate to the checkout, the door or the end of its track."""
    by_track = _ordered_visits(visits)
    last_seen = {t["id"]: t["last_ms"] for t in tracks}
    lines = {s["id"]: s for s in geometry["shapes"] if s["kind"] == "line"}
    has_checkout = any(s["kind"] == "checkout" for s in geometry["shapes"])
    results = {}
    for event in pickups:
        identity = event["local_track_id"]
        start = event["start_media_ms"]
        anchor = event.get("detected_media_ms", event["end_media_ms"])
        result = dict(
            event_id=event["id"],
            track_id=identity,
            status="unavailable",
            checkout_ms=None,
            checkout_name=None,
            exit_ms=None,
            last_seen_ms=last_seen.get(identity),
            seconds_to_outcome=None,
            shelf_id=None,
            shelf_name=None,
        )
        results[event["id"]] = result
        shelf = next(
            (
                v
                for v in by_track.get(identity, ())
                if v["kind"] == "shelf" and v["start_ms"] <= anchor <= v["end_ms"]
            ),
            None,
        )
        if shelf:
            result.update(shelf_id=shelf["area_id"], shelf_name=shelf["name"])
        if not physical_time:
            continue
        if not has_checkout:
            result["status"] = "checkout_not_mapped"
            continue
        checkout = next(
            (v for v in by_track.get(identity, ()) if v["kind"] == "checkout" and v["end_ms"] >= start),
            None,
        )
        exit_ms = _exit_time(identity, anchor, by_track, crossings, lines)
        if checkout:
            result.update(
                status="reached_checkout",
                checkout_ms=checkout["start_ms"],
                checkout_name=checkout["name"],
                seconds_to_outcome=round(max(0, checkout["start_ms"] - anchor) / 1000, 1),
            )
        elif exit_ms is not None:
            result.update(
                status="left_without_checkout",
                exit_ms=exit_ms,
                seconds_to_outcome=round((exit_ms - anchor) / 1000, 1),
            )
        elif last_seen.get(identity, anchor) >= run_end_ms - TAIL_MS:
            result["status"] = "still_in_view"
        else:
            result["status"] = "lost_before_checkout"
    return results


def cross_check_totals(results):
    counts = Counter(r["status"] for r in results.values())
    return {status: counts[status] for status in CROSSCHECK_STATUSES} | {
        "needs_review": sum(counts[s] for s in NEEDS_REVIEW)
    }


def route_stops(visits):
    """One track's meaningful stops, with immediate repeats collapsed."""
    stops = []
    for visit in visits:
        seconds = visit.get("observed_seconds") or 0
        if visit["kind"] in ("shelf", "zone") and seconds < ROUTE_STOP_SECONDS:
            continue
        if stops and stops[-1]["id"] == visit["area_id"]:
            continue
        stops.append(dict(id=visit["area_id"], name=visit["name"], kind=visit["kind"]))
    return stops


def routes(visits, limit=ROUTE_LIMIT):
    """Most common stop sequences, plus how often one area led to the next."""
    sequences = Counter()
    names = {}
    flows = Counter()
    for items in _ordered_visits(visits).values():
        stops = route_stops(items)
        if not stops:
            continue
        for stop in stops:
            names[stop["id"]] = stop
        key = tuple(stop["id"] for stop in stops)
        sequences[key] += 1
        for a, b in zip(key, key[1:]):
            flows[a, b] += 1
    total = sum(sequences.values())
    return dict(
        tracks=total,
        routes=[
            dict(
                steps=[names[k] for k in key],
                count=count,
                share=round(count / total, 3) if total else 0,
            )
            for key, count in sequences.most_common(limit)
        ],
        flows=[
            dict(
                from_id=a,
                to_id=b,
                from_name=names[a]["name"],
                to_name=names[b]["name"],
                count=count,
            )
            for (a, b), count in flows.most_common(40)
        ],
    )


def funnel(tracks, visits, pickups, crossings, geometry, available=True):
    """How far observed tracks progressed through this camera view."""
    by_track = _ordered_visits(visits)
    shapes = {s["id"]: s for s in geometry["shapes"] if s["kind"] == "line"}
    known = {t["id"] for t in tracks}
    entered = {
        c["track_id"]
        for c in crossings
        if c["area_id"] in shapes and line_flow(shapes[c["area_id"]], c["direction"]) == "enter"
    }
    engaged = {
        identity
        for identity, rows in by_track.items()
        if any(v["kind"] == "shelf" and (v.get("observed_seconds") or 0) >= ENGAGED_SECONDS for v in rows)
    }
    picked = {e["local_track_id"] for e in pickups if e["local_track_id"] in known}
    checkout = {i for i, rows in by_track.items() if any(v["kind"] == "checkout" for v in rows)}
    has_doors = any(s.get("positive_means") in ("enter", "leave") for s in shapes.values())
    has_shelves = any(s["kind"] == "shelf" for s in geometry["shapes"])
    has_checkout = any(s["kind"] == "checkout" for s in geometry["shapes"])
    return [
        dict(key="entered", count=len(entered) if has_doors else None),
        dict(key="observed", count=len(known)),
        dict(key="engaged", count=len(engaged) if has_shelves else None),
        dict(key="picked", count=len(picked) if available else None),
        dict(key="checkout", count=len(checkout) if has_checkout else None),
    ]


def queue_outcomes(visits, timeline, limit, physical_time=True):
    """Who left the waiting area for the till, who gave up, and how long it stayed long."""
    if not physical_time:
        return dict(abandoned=None, served=None, over_limit_seconds=None)
    abandoned = served = 0
    for rows in _ordered_visits(visits).values():
        waits = [v for v in rows if v["kind"] == "queue"]
        if not waits:
            continue
        reached = any(v["kind"] == "checkout" and v["end_ms"] >= waits[0]["start_ms"] for v in rows)
        if reached:
            served += 1
        elif any(v.get("reason") == "left" for v in waits):
            abandoned += 1
    over_limit = 0.0
    for a, b in zip(timeline, timeline[1:]):
        gap = b["media_ms"] - a["media_ms"]
        if 0 < gap <= 1000 and a["queue"] >= limit:
            over_limit += gap / 1000
    return dict(abandoned=abandoned, served=served, over_limit_seconds=round(over_limit, 1))


def track_journey(identity, data):
    """The ordered story of one track for the investigation view."""
    lines = {s["id"]: s for s in data["geometry"]["geometry"]["shapes"] if s["kind"] == "line"}
    steps = []
    for visit in data["visits"]:
        if visit["track_id"] == identity:
            steps.append(
                dict(
                    type="visit",
                    ms=visit["start_ms"],
                    end_ms=visit["end_ms"],
                    name=visit["name"],
                    kind=visit["kind"],
                    seconds=visit["observed_seconds"],
                )
            )
    for crossing in data["crossings"]:
        if crossing["track_id"] != identity:
            continue
        shape = lines.get(crossing["area_id"])
        steps.append(
            dict(
                type="crossing",
                ms=crossing["media_ms"],
                name=crossing["name"],
                flow=line_flow(shape, crossing["direction"]) if shape else None,
                direction=crossing["direction"],
            )
        )
    for incident in data["incidents"]:
        if incident["kind"] == "pickup" and incident["track_id"] == identity:
            steps.append(
                dict(
                    type="pickup",
                    ms=incident["media_ms"],
                    score=incident["score"],
                    event_id=incident["id"],
                    crosscheck=incident.get("crosscheck"),
                )
            )
    return sorted(steps, key=lambda s: (s["ms"], s["type"]))
