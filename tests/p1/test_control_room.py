"""The camera patrol must preserve store boundaries, evidence and worker ownership."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.p1.demo import make_video
from app.p1.server import create_app


class FakeLive:
    def __init__(self, root):
        self.root = root
        self.sessions = {}
        self.lock = threading.RLock()
        self.started = []

    def start(self, video_id):
        key = f"{len(self.started) + 1:032x}"
        folder = self.root / key
        folder.mkdir(exist_ok=True)
        session = {
            "id": key,
            "video_id": video_id,
            "folder": folder,
            "done": threading.Event(),
            "status": "running",
            "sequence": 1,
            "payload": {"status": "running", "tracks": [], "pickups": []},
        }
        with self.lock:
            self.sessions[key] = session
            self.started.append(video_id)

        def finish():
            if session["done"].wait(0.24):
                return
            session["status"] = "completed"
            session["payload"]["status"] = "completed"
            session["sequence"] += 1
            (folder / "summary.json").write_text(
                json.dumps({"video_id": video_id, "status": "completed"})
            )
            session["done"].set()

        threading.Thread(target=finish, daemon=True).start()
        return {"id": key}

    def get(self, key):
        return self.sessions[key]

    def stop(self, key):
        session = self.sessions[key]
        session["status"] = "stopped"
        session["payload"]["status"] = "stopped"
        session["done"].set()
        return {"id": key, "status": "stopping"}


class ControlRoomTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "workspace")
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.ops = self.app.state.operations
        self.room = self.app.state.control_room
        self.fake = FakeLive(self.ops.live.root)
        self.room.live = self.fake
        first = make_video(
            self.root / "one.mp4", times=[0, 100, 200], width=1280, height=720
        )
        second = make_video(
            self.root / "two.mp4", times=[0, 100, 200, 300], width=1280, height=720
        )

        def register(path, camera, session, store="carr-public-research"):
            response = self.client.post(
                "/api/videos/import",
                json={
                    "path": str(path),
                    "store_id": store,
                    "camera_id": camera,
                    "stream_session_id": session,
                    "collection_session": "carr/research/CS1/test/" + session,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        self.a = register(first, "UNEJ-Mart", "V0774")
        self.b = register(second, "carr-unej-overhead", "V0775", store="carr-unej")
        self.other = register(first, "KPRI-Kalimantan", "V0762")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def wait(self, predicate, seconds=3):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            threading.Event().wait(0.02)
        self.fail("Timed out waiting for patrol state")

    def start(self, client="browser"):
        response = self.client.post(
            "/api/control-room/start",
            json={"store_id": "carr-unej", "client_id": client},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_manifest_uses_publisher_location_and_excludes_unrelated_store(self):
        data = self.client.get("/api/control-room/manifest").json()
        unej = next(s for s in data["stores"] if s["id"] == "carr-unej")
        # A shorter excerpt of an already represented publisher video is omitted.
        duplicate = self.client.post(
            "/api/videos/import",
            json={
                "path": str(self.root / "one.mp4"),
                "store_id": "carr-unej",
                "camera_id": "carr-unej-overhead",
                "stream_session_id": "shorter-copy",
                "collection_session": "carr/research/CS1/test/V0775-short",
            },
        )
        self.assertEqual(duplicate.status_code, 200)
        unej = next(s for s in self.room.manifest()["stores"] if s["id"] == "carr-unej")
        self.assertEqual(len(unej["cameras"]), 1)
        self.assertEqual(
            {c["id"] for c in unej["cameras"][0]["clips"]}, {self.a["id"], self.b["id"]}
        )
        self.assertNotIn(
            self.other["id"], {c["id"] for c in unej["cameras"][0]["clips"]}
        )
        self.assertEqual(data["map_basis"], "schematic")
        self.assertFalse(data["live_feed_available"])

    def test_loop_advances_without_manual_play_and_keeps_store_boundary(self):
        self.start()
        self.wait(lambda: len(self.fake.started) >= 3)
        self.assertEqual(
            self.fake.started[:3], [self.b["id"], self.a["id"], self.b["id"]]
        )
        self.assertGreaterEqual(self.room.status()["cycle"], 2)
        response = self.client.post(
            "/api/control-room/stop", json={"client_id": "browser"}
        )
        self.assertEqual(response.json()["status"], "paused")
        count = len(self.fake.started)
        threading.Event().wait(0.3)
        self.assertEqual(len(self.fake.started), count)

    def test_same_camera_joins_one_worker_and_next_skips_to_next_clip(self):
        self.start()
        self.wait(lambda: bool(self.fake.started))
        self.start("second-browser")
        self.assertEqual(len(self.fake.started), 1)
        self.client.post("/api/control-room/next", json={"client_id": "browser"})
        self.wait(lambda: len(self.fake.started) >= 2)
        self.assertEqual(self.fake.started[1], self.a["id"])
        self.client.post("/api/control-room/stop", json={"client_id": "browser"})
        self.assertIn(self.room.status()["status"], ("starting", "running"))
        self.client.post("/api/control-room/stop", json={"client_id": "second-browser"})
        self.assertEqual(self.room.status()["status"], "paused")

    def test_expired_browser_lease_stops_unattended_analysis(self):
        self.room.lease_seconds = 0.03
        self.start()
        self.wait(lambda: self.room.status()["status"] == "paused")
        self.assertEqual(len(self.fake.started), 1)

    def test_layout_is_validated_revisioned_and_persistent(self):
        body = {
            "store_id": "carr-unej",
            "camera_id": "carr-unej-overhead",
            "x": 45,
            "y": 55,
            "heading": 90,
            "expected_revision": 0,
        }
        self.assertEqual(
            self.client.put("/api/control-room/layout", json=body).status_code, 200
        )
        self.assertEqual(
            self.client.put("/api/control-room/layout", json=body).status_code, 409
        )
        self.assertEqual(
            self.client.put(
                "/api/control-room/layout",
                json=body | {"x": 110, "expected_revision": 1},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.put(
                "/api/control-room/layout",
                json=body | {"store_id": "carr-kpri-kalimantan"},
            ).status_code,
            404,
        )
        room = type(self.room)(self.ops)
        camera = next(s for s in room.manifest()["stores"] if s["id"] == "carr-unej")[
            "cameras"
        ][0]
        self.assertEqual(camera["layout"]["x"], 45)
        self.assertEqual(camera["layout"]["revision"], 1)
        room.shutdown()

    def test_cannot_preempt_an_unrelated_manual_live_analysis(self):
        self.fake.sessions["manual"] = {"done": threading.Event()}
        response = self.client.post(
            "/api/control-room/start",
            json={"store_id": "carr-unej", "client_id": "browser"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.fake.started, [])
        self.fake.sessions["manual"]["done"].set()

    def test_pruning_keeps_reviewed_reported_and_manual_evidence(self):
        for i in range(6):
            folder = self.fake.root / f"{i + 10:032x}"
            folder.mkdir()
            (folder / ".control-room").write_text("owned")
            (folder / "summary.json").write_text(
                json.dumps({"video_id": self.a["id"], "status": "completed"})
            )
            os.utime(folder, (100 + i, 100 + i))
        reviewed = f"{10:032x}"
        reported = f"{11:032x}"
        with self.ops.catalog.connect(write=True) as db:
            db.execute(
                "INSERT INTO ops_reviews VALUES (?,?,?)",
                ("live:" + reviewed, "event", "{}"),
            )
        report = self.ops.root / "snapshot"
        report.mkdir()
        (report / "report.json").write_text(
            json.dumps({"sources": [{"run_key": "live:" + reported}]})
        )
        manual = self.fake.root / "manual-evidence"
        manual.mkdir()
        (manual / "summary.json").write_text("{}")
        self.room.prune(self.a["id"])
        self.assertTrue((self.fake.root / reviewed).exists())
        self.assertTrue((self.fake.root / reported).exists())
        self.assertTrue(manual.exists())
        self.assertFalse((self.fake.root / f"{12:032x}").exists())


if __name__ == "__main__":
    unittest.main()
