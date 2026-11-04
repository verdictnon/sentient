"""Store-level reductions: hours, days and periods built from per-recording analyses.

A rollup is the only thing the store overview reads, so real recordings and the
simulated store share one code path. Each rollup keeps its basis ("measured" or
"simulated") so the two are never blended, and each value stays None when the
footage cannot support it: an unconfigured door is unknown, not zero.
"""

from collections import Counter, defaultdict
from datetime import timedelta

from app.journeys import CROSSCHECK_STATUSES, NEEDS_REVIEW, line_flow, routes

ROLLUP_VERSION = 1
GRID = (48, 27)
WAIT_BIN_SECONDS = 15


def empty_hour():
    return dict(
        entries=0,
        exits=0,
        tracks=0,
        people_sum=0,
        samples=0,
        people_peak=0,
        queue_peak=0,
        queue_over_s=0.0,
        coverage_s=0.0,
        pickups=0,
        needs_review=0,
        wait_sum=0.0,
        wait_count=0,
    )


def empty_day(date, basis, camera_id):
    return dict(
        version=ROLLUP_VERSION,
        date=date,
        basis=basis,
        camera_id=camera_id,
        hours=defaultdict(empty_hour),
        zones={},
        routes=Counter(),
        flows=Counter(),
        heat=defaultdict(Counter),
        waits=Counter(),
        wait_sum=0.0,
        wait_count=0,
        wait_max=None,
        crosscheck=Counter(),
        review_zones=Counter(),
        visit_seconds=0.0,
        tracks=0,
        abandoned=0,
        served=0,
        doors_configured=False,
        queue_configured=False,
        checkout_configured=False,
        funnel=Counter(),
        funnel_known=set(),
        pickups_scored=False,
    )


def day_rollups(result, rows, geometry, started, tz, first_media_ms, dimensions, *, basis, camera_id):
    """Split one analysed recording into per-day rollups on the store's own clock."""
    second_cache = {}

    def bucket(ms):
        if started is None:
            return "undated", None
        second = int((ms - first_media_ms) // 1000)
        cached = second_cache.get(second)
        if cached is None:
            local = (started + timedelta(seconds=second)).astimezone(tz)
            cached = local.date().isoformat(), local.hour
            second_cache[second] = cached
        return cached

    days = {}

    def day(key):
        if key not in days:
            days[key] = empty_day(key, basis, camera_id)
        return days[key]

    width, height = dimensions
    cell_w, cell_h = width / GRID[0], height / GRID[1]
    previous_ms = None
    for row in rows:
        ms = row["media_ms"]
        date, hour = bucket(ms)
        target = day(date)
        slot = target["hours"][hour]
        observed = [t for t in row.get("tracks", []) if t["position_status"] == "observed"]
        slot["people_sum"] += len(observed)
        slot["samples"] += 1
        slot["people_peak"] = max(slot["people_peak"], len(observed))
        if previous_ms is not None and 0 < ms - previous_ms <= 1000:
            slot["coverage_s"] += (ms - previous_ms) / 1000
        previous_ms = ms
        heat = target["heat"][hour]
        for track in observed:
            box = track["box"]
            x = min(GRID[0] - 1, max(0, int((box[0] + box[2]) / 2 / cell_w)))
            y = min(GRID[1] - 1, max(0, int(box[3] / cell_h)))
            heat[y * GRID[0] + x] += 1

    limit = result["queue"].get("limit", 3)
    timeline = result["timeline"]
    for sample in timeline:
        date, hour = bucket(sample["media_ms"])
        slot = day(date)["hours"][hour]
        slot["queue_peak"] = max(slot["queue_peak"], sample["queue"])
    for a, b in zip(timeline, timeline[1:]):
        gap = b["media_ms"] - a["media_ms"]
        if 0 < gap <= 1000 and a["queue"] >= limit:
            date, hour = bucket(a["media_ms"])
            day(date)["hours"][hour]["queue_over_s"] += gap / 1000

    shapes = {s["id"]: s for s in geometry["shapes"]}
    lines = {k: s for k, s in shapes.items() if s["kind"] == "line"}
    for crossing in result["crossings"]:
        shape = lines.get(crossing["area_id"])
        flow = line_flow(shape, crossing["direction"]) if shape else None
        if flow:
            date, hour = bucket(crossing["media_ms"])
            day(date)["hours"][hour]["entries" if flow == "enter" else "exits"] += 1

    for track in result["tracks"]:
        date, hour = bucket(track["first_ms"])
        target = day(date)
        target["hours"][hour]["tracks"] += 1
        target["tracks"] += 1
        target["visit_seconds"] += track.get("observed_seconds") or 0

    for visit in result["visits"]:
        date, hour = bucket(visit["start_ms"])
        target = day(date)
        zone = target["zones"].setdefault(
            visit["area_id"],
            dict(
                id=visit["area_id"],
                name=visit["name"],
                kind=visit["kind"],
                visits=0,
                dwell_s=0.0,
                pickups=0,
                tracks=set(),
                people_sum=Counter(),
            ),
        )
        zone["visits"] += 1
        zone["dwell_s"] += visit.get("observed_seconds") or 0
        zone["tracks"].add(visit["track_id"])

    for sample in timeline:
        date, hour = bucket(sample["media_ms"])
        for key, value in sample["areas"].items():
            if value and key in day(date)["zones"]:
                day(date)["zones"][key]["people_sum"][hour] += value

    journey = routes(result["visits"], limit=400)
    # Routes are attributed to the first day in a recording; one recording rarely crosses midnight.
    first_day = bucket(rows[0]["media_ms"])[0] if rows else None
    if first_day:
        target = day(first_day)
        for route in journey["routes"]:
            key = ">".join(step["id"] for step in route["steps"])
            target["routes"][key] += route["count"]
        for flow in journey["flows"]:
            target["flows"][flow["from_id"], flow["to_id"]] += flow["count"]
        target["names"] = {
            a["id"]: dict(name=a["name"], kind=a["kind"]) for a in result["areas"]
        }
        queue = result["queue"]
        target["abandoned"] += queue.get("abandoned") or 0
        target["served"] += queue.get("served") or 0
        target["doors_configured"] = result["footfall"]["configured"]
        target["queue_configured"] = any(s["kind"] == "queue" for s in shapes.values())
        target["checkout_configured"] = any(s["kind"] == "checkout" for s in shapes.values())
        target["pickups_scored"] = result["summary"].get("pickup_scoring_available", True)
        for step in result["funnel"]:
            if step["count"] is not None:
                target["funnel"][step["key"]] += step["count"]
                target["funnel_known"].add(step["key"])

    for wait in result["queue"]["observed_waits"]:
        date, hour = bucket(wait["start_ms"])
        target = day(date)
        seconds = wait["observed_wait_seconds"]
        target["hours"][hour]["wait_sum"] += seconds
        target["hours"][hour]["wait_count"] += 1
        target["waits"][int(seconds // WAIT_BIN_SECONDS)] += 1
        target["wait_sum"] += seconds
        target["wait_count"] += 1
        target["wait_max"] = max(target["wait_max"] or 0, seconds)

    incidents = {e["id"]: e for e in result["incidents"] if e["kind"] == "pickup"}
    for check in result["crosscheck"]["events"]:
        incident = incidents.get(check["event_id"])
        if incident is None:
            continue
        date, hour = bucket(incident["media_ms"])
        target = day(date)
        target["crosscheck"][check["status"]] += 1
        target["hours"][hour]["pickups"] += 1
        shelf = check.get("shelf_id")
        if shelf and shelf in target["zones"]:
            target["zones"][shelf]["pickups"] += 1
        if check["status"] in NEEDS_REVIEW:
            target["hours"][hour]["needs_review"] += 1
            if shelf:
                target["review_zones"][shelf] += 1
    return {key: freeze(value) for key, value in days.items()}


def freeze(day):
    """Reduce a working rollup to plain JSON."""
    hours = {
        ("x" if hour is None else str(hour)): {
            k: (round(v, 1) if isinstance(v, float) else v) for k, v in values.items()
        }
        for hour, values in day["hours"].items()
    }
    return dict(
        version=ROLLUP_VERSION,
        date=day["date"],
        basis=day["basis"],
        camera_id=day["camera_id"],
        hours=hours,
        zones={
            key: dict(
                id=zone["id"],
                name=zone["name"],
                kind=zone["kind"],
                visits=zone["visits"],
                tracks=len(zone["tracks"]),
                dwell_s=round(zone["dwell_s"], 1),
                pickups=zone["pickups"],
                people_sum={("x" if h is None else str(h)): n for h, n in zone["people_sum"].items()},
            )
            for key, zone in day["zones"].items()
        },
        names=day.get("names", {}),
        routes=dict(day["routes"]),
        flows={f"{a}>{b}": n for (a, b), n in day["flows"].items()},
        heat={
            ("x" if hour is None else str(hour)): {str(k): v for k, v in cells.items()}
            for hour, cells in day["heat"].items()
        },
        waits={str(k): v for k, v in day["waits"].items()},
        wait_sum=round(day["wait_sum"], 1),
        wait_count=day["wait_count"],
        wait_max=day["wait_max"],
        crosscheck=dict(day["crosscheck"]),
        review_zones=dict(day["review_zones"]),
        visit_seconds=round(day["visit_seconds"], 1),
        tracks=day["tracks"],
        abandoned=day["abandoned"],
        served=day["served"],
        doors_configured=day["doors_configured"],
        queue_configured=day["queue_configured"],
        checkout_configured=day["checkout_configured"],
        pickups_scored=day["pickups_scored"],
        funnel={k: day["funnel"][k] for k in day["funnel_known"]},
    )


def _hour_key(value):
    return None if value == "x" else int(value)


def merge(days):
    """Combine day rollups of one basis into a single period."""
    hours = defaultdict(empty_hour)
    by_day = defaultdict(lambda: defaultdict(empty_hour))
    zones = {}
    names = {}
    route_counts = Counter()
    flow_counts = Counter()
    heat = defaultdict(lambda: defaultdict(int))
    waits = Counter()
    crosscheck = Counter()
    review_zones = Counter()
    funnel = Counter()
    funnel_known = set()
    totals = Counter()
    wait_max = None
    flags = dict(
        doors_configured=False,
        queue_configured=False,
        checkout_configured=False,
        pickups_scored=False,
    )
    for day in days:
        names.update(day.get("names", {}))
        for key, values in day["hours"].items():
            hour = _hour_key(key)
            for target in (hours[hour], by_day[day["date"]][hour]):
                for name, value in values.items():
                    if name in ("people_peak", "queue_peak"):
                        target[name] = max(target[name], value)
                    else:
                        target[name] += value
        for key, zone in day["zones"].items():
            target = zones.setdefault(
                key,
                dict(
                    id=key,
                    name=zone["name"],
                    kind=zone["kind"],
                    visits=0,
                    tracks=0,
                    dwell_s=0.0,
                    pickups=0,
                    people_sum=Counter(),
                ),
            )
            target["visits"] += zone["visits"]
            target["tracks"] += zone["tracks"]
            target["dwell_s"] += zone["dwell_s"]
            target["pickups"] += zone["pickups"]
            for hour, value in zone.get("people_sum", {}).items():
                target["people_sum"][hour] += value
        route_counts.update(day["routes"])
        flow_counts.update(day["flows"])
        for hour, cells in day["heat"].items():
            target = heat[hour]
            for cell, value in cells.items():
                target[cell] += value
        waits.update(day["waits"])
        crosscheck.update(day["crosscheck"])
        review_zones.update(day["review_zones"])
        funnel.update(day["funnel"])
        funnel_known.update(day["funnel"])
        for key in ("wait_sum", "wait_count", "visit_seconds", "tracks", "abandoned", "served"):
            totals[key] += day[key]
        if day["wait_max"] is not None:
            wait_max = max(wait_max or 0, day["wait_max"])
        for key in flags:
            flags[key] = flags[key] or day[key]
    return dict(
        hours=hours,
        by_day=by_day,
        zones=zones,
        names=names,
        routes=route_counts,
        flows=flow_counts,
        heat=heat,
        waits=waits,
        crosscheck=crosscheck,
        review_zones=review_zones,
        funnel={k: funnel[k] for k in funnel_known},
        totals=totals,
        wait_max=wait_max,
        flags=flags,
        days=[d["date"] for d in days],
    )


def wait_percentile(waits, share):
    total = sum(waits.values())
    if not total:
        return None
    seen = 0
    for key in sorted(waits, key=int):
        seen += waits[key]
        if seen >= total * share:
            return (int(key) + 0.5) * WAIT_BIN_SECONDS
    return None


def kpis(period):
    hours = {h: v for h, v in period["hours"].items() if h is not None}
    flags = period["flags"]
    totals = period["totals"]
    entries = sum(v["entries"] for v in period["hours"].values()) if flags["doors_configured"] else None
    exits = sum(v["exits"] for v in period["hours"].values()) if flags["doors_configured"] else None
    busiest = None
    if hours:
        # A period can repeat each hour of the day, so rank by mean arrivals on covered days.
        key = "entries" if flags["doors_configured"] else "tracks"
        best = max(hours.items(), key=lambda item: (item[1][key], item[1]["people_sum"]))
        if best[1][key]:
            busiest = dict(hour=best[0], value=round(best[1][key] / max(1, len(period["days"])), 1), metric=key)
    samples = sum(v["samples"] for v in period["hours"].values())
    review = sum(period["crosscheck"].get(s, 0) for s in NEEDS_REVIEW)
    # The day-long median hides the rush; the worst well-sampled hour shows it.
    waits = [v["wait_sum"] / v["wait_count"] for v in hours.values() if v["wait_count"] >= 5]
    pickups = sum(period["crosscheck"].values()) if flags["pickups_scored"] else None
    coverage = sum(v["coverage_s"] for v in period["hours"].values())
    return dict(
        entries=entries,
        exits=exits,
        tracks=totals["tracks"],
        busiest_hour=busiest,
        people_peak=max((v["people_peak"] for v in period["hours"].values()), default=None),
        people_mean=round(sum(v["people_sum"] for v in period["hours"].values()) / samples, 1) if samples else None,
        visit_minutes=round(totals["visit_seconds"] / totals["tracks"] / 60, 1) if totals["tracks"] else None,
        queue_peak=max((v["queue_peak"] for v in period["hours"].values()), default=None) if flags["queue_configured"] else None,
        queue_over_minutes=round(sum(v["queue_over_s"] for v in period["hours"].values()) / 60, 1) if flags["queue_configured"] else None,
        wait_median_s=wait_percentile(period["waits"], 0.5),
        wait_p90_s=wait_percentile(period["waits"], 0.9),
        wait_max_s=period["wait_max"],
        wait_peak_s=round(max(waits), 1) if waits else None,
        abandoned=totals["abandoned"] if flags["queue_configured"] else None,
        served=totals["served"] if flags["queue_configured"] else None,
        pickups=pickups,
        needs_review=review if flags["pickups_scored"] and flags["checkout_configured"] else None,
        left_without_checkout=period["crosscheck"].get("left_without_checkout", 0) if flags["pickups_scored"] and flags["checkout_configured"] else None,
        lost_before_checkout=period["crosscheck"].get("lost_before_checkout", 0) if flags["pickups_scored"] and flags["checkout_configured"] else None,
        reached_checkout=period["crosscheck"].get("reached_checkout", 0) if flags["pickups_scored"] and flags["checkout_configured"] else None,
        coverage_hours=round(coverage / 3600, 1),
    )


def change(current, previous):
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 1)


def zone_rows(period):
    rows = []
    shoppers = max((z["tracks"] for z in period["zones"].values()), default=0)
    total_tracks = period["totals"]["tracks"]
    for zone in period["zones"].values():
        rows.append(
            dict(
                id=zone["id"],
                name=zone["name"],
                kind=zone["kind"],
                visits=zone["visits"],
                tracks=zone["tracks"],
                reach=round(zone["tracks"] / total_tracks, 3) if total_tracks else None,
                dwell_per_visit_s=round(zone["dwell_s"] / zone["visits"], 1) if zone["visits"] else None,
                dwell_s=round(zone["dwell_s"], 1),
                pickups=zone["pickups"],
                pickup_rate=round(zone["pickups"] / zone["visits"], 3) if zone["visits"] else None,
                needs_review=period["review_zones"].get(zone["id"], 0),
                people_by_hour={h: n for h, n in zone["people_sum"].items()},
            )
        )
    rows.sort(key=lambda r: (-r["tracks"], -r["dwell_s"], r["name"]))
    return rows if shoppers else []


def route_rows(period, limit=8):
    """Whole routes rarely repeat, so rank the three-stop chains inside them."""
    chains = Counter()
    total = 0
    for key, count in period["routes"].items():
        stops = key.split(">")
        total += count
        if len(stops) < 3:
            chains[tuple(stops)] += count
            continue
        for index in range(len(stops) - 2):
            chains[tuple(stops[index : index + 3])] += count
    rows = []
    for chain, count in chains.most_common(limit):
        steps = []
        for item in chain:
            meta = period["names"].get(item, dict(name=item, kind="zone"))
            steps.append(dict(id=item, name=meta["name"], kind=meta["kind"]))
        rows.append(dict(steps=steps, count=count, share=round(count / total, 3) if total else 0))
    return rows


def flow_rows(period, limit=24):
    rows = []
    for key, count in period["flows"].most_common(limit):
        a, b = key.split(">")
        rows.append(dict(from_id=a, to_id=b, count=count))
    return rows


def hourly_rows(period):
    days = max(1, len(period["days"]))
    return [
        dict(
            hour=hour,
            per_day=round(values["entries"] / days, 1),
            tracks_per_day=round(values["tracks"] / days, 1),
            people_mean=round(values["people_sum"] / values["samples"], 1) if values["samples"] else None,
            wait_mean_s=round(values["wait_sum"] / values["wait_count"], 1) if values["wait_count"] else None,
            **values,
        )
        for hour, values in sorted(period["hours"].items(), key=lambda item: (item[0] is None, item[0]))
    ]


def grid_rows(period):
    """One row per calendar day for the busy-hours grid."""
    rows = []
    for date in sorted(period["by_day"]):
        rows.append(
            dict(
                date=date,
                hours={
                    str(hour): dict(
                        entries=v["entries"],
                        tracks=v["tracks"],
                        people_mean=round(v["people_sum"] / v["samples"], 1) if v["samples"] else None,
                        queue_peak=v["queue_peak"],
                    )
                    for hour, v in period["by_day"][date].items()
                    if hour is not None
                },
            )
        )
    return rows


def heat_rows(period):
    """Density by hour of day; the client sums the hours it needs."""
    return {hour: dict(cells) for hour, cells in period["heat"].items()}


def _contiguous(hours):
    """Collapse hours into the single widest continuous window."""
    if not hours:
        return None
    ordered = sorted(hours)
    best = current = [ordered[0]]
    for hour in ordered[1:]:
        current = current + [hour] if hour == current[-1] + 1 else [hour]
        if len(current) > len(best):
            best = current
    return best[0], best[-1] + 1


def recommendations(period, numbers, previous, settings):
    """Plain findings with the numbers behind them. The client words them."""
    items = []
    flags = period["flags"]
    hours = {h: v for h, v in period["hours"].items() if h is not None}
    days = max(1, len(period["days"]))

    review = numbers["needs_review"]
    if review:
        top = period["review_zones"].most_common(1)
        name = None
        count = 0
        if top:
            name = period["zones"].get(top[0][0], {}).get("name")
            count = top[0][1]
        items.append(
            dict(
                code="review_pickups",
                tone="alert",
                view="incidents",
                params=dict(
                    count=review,
                    left=numbers["left_without_checkout"],
                    zone=name,
                    zone_count=count,
                ),
            )
        )

    if flags["queue_configured"] and numbers["queue_over_minutes"]:
        pressure = {h: v["queue_over_s"] / days for h, v in hours.items() if v["queue_over_s"] / days >= 300}
        window = _contiguous(pressure)
        if window:
            items.append(
                dict(
                    code="open_till",
                    tone="action",
                    view="analytics",
                    params=dict(
                        start=window[0],
                        end=window[1],
                        minutes=round(sum(pressure.values()) / 60),
                        limit=settings.get("queue_limit", 3),
                        wait=numbers["wait_median_s"],
                        abandoned=numbers["abandoned"],
                    ),
                )
            )

    shelves = [z for z in zone_rows(period) if z["kind"] == "shelf" and z["visits"] >= 20 and z["dwell_per_visit_s"]]
    if len(shelves) >= 3 and numbers["pickups"]:
        rate = sorted(s["pickup_rate"] or 0 for s in shelves)[len(shelves) // 2]
        dwell = sorted(s["dwell_per_visit_s"] for s in shelves)[len(shelves) // 2]
        stuck = [s for s in shelves if s["dwell_per_visit_s"] > dwell * 1.25 and (s["pickup_rate"] or 0) < rate * 0.7]
        if stuck:
            item = max(stuck, key=lambda s: s["dwell_per_visit_s"])
            items.append(
                dict(
                    code="dwell_no_pickup",
                    tone="action",
                    view="analytics",
                    params=dict(
                        zone=item["name"],
                        dwell=item["dwell_per_visit_s"],
                        rate=round((item["pickup_rate"] or 0) * 100),
                        typical=round(rate * 100),
                    ),
                )
            )
        reach = [s for s in shelves if s["reach"] is not None]
        if reach:
            cold = min(reach, key=lambda s: s["reach"])
            top_reach = max(s["reach"] for s in reach)
            if cold["reach"] < top_reach * 0.35:
                items.append(
                    dict(
                        code="cold_zone",
                        tone="action",
                        view="analytics",
                        params=dict(zone=cold["name"], reach=round(cold["reach"] * 100), top=round(top_reach * 100)),
                    )
                )

    if numbers["busiest_hour"]:
        items.append(
            dict(
                code="peak_hour",
                tone="info",
                view="analytics",
                params=dict(
                    hour=numbers["busiest_hour"]["hour"],
                    value=numbers["busiest_hour"]["value"],
                    metric=numbers["busiest_hour"]["metric"],
                ),
            )
        )

    if previous and previous["entries"] and numbers["entries"] is not None:
        delta = change(numbers["entries"], previous["entries"])
        if delta is not None and abs(delta) >= 3:
            items.append(dict(code="traffic_change", tone="info", view="analytics", params=dict(delta=delta)))
    return items


def payload(days, previous_days, settings):
    """Everything the store overview needs for one period."""
    period = merge(days)
    numbers = kpis(period)
    earlier = kpis(merge(previous_days)) if previous_days else None
    deltas = {}
    if earlier:
        for key in ("entries", "tracks", "needs_review", "wait_peak_s", "people_peak"):
            deltas[key] = change(numbers.get(key), earlier.get(key))
    value = settings.get("avg_item_value") or 0
    return dict(
        kpis=numbers,
        previous=earlier,
        deltas=deltas,
        hourly=hourly_rows(period),
        grid=grid_rows(period),
        zones=zone_rows(period),
        routes=route_rows(period),
        flows=flow_rows(period),
        heat=heat_rows(period),
        heat_grid=list(GRID),
        funnel=period["funnel"],
        crosscheck={k: period["crosscheck"].get(k, 0) for k in CROSSCHECK_STATUSES},
        recommendations=recommendations(period, numbers, earlier, settings),
        value_at_risk=dict(
            item_value=value,
            currency=settings.get("currency", "KZT"),
            amount=round(value * numbers["left_without_checkout"]) if value and numbers["left_without_checkout"] is not None else None,
        ),
        flags=period["flags"],
        days=period["days"],
    )
