"""Real clips arrive with saved model output, and nothing here invents a detection."""

import gzip
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import showcase
from app.p1.demo import make_video
from app.p1.server import create_app


def clip(folder):
    folder.mkdir()
    video = make_video(folder / "aisle.mp4", times=list(range(0, 1000, 100)), width=320, height=180)
    frames = [dict(t=t, boxes=[[1, 20.0, 30.0, 60.0, 120.0, 0.9, True, None]]) for t in range(0, 1000, 200)]
    payload = dict(
        name="Aisle", store_id="real-shop", store_name="Real shop", camera_id="cam-1", size=[320, 180],
        duration_s=0.9, source="Test", source_url="https://example.com", licence="CC BY 4.0",
        model=dict(detector="pose-test", tracker="ByteTrack", sample_ms=200), geometry=None, frames=frames,
    )
    video.with_suffix(".json.gz").write_bytes(gzip.compress(json.dumps(payload).encode()))
    return payload


class ShowcaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.payload = clip(self.folder / "clips")
        patcher = patch.object(showcase, "FOLDERS", (self.folder / "clips",))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_saved_tracks_and_video_are_served_unchanged(self):
        with TestClient(create_app(self.folder / "workspace")) as web:
            self.assertEqual(web.get("/api/showcase").json()[0]["id"], "aisle")
            self.assertEqual(web.get("/api/showcase/aisle").json(), self.payload)
            part = web.get("/api/showcase/aisle/video.mp4", headers={"Range": "bytes=0-99"})
            self.assertEqual(part.status_code, 206)
            self.assertEqual(web.get("/api/showcase/none").status_code, 404)

    def test_import_makes_a_real_recording_with_a_finished_run(self):
        application = create_app(self.folder / "workspace")
        with TestClient(application) as web:
            operations = application.state.operations
            self.assertEqual(showcase.import_footage(operations), ["aisle"])
            self.assertEqual(showcase.import_footage(operations), [])
            video = web.get("/api/videos").json()[0]
            self.assertFalse(video["source_profile"].get("simulated", False))
            run = web.get("/api/ops/runs", params=dict(video_id=video["id"])).json()[0]
            self.assertEqual(web.get(f"/api/ops/runs/{run['key']}/insights").json()["incidents"], [])
            summary = json.loads(next(operations.live.root.glob("*/summary.json")).read_text(encoding="utf-8"))
            self.assertFalse(summary["simulated"])
            self.assertEqual(summary["model_id"], "pose-test")


if __name__ == "__main__":
    unittest.main()
