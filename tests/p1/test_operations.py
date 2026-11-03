"""Operations must use observed positions, preserve review state and respect report time."""

from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

from fastapi.testclient import TestClient
from app.insights import analyse
from app.operations import next_due, period_start
from app.p1.demo import make_video
from app.p1.server import create_app


def geometry():
    return dict(
        geometry_version=2,
        frame_dims_px=[640, 360],
        max_gap_ms=1500,
        active=dict(line=None, zone=None, queue=None, checkout=None),
        shapes=[
            dict(
                id="shelf",
                name="Drinks",
                kind="shelf",
                points=[[100, 100], [400, 100], [400, 300], [100, 300]],
            )
        ],
    )


def row(ms, x=250, status="observed", identity="local:1"):
    return dict(
        frame_index=ms // 100,
        media_ms=ms,
        tracks=[
            dict(
                local_track_id=identity,
                box=[x - 30, 80, x + 30, 240],
                position_status=status,
            )
        ],
    )


class InsightTests(unittest.TestCase):
    def test_gaps_predictions_and_sparse_time_are_not_credited(self):
        rows = [row(0), row(200), row(400, status="predicted"), row(1600)]
        data = analyse(rows, geometry(), rules=dict(dwell_limit_s=1))
        self.assertEqual(data["tracks"][0]["observed_seconds"], 0.2)
        self.assertEqual(data["tracks"][0]["segments"], 2)
        self.assertEqual(data["summary"]["peak_people"], 1)
        self.assertEqual(data["areas"][0]["visits"], 2)
        sparse = analyse(rows, geometry(), physical_time=False)
        self.assertIsNone(sparse["tracks"][0]["observed_seconds"])
        self.assertIsNone(sparse["tracks"][0]["path_pixels"])
        self.assertEqual(sparse["transitions"], [])

    def test_area_transition_and_dwell_alert(self):
        g = geometry()
        g["shapes"].append(
            dict(
                id="other",
                name="Aisle",
                kind="zone",
                points=[[410, 100], [620, 100], [620, 300], [410, 300]],
            )
        )
        data = analyse(
            [row(ms) for ms in range(0, 1400, 200)] + [row(1400, x=500)],
            g,
            rules=dict(dwell_limit_s=1),
        )
        self.assertEqual(data["transitions"][0]["count"], 1)
        self.assertEqual(data["transitions"][0]["to_id"], "other")
        self.assertEqual(len([e for e in data["incidents"] if e["kind"] == "dwell"]), 1)

    def test_queue_transition_is_observed_and_boundary_entry_is_censored(self):
        g = geometry()
        g["shapes"][0]["kind"] = "queue"
        g["active"]["queue"] = "shelf"
        g["active"]["checkout"] = "service"
        g["shapes"].append(
            dict(
                id="service",
                name="Checkout",
                kind="checkout",
                points=[[410, 100], [620, 100], [620, 300], [410, 300]],
            )
        )
        data = analyse([row(0, x=50), row(200), row(400), row(600, x=500)], g)
        self.assertEqual(data["queue"]["median_complete_wait_seconds"], 0.4)
        censored = analyse([row(200), row(400), row(600, x=500)], g)
        self.assertIsNone(censored["queue"]["median_complete_wait_seconds"])
        self.assertEqual(
            censored["queue"]["observed_waits"][0]["observed_wait_seconds"], 0.4
        )

    def test_overnight_and_month_end_schedules(self):
        schedule = dict(
            cadence="shift",
            timezone="Asia/Almaty",
            shift_start="22:00",
            shift_end="06:00",
        )
        due = next_due(schedule, datetime(2026, 11, 3, 17, tzinfo=timezone.utc))
        self.assertEqual(due.isoformat(), "2026-11-04T01:00:00+00:00")
        self.assertEqual(
            period_start(schedule, due).isoformat(), "2026-11-03T17:00:00+00:00"
        )
        schedule["cadence"] = "monthly"
        due = next_due(schedule, datetime(2026, 12, 15, tzinfo=timezone.utc))
        self.assertEqual(due.isoformat(), "2026-12-31T19:00:00+00:00")
        self.assertEqual(
            period_start(schedule, due).isoformat(), "2026-11-30T19:00:00+00:00"
        )


class OperationsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / "workspace")
        self.client = TestClient(self.app)
        self.client.__enter__()
        path = make_video(
            self.root / "camera.mp4",
            times=list(range(0, 3300, 100)),
            width=640,
            height=360,
        )
        self.video = self.client.post(
            "/api/videos/import", json=dict(path=str(path))
        ).json()
        self.saved = self.client.put(
            "/api/videos/" + self.video["id"] + "/geometry", json=geometry()
        ).json()
        self.key = "c" * 32
        self.run = "live:" + self.key
        self.folder = self.root / "workspace/live" / self.key
        self.folder.mkdir(parents=True)
        event = dict(
            id="pickup:1",
            local_track_id="local:1",
            start_media_ms=1000,
            end_media_ms=2800,
            detected_media_ms=1200,
            score=0.99,
        )
        summary = dict(
            id=self.key,
            video_id=self.video["id"],
            geometry_id=self.saved["id"],
            status="completed",
            processed_frames=16,
            scored_windows=1,
            pickups=[event],
        )
        (self.folder / "summary.json").write_text(json.dumps(summary))
        (self.folder / "observations.jsonl").write_text(
            "\n".join(json.dumps(row(ms)) for ms in range(0, 3200, 200))
        )

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def report_request(self, **extra):
        return dict(
            title="Retail operations report",
            scope="collection",
            video_ids=[self.video["id"]],
            **extra,
        )

    def test_track_investigation_and_optimistic_reviews(self):
        data = self.client.get("/api/ops/runs/" + self.run + "/insights").json()
        self.assertEqual(data["summary"]["tracks"], 1)
        track = self.client.get("/api/ops/runs/" + self.run + "/tracks/local:1").json()
        self.assertEqual(len(track["samples"]), 16)
        url = "/api/ops/runs/" + self.run + "/reviews/pickup:1"
        r = self.client.put(
            url,
            json=dict(status="rejected", expected_revision=0, notes="False candidate"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            self.client.put(
                url, json=dict(status="accepted", expected_revision=0)
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.get("/api/ops/runs/" + self.run + "/insights").json()[
                "summary"
            ]["rejected"],
            1,
        )
        self.assertEqual(
            self.client.get("/api/ops/runs/live:../insights").status_code, 404
        )

    def test_period_excludes_undated_clips_and_clips_samples_at_midnight(self):
        request = self.report_request()
        request.update(
            scope="day",
            start="2026-11-03T00:00:00+05:00",
            end="2026-11-04T00:00:00+05:00",
        )
        p = self.client.post("/api/ops/reports/preview", json=request).json()
        self.assertEqual(p["coverage_status"], "no_matching_recordings")
        self.assertEqual(p["excluded"][0]["reason"], "Recording time is unknown")
        self.client.put(
            "/api/ops/recordings/" + self.video["id"],
            json=dict(started_at_utc="2026-11-02T23:59:59+05:00"),
        )
        p = self.client.post("/api/ops/reports/preview", json=request).json()
        self.assertEqual(p["summary"]["tracks"], 1)
        self.assertAlmostEqual(p["summary"]["observed_seconds"], 2.0)
        self.assertEqual(p["daily"][0]["date"], "2026-11-03")
        self.assertEqual(p["daily"][0]["pickups"], 1)

    def test_report_files_snapshot_reviews_and_scheduler_idempotency(self):
        request = self.report_request()
        r = self.client.post("/api/ops/reports", json=request)
        self.assertEqual(r.status_code, 200, r.text)
        key = r.json()["id"]
        self.assertTrue(
            self.client.get(f"/api/ops/reports/{key}/pdf").content.startswith(b"%PDF")
        )
        self.assertTrue(
            self.client.get(f"/api/ops/reports/{key}/docx").content.startswith(b"PK")
        )
        snapshot = self.client.get(f"/api/ops/reports/{key}/json").json()
        self.assertEqual(snapshot["summary"]["tracks"], 1)
        self.client.put(
            "/api/ops/runs/" + self.run + "/reviews/pickup:1",
            json=dict(status="accepted", expected_revision=0),
        )
        self.assertEqual(
            self.client.get(f"/api/ops/reports/{key}/json").json()["summary"][
                "accepted"
            ],
            0,
        )
        s = self.client.post(
            "/api/ops/schedules", json=dict(name="Shift summary", cadence="shift")
        ).json()
        self.app.state.operations.tick(datetime.fromisoformat(s["next_due"]))
        count = len(self.app.state.operations.reports())
        self.app.state.operations.tick(datetime.fromisoformat(s["next_due"]))
        self.assertEqual(len(self.app.state.operations.reports()), count)
        self.assertEqual(
            self.client.get("/api/ops/reports/not-a-report/pdf").status_code, 404
        )

    def test_duplicate_analysis_runs_do_not_double_the_report(self):
        key = "d" * 32
        folder = self.folder.parent / key
        folder.mkdir()
        summary = json.loads((self.folder / "summary.json").read_text())
        summary["id"] = key
        (folder / "summary.json").write_text(json.dumps(summary))
        (folder / "observations.jsonl").write_bytes(
            (self.folder / "observations.jsonl").read_bytes()
        )
        data = self.client.post(
            "/api/ops/reports/preview", json=self.report_request()
        ).json()
        self.assertEqual(data["summary"]["tracks"], 1)
        self.assertEqual(len(data["recordings"]), 1)

    def test_validation(self):
        self.assertEqual(
            self.client.put(
                "/api/ops/recordings/" + self.video["id"],
                json=dict(started_at_utc="2026-11-03T12:00:00"),
            ).status_code,
            422,
        )

        self.assertEqual(
            self.client.put(
                "/api/ops/settings", json=dict(timezone="invalid")
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/api/ops/schedules",
                json=dict(name="Monthly", cadence="monthly", timezone="invalid"),
            ).status_code,
            422,
        )

    def test_unscored_tracking_is_unknown_in_insights_and_reports(self):
        from unittest.mock import patch

        ops = self.app.state.operations
        run = ops.run(self.run)
        rows = ops.observations(run)
        run.update(kind="track", key="track:fixture", pickups=[])
        candidate = dict(
            key=run["key"],
            video_id=self.video["id"],
            created_at="2026-11-03T00:00:00+00:00",
        )
        with (
            patch.object(ops, "run", return_value=run),
            patch.object(ops, "observations", return_value=rows),
            patch.object(ops, "runs", return_value=[candidate]),
        ):
            data, _ = ops.insight(run["key"])
            self.assertIsNone(data["summary"]["pickups"])
            self.assertIsNone(data["tracks"][0]["pickups"])
            report = ops.preview(self.report_request())
            self.assertIsNone(report["summary"]["pickups"])
            self.assertIn("person tracking only", " ".join(report["warnings"]))

    def test_missing_live_artifact_is_excluded_and_evidence_is_real_png(self):
        image = self.client.get(
            "/api/ops/runs/" + self.run + "/tracks/local:1/evidence?media_ms=600"
        )
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.content.startswith(b"\x89PNG"))
        (self.folder / "observations.jsonl").unlink()
        self.assertFalse(
            any(r["key"] == self.run for r in self.app.state.operations.runs())
        )


if __name__ == "__main__":
    unittest.main()
