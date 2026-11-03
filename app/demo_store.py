"""Install the simulated store into a workspace, next to any real recordings."""

from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, time, timedelta
import json
import os
import shutil
from zoneinfo import ZoneInfo

from app import simulation as sim
from app.p1.geometry import save_geometry
from app.p1.media import register
from app.storage import canonical


def build_day_job(day_text, settings, tz_name):
    """Runs in a worker process; only plain data crosses the boundary."""
    rollups, _ = sim.build_day(date.fromisoformat(day_text), settings | sim.RULES, tz_name)
    return day_text, list(rollups.values())


def purge(ops):
    """Remove a previous simulated store. Real recordings are never touched."""
    catalog = ops.catalog
    with catalog.connect(write=True) as db:
        db.execute("DELETE FROM ops_simulated_days WHERE store_id=?", (sim.STORE_ID,))
        rows = db.execute("SELECT id,sha,metadata FROM videos").fetchall()
        targets = [r for r in rows if json.loads(r["metadata"]).get("collection_session") == sim.COLLECTION]
        for row in targets:
            db.execute("DELETE FROM ops_context WHERE video_id=?", (row["id"],))
            db.execute("DELETE FROM ops_rollup_cache WHERE video_id=?", (row["id"],))
            db.execute("DELETE FROM geometry WHERE video_id=?", (row["id"],))
            db.execute("DELETE FROM videos WHERE id=?", (row["id"],))
            if not db.execute("SELECT 1 FROM videos WHERE sha=?", (row["sha"],)).fetchone():
                db.execute("DELETE FROM frames WHERE sha=?", (row["sha"],))
                db.execute("DELETE FROM assets WHERE sha=?", (row["sha"],))
    for path in ops.live.root.glob("*/summary.json"):
        try:
            simulated = json.loads(path.read_text(encoding="utf-8")).get("simulated")
        except (OSError, ValueError):
            continue
        if simulated:
            with catalog.connect(write=True) as db:
                db.execute("DELETE FROM ops_reviews WHERE run_key=?", ("live:" + path.parent.name,))
            shutil.rmtree(path.parent, ignore_errors=True)
    shutil.rmtree(catalog.root / "simulation", ignore_errors=True)


def seed_store(ops, days=14, today=None, progress=None):
    """Simulate the last `days` days and one camera clip from the most recent day."""
    report = progress or (lambda stage, fraction: None)
    catalog = ops.catalog
    settings = ops.settings()
    # A demo store needs a value to show what a review queue is worth.
    if not settings.get("avg_item_value"):
        settings = settings | dict(avg_item_value=1850)
        with catalog.connect(write=True) as db:
            db.execute("INSERT OR REPLACE INTO ops_settings VALUES ('workspace',?)", (canonical(settings),))
    tz_name = settings["timezone"]
    tz = ZoneInfo(tz_name)
    today = today or datetime.now(tz).date()
    last = sim.demo_day(today)
    purge(ops)
    report("days", 0.02)
    earlier = [d for d in sim.days_before(today, days) if d != last]
    results = {}
    finished = 0

    def collect(day_text, rollups):
        nonlocal finished
        results[day_text] = rollups
        finished += 1
        report("days", 0.02 + 0.6 * finished / days)

    workers = min(len(earlier), max(1, (os.cpu_count() or 2) - 1))
    try:
        if workers < 2:
            raise OSError("One worker is simplest in the current process")
        with ProcessPoolExecutor(max_workers=workers) as pool:
            jobs = [pool.submit(build_day_job, d.isoformat(), settings, tz_name) for d in earlier]
            for job in jobs:
                collect(*job.result())
    except (OSError, RuntimeError):
        # Restricted environments may refuse subprocesses; the result is identical.
        for day in earlier:
            if day.isoformat() not in results:
                collect(*build_day_job(day.isoformat(), settings, tz_name))
    rollups, shoppers = sim.build_day(last, settings | sim.RULES, tz_name, scripted=sim.clip_scripts())
    collect(last.isoformat(), list(rollups.values()))
    with catalog.connect(write=True) as db:
        for day_text, values in results.items():
            for value in values:
                db.execute(
                    "INSERT OR REPLACE INTO ops_simulated_days VALUES (?,?,?)",
                    (sim.STORE_ID, value["date"], json.dumps(value)),
                )
    folder = catalog.root / "simulation"
    folder.mkdir(exist_ok=True)
    report("video", 0.64)
    path = sim.render_clip(folder / "overhead-camera-01.mp4", shoppers, lambda f: report("video", 0.64 + 0.32 * f))
    report("run", 0.96)
    video = register(
        catalog,
        path,
        store_id=sim.STORE_ID,
        camera_id=sim.CAMERA_ID,
        stream_session_id="simulated-clip",
        collection_session=sim.COLLECTION,
        display_name=sim.DISPLAY_NAME,
    )
    saved = save_geometry(catalog, video["id"], sim.geometry())
    start, end = sim.clip_window()
    rows, events, _ = sim.sample_rows(shoppers, start, end, sim.CLIP_SAMPLE_MS, last.isoformat(), "clip")
    ratio = sim.CLIP_FPS * sim.CLIP_SAMPLE_MS // 1000
    for row in rows:
        row["frame_index"] *= ratio
    (folder / "clip-observations.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (folder / "clip-events.json").write_text(json.dumps(events), encoding="utf-8")
    key = sim.write_run(ops.live.root, video["id"], saved["id"], rows, events)
    moment = datetime.combine(last, time(sim.OPEN_HOUR), tz) + timedelta(seconds=start)
    ops.save_context(
        video["id"],
        dict(
            label="",
            started_at_utc=moment.astimezone(ZoneInfo("UTC")).isoformat(),
            shift_label="Evening shift",
            notes="Simulated clip. Nothing here was filmed.",
        ),
    )
    with catalog.connect(write=True) as db:
        row = db.execute("SELECT payload FROM ops_context WHERE video_id=?", (video["id"],)).fetchone()
        context = json.loads(row[0]) | dict(time_basis="simulated")
        db.execute("UPDATE ops_context SET payload=? WHERE video_id=?", (canonical(context), video["id"]))
    report("done", 1.0)
    return dict(video_id=video["id"], run_key="live:" + key, days=len(results))
