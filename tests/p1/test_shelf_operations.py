"""Inventory integrity and virtual-camera integration, independent of model files."""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.p1.demo import make_video
from app.p1.geometry import validate_geometry
from app.p1.server import create_app
from app.simulation import CAMERA_VIEWS, COLLECTION
from app.simulation import geometry as sim_geometry


class ShelfOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "workspace", include_generated=True)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.ops = self.app.state.operations
        path = make_video(
            self.root / "camera.mp4",
            times=list(range(0, 2100, 100)),
            width=640,
            height=360,
        )
        self.video = self.client.post(
            "/api/videos/import",
            json={"path": str(path), "store_id": "shop", "camera_id": "shelves"},
        ).json()
        self.video_id = self.video["id"]
        self.geometry = {
            "geometry_version": 2,
            "frame_dims_px": [640, 360],
            "max_gap_ms": 1500,
            "active": {"line": None, "zone": None, "queue": None, "checkout": None},
            "shapes": [
                {
                    "id": "dairy",
                    "name": "Aisle 2 / Bay 1",
                    "product_type": "Milk & yoghurt",
                    "kind": "shelf",
                    "points": [[0, 0], [600, 0], [600, 350], [0, 350]],
                }
            ],
        }
        self.saved = self.client.put(
            f"/api/videos/{self.video_id}/geometry", json=self.geometry
        ).json()
        self.base = f"/api/ops/recordings/{self.video_id}/shelves"

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def update(self, **changes):
        value = {
            "action": "count",
            "quantity": 5,
            "capacity": 60,
            "reorder_at": 12,
            "expected_revision": 0,
            "notes": "Physical count",
        } | changes
        return self.client.put(self.base + "/dairy", json=value)

    def test_geometry_preserves_product_type_and_validates_it(self):
        self.assertEqual(
            self.saved["geometry"]["shapes"][0]["product_type"], "Milk & yoghurt"
        )
        for invalid in (13, "x" * 101):
            self.geometry["shapes"][0]["product_type"] = invalid
            self.assertEqual(
                self.client.put(
                    f"/api/videos/{self.video_id}/geometry", json=self.geometry
                ).status_code,
                422,
            )
        self.geometry["shapes"][0].pop("product_type")
        validate_geometry(self.geometry, [640, 360])

    def test_counts_refills_removals_and_conflicting_updates(self):
        before = self.client.get(self.base).json()["shelves"][0]
        self.assertEqual(before["status"], "unconfigured")
        self.assertIsNone(before["visits"])
        self.assertEqual(self.update(action="refill").status_code, 422)
        self.assertEqual(self.update().status_code, 200)
        self.assertEqual(
            self.client.get(self.base).json()["shelves"][0]["status"], "refill"
        )
        self.assertEqual(self.update().status_code, 409)
        self.assertEqual(
            self.update(action="refill", quantity=56, expected_revision=1).status_code,
            422,
        )
        self.assertEqual(
            self.update(action="refill", quantity=55, expected_revision=1).json()[
                "on_hand"
            ],
            60,
        )
        self.assertEqual(
            self.update(action="remove", quantity=60, expected_revision=2).json()[
                "on_hand"
            ],
            0,
        )
        after = self.client.get(self.base).json()
        self.assertEqual(after["shelves"][0]["status"], "empty")
        self.assertEqual(len(after["history"]), 3)
        self.assertEqual(after["history"][0]["before"], 60)
        self.assertEqual(
            self.update(action="remove", quantity=1, expected_revision=3).status_code,
            422,
        )
        self.assertEqual(
            self.update(quantity=True, expected_revision=3).status_code, 422
        )
        self.assertEqual(
            self.update(reorder_at=60, expected_revision=3).status_code, 422
        )
        self.assertEqual(
            self.client.put(
                self.base + "/missing",
                json={
                    "action": "count",
                    "quantity": 1,
                    "capacity": 60,
                    "reorder_at": 12,
                    "expected_revision": 0,
                },
            ).status_code,
            404,
        )

    def save_run(self, video=None, geometry=None):
        video = video or self.video
        geometry = geometry or self.saved
        key = "a" * 32
        folder = self.ops.live.root / key
        folder.mkdir(exist_ok=True)
        rows = [
            {
                "media_ms": float(ms),
                "frame_index": ms // 100,
                "tracks": [
                    {
                        "local_track_id": "local:1",
                        "position_status": "observed",
                        "box": [200, 100, 250, 200],
                    }
                ],
            }
            for ms in range(0, 2001, 200)
        ]
        (folder / "observations.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows)
        )
        summary = {
            "id": key,
            "video_id": video["id"],
            "geometry_id": geometry["id"],
            "status": "completed",
            "processed_frames": len(rows),
            "pickups": [],
            "scored_windows": 1,
        }
        (folder / "summary.json").write_text(json.dumps(summary))
        return key

    def test_stock_survives_restart_and_is_shared_only_with_same_camera(self):
        self.update()
        other = self.client.post(
            "/api/videos/import",
            json={
                "path": str(self.root / "camera.mp4"),
                "store_id": "other",
                "camera_id": "shelves",
            },
        ).json()
        self.client.put(f"/api/videos/{other['id']}/geometry", json=self.geometry)
        self.assertIsNone(
            self.client.get(f"/api/ops/recordings/{other['id']}/shelves").json()[
                "shelves"
            ][0]["stock"]
        )
        again = create_app(self.root / "workspace", include_generated=True)
        with TestClient(again) as client:
            self.assertEqual(
                client.get(self.base).json()["shelves"][0]["stock"]["on_hand"], 5
            )

    def test_activity_and_report_use_saved_run_and_freeze_inventory(self):
        self.update()
        self.save_run()
        snapshot = self.client.get(self.base).json()
        shelf = snapshot["shelves"][0]
        self.assertEqual(shelf["visits"], 1)
        self.assertEqual(shelf["unique_tracks"], 1)
        self.assertEqual(shelf["observed_dwell_seconds"], 2)
        report = self.client.post(
            "/api/ops/reports",
            json={
                "title": "Overall report",
                "scope": "collection",
                "video_ids": [self.video_id],
            },
        ).json()
        self.assertIn("id", report)
        self.update(action="refill", quantity=55, expected_revision=1)
        frozen = self.client.get(f"/api/ops/reports/{report['id']}/json").json()
        self.assertEqual(
            frozen["shelf_operations"][0]["shelves"][0]["stock"]["on_hand"], 5
        )
        self.assertEqual(frozen["areas"][0]["product_type"], "Milk & yoghurt")
        self.client.put(
            f"/api/ops/recordings/{self.video_id}",
            json={"started_at_utc": "2026-11-03T10:00:00+05:00"},
        )
        overview = self.client.get(
            "/api/ops/overview", params={"store_id": "shop"}
        ).json()
        ranked = overview["zones"][0]
        self.assertEqual(ranked["product_type"], "Milk & yoghurt")
        self.assertEqual(ranked["visits"], 1)
        self.assertEqual(ranked["dwell_s"], 2)
        pdf = self.client.get(f"/api/ops/reports/{report['id']}/pdf").content
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_virtual_cameras_have_one_source_clock_and_no_extra_recordings(self):
        self.assertFalse(
            self.client.get("/api/ops/demo-store/cameras").json()["available"]
        )
        video = self.client.post(
            "/api/videos/import",
            json={
                "path": str(self.root / "camera.mp4"),
                "store_id": "sim-store",
                "camera_id": "cam-01",
                "collection_session": COLLECTION,
            },
        ).json()
        layout = sim_geometry()
        layout["frame_dims_px"] = [640, 360]
        for shape in layout["shapes"]:
            shape["points"] = [[x / 2, y / 2] for x, y in shape["points"]]
        saved = self.client.put(
            f"/api/videos/{video['id']}/geometry", json=layout
        ).json()
        self.save_run(video, saved)
        count = len(self.ops.catalog.videos())
        manifest = self.client.get("/api/ops/demo-store/cameras").json()
        self.assertTrue(manifest["simulated"])
        self.assertEqual(len(manifest["cameras"]), 3)
        self.assertEqual(manifest["video_id"], video["id"])
        self.assertEqual(
            [c["id"] for c in manifest["cameras"]], ["shelves", "checkout", "exit"]
        )
        self.assertEqual(self.client.get(manifest["media_url"]).status_code, 200)
        self.assertEqual(len(self.ops.catalog.videos()), count)
        self.assertEqual(len(self.ops.runs(video["id"])), 1)
        for camera in CAMERA_VIEWS:
            x, y, x2, y2 = camera["roi"]
            self.assertTrue(0 <= x < x2 <= 1280 and 0 <= y < y2 <= 720)


if __name__ == "__main__":
    unittest.main()
