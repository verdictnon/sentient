"""Store rollups share one path for real and simulated data and keep them apart."""

from datetime import date, datetime
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from docx import Document
from fastapi.testclient import TestClient

from app import simulation as sim
from app.demo_store import seed_store
from app.insights import analyse
from app.p1.demo import make_video
from app.p1.server import create_app
from app.rollups import day_rollups, kpis, merge, payload, wait_percentile

ALMATY = ZoneInfo("Asia/Almaty")


def layout():
    return dict(
        geometry_version=2,
        frame_dims_px=[640, 360],
        max_gap_ms=1500,
        active=dict(line=None, zone=None, queue=None, checkout=None),
        shapes=[dict(id="shelf", name="Drinks", kind="shelf", points=[[100, 100], [400, 100], [400, 300], [100, 300]])],
    )


def rows(start, seconds, x=250):
    return [
        dict(
            frame_index=i,
            media_ms=float(start + i * 500),
            tracks=[dict(local_track_id="local:1", box=[x - 30, 80, x + 30, 240], position_status="observed")],
        )
        for i in range(seconds * 2)
    ]


def rollup(samples, started, geometry=None):
    geometry = geometry or layout()
    result = analyse(samples, geometry, [])
    result["summary"]["pickup_scoring_available"] = False
    return day_rollups(result, samples, geometry, started, ALMATY, 0.0, [640, 360], basis="measured", camera_id="cam")


class RollupTests(unittest.TestCase):
    def test_a_recording_is_split_at_local_midnight(self):
        started = datetime(2026, 11, 3, 23, 59, 50, tzinfo=ALMATY)
        days = rollup(rows(0, 20), started)
        self.assertEqual(sorted(days), ["2026-11-03", "2026-11-04"])
        self.assertIn("23", days["2026-11-03"]["hours"])
        self.assertIn("0", days["2026-11-04"]["hours"])

    def test_undated_recordings_have_no_hours_and_unknowns_stay_unknown(self):
        days = rollup(rows(0, 5), None)
        self.assertEqual(list(days), ["undated"])
        numbers = kpis(merge(days.values()))
        self.assertIsNone(numbers["entries"])
        self.assertIsNone(numbers["queue_peak"])
        self.assertIsNone(numbers["pickups"])
        self.assertIsNone(numbers["busiest_hour"])
        self.assertEqual(numbers["tracks"], 1)

    def test_payload_reports_the_change_against_the_previous_period(self):
        door = layout()
        door["shapes"].append(dict(id="d", name="Door", kind="line", points=[[320, 0], [320, 360]], hysteresis_px=2, positive_means="enter"))

        def crossing(identity, ms):
            return [
                dict(frame_index=i, media_ms=float(ms + i * 400), tracks=[dict(local_track_id=identity, box=[x - 10, 200, x + 10, 300], position_status="observed")])
                for i, x in enumerate((360, 280))
            ]

        def day(date_value, people):
            samples = [row for i in range(people) for row in crossing(f"local:{i}", i * 5000)]
            started = datetime.combine(date_value, datetime.min.time().replace(hour=10), ALMATY)
            return list(rollup(samples, started, door).values())

        current = day(date(2026, 11, 3), 3)
        previous = day(date(2026, 11, 2), 2)
        data = payload(current, previous, dict(queue_limit=3))
        self.assertEqual(data["kpis"]["entries"], 3)
        self.assertEqual(data["kpis"]["busiest_hour"]["hour"], 10)
        self.assertEqual(data["deltas"]["entries"], 50.0)

    def test_wait_percentile_reads_the_histogram(self):
        self.assertEqual(wait_percentile({"0": 1, "1": 1, "8": 2}, 0.5), 22.5)
        self.assertIsNone(wait_percentile({}, 0.5))


class SimulationTests(unittest.TestCase):
    def test_the_same_day_always_produces_the_same_people(self):
        day = date(2026, 10, 31)
        first = sim.simulate_day(day)
        second = sim.simulate_day(day)
        self.assertEqual(len(first), len(second))
        self.assertEqual(first[5].points, second[5].points)
        self.assertNotEqual(len(first), len(sim.simulate_day(date(2026, 11, 1))))

    def test_candidates_land_on_observed_samples_of_their_track(self):
        day = date(2026, 10, 28)
        group = sim.simulate_day(day)
        samples, events, _ = sim.sample_rows(group, 0, 7200, sim.DAY_SAMPLE_MS, day.isoformat(), "day")
        self.assertTrue(events)
        by_time = {row["media_ms"]: row for row in samples}
        for event in events[:40]:
            row = by_time[event["detected_media_ms"]]
            self.assertIn(event["local_track_id"], {t["local_track_id"] for t in row["tracks"]})

    def test_geometry_is_valid_and_doors_are_declared(self):
        from app.p1.geometry import validate_geometry

        layout_ = sim.geometry()
        validate_geometry(layout_, [sim.WIDTH, sim.HEIGHT])
        meanings = {s["name"]: s["positive_means"] for s in layout_["shapes"] if s["kind"] == "line"}
        self.assertEqual(meanings, {"Entrance door": "enter", "Exit door": "leave"})


class DemoStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.app = create_app(cls.root / "workspace")
        cls.client = TestClient(cls.app)
        cls.client.__enter__()
        cls.ops = cls.app.state.operations
        path = make_video(cls.root / "real.mp4", times=list(range(0, 3300, 100)), width=640, height=360)
        cls.real = cls.client.post("/api/videos/import", json=dict(path=str(path), store_id="corner-shop")).json()
        saved = cls.client.put(f"/api/videos/{cls.real['id']}/geometry", json=layout()).json()
        key = "f" * 32
        folder = cls.root / "workspace/live" / key
        folder.mkdir(parents=True)
        summary = dict(id=key, video_id=cls.real["id"], geometry_id=saved["id"], status="completed", processed_frames=6, scored_windows=1, pickups=[])
        (folder / "summary.json").write_text(json.dumps(summary))
        (folder / "observations.jsonl").write_text("\n".join(json.dumps(r) for r in rows(0, 3)))
        with patch.object(sim, "CLIP_SECONDS", 6):
            cls.seeded = seed_store(cls.ops, days=2, today=date(2026, 11, 4))

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.temp.cleanup()

    def test_stores_are_listed_separately_with_the_calendar_store_first(self):
        stores = self.client.get("/api/ops/stores").json()
        self.assertEqual([s["id"] for s in stores], [sim.STORE_ID, "corner-shop"])
        self.assertTrue(stores[0]["simulated"])
        self.assertFalse(stores[1]["simulated"])
        self.assertEqual(stores[0]["days"], 2)

    def test_simulated_overview_is_labelled_and_complete(self):
        data = self.client.get("/api/ops/overview").json()
        self.assertEqual(data["basis"], "simulated")
        self.assertEqual(data["mode"], "calendar")
        self.assertEqual(data["end"], "2026-11-03")
        self.assertEqual(data["available_days"], ["2026-11-02", "2026-11-03"])
        self.assertGreater(data["kpis"]["entries"], 100)
        self.assertIsNotNone(data["kpis"]["needs_review"])
        self.assertTrue(data["recommendations"])
        self.assertEqual(data["camera"]["frame_url"], "/api/ops/demo-store/floor.png")
        self.assertEqual(self.client.get(data["camera"]["frame_url"]).headers["content-type"], "image/png")
        self.assertEqual(len(data["week_grid"]), 2)
        week = self.client.get("/api/ops/overview", params=dict(start="2026-11-02", end="2026-11-03")).json()
        self.assertGreater(week["kpis"]["entries"], data["kpis"]["entries"])

    def test_real_undated_store_never_receives_simulated_numbers(self):
        data = self.client.get("/api/ops/overview", params=dict(store_id="corner-shop")).json()
        self.assertEqual(data["basis"], "measured")
        self.assertEqual(data["mode"], "collection")
        self.assertEqual(data["kpis"]["tracks"], 1)
        self.assertIsNone(data["kpis"]["entries"])
        self.assertEqual(data["recommendations"], [])
        self.assertEqual(data["camera"]["frame_url"], f"/api/videos/{self.real['id']}/frame/0")

    def test_dated_real_store_moves_onto_the_calendar_and_cache_follows_the_date(self):
        started = "2026-11-03T10:00:00+05:00"
        self.client.put("/api/ops/recordings/" + self.real["id"], json=dict(started_at_utc=started))
        try:
            data = self.client.get("/api/ops/overview", params=dict(store_id="corner-shop")).json()
            self.assertEqual(data["mode"], "calendar")
            self.assertEqual(data["available_days"], ["2026-11-03"])
            self.assertEqual(data["hourly"][0]["hour"], 10)
        finally:
            self.client.put("/api/ops/recordings/" + self.real["id"], json=dict())

    def test_z_report_is_a_pdf_in_both_languages(self):
        for language in ("en", "ru"):
            response = self.client.get("/api/ops/z-report.pdf", params=dict(lang=language))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(b"%PDF"))
            self.assertIn("sentient-z-report-2026-11-03.pdf", response.headers["content-disposition"])
        self.assertEqual(self.client.get("/api/ops/z-report.pdf", params=dict(lang="kk")).status_code, 422)

    def test_clip_recording_is_marked_simulated(self):
        video = self.client.get("/api/videos/" + self.seeded["video_id"]).json()
        self.assertTrue(video["source_profile"]["simulated"])
        self.assertEqual(video["source_profile"]["dataset"], "Simulated")
        context = next(r["context"] for r in self.client.get("/api/ops/workspace").json()["recordings"] if r["id"] == video["id"])
        self.assertEqual(context["time_basis"], "simulated")

    def test_evidence_clip_is_an_mp4_and_is_reused(self):
        run = self.seeded["run_key"]
        track = self.client.get(f"/api/ops/runs/{run}/insights").json()["tracks"][0]
        url = f"/api/ops/runs/{run}/tracks/{track['id']}/clip"
        first = self.client.get(url, params=dict(media_ms=2000))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.content[4:8], b"ftyp")
        clips = list((self.root / "workspace/clips").glob("*.mp4"))
        self.client.get(url, params=dict(media_ms=2000))
        self.assertEqual(len(list((self.root / "workspace/clips").glob("*.mp4"))), len(clips))
        self.assertEqual(self.client.get(f"/api/ops/runs/{run}/tracks/nobody/clip").status_code, 404)
        crop = self.client.get(f"/api/ops/runs/{run}/tracks/{track['id']}/evidence", params=dict(media_ms=2000, crop=True))
        self.assertTrue(crop.content.startswith(b"\x89PNG"))

    def test_track_endpoint_tells_the_journey_in_order(self):
        run = self.seeded["run_key"]
        data = self.client.get(f"/api/ops/runs/{run}/insights").json()
        track = max(data["tracks"], key=lambda t: len(t["area_ids"]))
        journey = self.client.get(f"/api/ops/runs/{run}/tracks/{track['id']}").json()["journey"]
        self.assertTrue(journey)
        self.assertEqual([s["ms"] for s in journey], sorted(s["ms"] for s in journey))

    def test_simulated_live_replay_needs_no_model_and_saves_a_labelled_run(self):
        video = self.seeded["video_id"]
        session = self.client.post("/api/live/videos/" + video)
        self.assertEqual(session.status_code, 200, session.text)
        key = session.json()["id"]
        deadline = time.monotonic() + 20
        latest = {}
        while time.monotonic() < deadline:
            latest = self.client.get("/api/live/sessions/" + key).json()
            if latest["status"] in ("completed", "stopped", "error"):
                break
            time.sleep(0.2)
        self.assertEqual(latest["status"], "completed", latest.get("latest", {}).get("error"))
        self.assertTrue(latest["latest"]["simulated"])
        self.assertIn("no detector", latest["latest"]["execution"])
        self.assertGreater(latest["latest"]["processed_frames"], 20)
        self.assertEqual(self.client.get("/api/ops/runs/live:" + key + "/insights").status_code, 200)

    def test_reports_keep_simulated_and_real_recordings_apart(self):
        recordings = self.client.get("/api/ops/workspace").json()["recordings"]
        fake = [r["id"] for r in recordings if r["simulated"]]
        real = [r["id"] for r in recordings if not r["simulated"]]
        self.assertTrue(fake and real)

        mixed = self.client.post("/api/ops/reports/preview", json=dict(title="Both", video_ids=fake + real))
        self.assertEqual(mixed.status_code, 422)
        self.assertIn("separately", mixed.text)

        # An open scope, which is what a schedule sends, means the real shop.
        everything = self.client.post("/api/ops/reports/preview", json=dict(title="All")).json()
        self.assertFalse(everything["simulated"])
        self.assertEqual({r["video_id"] for r in everything["recordings"]}, set(real))

        saved = self.client.post("/api/ops/reports", json=dict(title="Demo", video_ids=fake)).json()
        self.assertTrue(saved["simulated"])
        key = saved["id"]
        data = self.client.get(f"/api/ops/reports/{key}/json").json()
        self.assertTrue(data["simulated"])
        self.assertIn("Simulated data", data["warnings"][0])
        table = self.client.get(f"/api/ops/reports/{key}/csv").text.splitlines()
        self.assertTrue(table[0].endswith(",basis"))
        self.assertTrue(table[1].endswith(",simulated"))
        word = Document(BytesIO(self.client.get(f"/api/ops/reports/{key}/docx").content))
        self.assertIn("Simulated data", " ".join(p.text for p in word.paragraphs[:5]))

    def test_rebuilding_replaces_only_the_simulated_store(self):
        before = len(list(self.ops.live.root.glob("*/summary.json")))
        with patch.object(sim, "CLIP_SECONDS", 6):
            again = seed_store(self.ops, days=2, today=date(2026, 11, 4))
        type(self).seeded = again
        stores = self.client.get("/api/ops/stores").json()
        self.assertEqual(stores[0]["days"], 2)
        self.assertEqual(sum(s["id"] == "corner-shop" for s in stores), 1)
        self.assertLessEqual(len(list(self.ops.live.root.glob("*/summary.json"))), before)
        self.assertEqual(self.client.get("/api/videos/" + self.real["id"]).status_code, 200)
        self.assertEqual(len([v for v in self.client.get("/api/videos").json() if v["source_profile"]["simulated"]]), 1)


class LiveInferenceWatchTests(unittest.TestCase):
    """The real live path, with the detector and action model replaced by stubs."""

    def test_live_run_reports_outcomes_without_touching_model_results(self):
        import numpy as np
        from threading import RLock

        from app.live import LiveManager
        from app.p1.catalog import Catalog
        from app.p1.geometry import save_geometry
        from app.p1.media import register
        from app.p2.tracker import Detection

        class Detector:
            metadata = {}

            def detect(self, image, index):
                width, height = image.size
                x = (50 + index * 12) % width
                return [Detection((x, height * 0.2, x + width * 0.2, height * 0.8), 0.9, 0, index)]

            def close(self):
                pass

        class Runtime:
            metadata = dict(pickup_policy=dict(threshold=0.97), model_id="stub")

            def predict(self, batch):
                return np.full((len(batch), 3), 0.99)

            def close(self):
                pass

        class Tracking:
            inference_lock = RLock()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = Catalog(root / "workspace")
            path = make_video(root / "camera.mp4", times=list(range(0, 4000, 100)), width=640, height=360)
            video = register(catalog, path, store_id="s", camera_id="c", stream_session_id="x")
            geometry = layout()
            geometry["shapes"].append(dict(id="till", name="Till", kind="checkout", points=[[500, 0], [640, 0], [640, 360], [500, 360]]))
            save_geometry(catalog, video["id"], geometry)
            manager = LiveManager(catalog, Tracking(), None)
            manager.detector, manager.runtime = Detector(), Runtime()
            try:
                session = manager.start(video["id"])
                stored = manager.get(session["id"])
                self.assertTrue(stored["done"].wait(30))
                payload_ = stored["payload"]
                self.assertEqual(payload_["status"], "completed", payload_.get("error"))
                self.assertGreater(payload_["processed_frames"], 10)
                self.assertTrue(payload_["pickups"])
            finally:
                manager.pool.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
