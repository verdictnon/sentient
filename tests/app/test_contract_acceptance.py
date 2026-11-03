"""Real acceptance against the unmodified original v1 handoff."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.contracts import ContractValidator
from app.replay import replay
from app.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "contracts/v1/examples.jsonl"
CONFIG = ROOT / "configs/app.example.json"


class ContractAcceptanceTests(unittest.TestCase):
    def test_supplied_fixture_crossing_and_dwell(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "real.sqlite"
            summary = replay(INPUT, CONFIG, db)
            self.assertEqual(summary["records_inserted"], 9)
            self.assertEqual(summary["inward_crossings"], 1)
            self.assertEqual(summary["outward_crossings"], 0)
            self.assertEqual(summary["complete_observed_dwell_seconds"], 2.0)
            self.assertEqual(summary["confirmed_physical_actions"], 0)
            with SQLiteStore(db) as store:
                events, inputs = store.events(), store.inputs()
                inward = [e for e in events if e["kind"] == "entrance.inward"]
                self.assertEqual(len(inward), 1)
                self.assertEqual(inward[0]["media_time_ms"], 2000)
                visit = next(e["payload"] for e in events if e["kind"] == "zone.visit_closed")
                self.assertEqual((visit["start_ms"], visit["end_ms"], visit["observed_dwell_ms"]), (2000, 4000, 2000))
                self.assertTrue(visit["complete"])
                a = store.get_state("track", ["demo-store", "camera-a", "session-a", "7"])
                b = store.get_state("track", ["demo-store", "camera-a", "session-b", "7"])
                self.assertIsNotNone(a)
                self.assertIsNotNone(b)
                self.assertFalse(any(e["track_key"][2] == "session-b" for e in events))
                self.assertEqual(store.get_state("camera", ["demo-store", "camera-a"]), "session-b")
                originals = [record for _, record in ContractValidator().read_jsonl(INPUT)]
                self.assertEqual([row["payload"] for row in inputs], originals)
                self.assertTrue(all(row["capture_time_utc"] is None for row in inputs))
                self.assertTrue(all(row["processed_at_utc"].endswith("Z") for row in inputs))
                b_payload = next(r["payload"] for r in inputs if r["record_id"] == "obs-b-0")
                self.assertIsNone(b_payload["global_visit_id"])

    def test_supplied_fixture_double_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "real.sqlite"
            command = [sys.executable, "-m", "app.replay", "--input", str(INPUT),
                       "--config", str(CONFIG), "--db", str(db)]
            first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            with SQLiteStore(db) as store:
                before = store.events(), store.inputs(), store.states("track")
            second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            report = json.loads(second.stdout)
            self.assertEqual(report["records_inserted"], 0)
            self.assertEqual(report["records_duplicate"], 9)
            self.assertEqual(report["database_records"], 9)
            self.assertEqual(report["events_added"], 0)
            self.assertEqual(report["events_total"], 3)
            with SQLiteStore(db) as store:
                self.assertEqual((store.events(), store.inputs(), store.states("track")), before)

    def test_versioned_input_contract_validation(self):
        result = subprocess.run([sys.executable, "scripts/validate_handoff.py"], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("9 examples pass", result.stdout)
        records = ContractValidator().read_jsonl(INPUT)
        self.assertEqual(len(records), 9)

    def test_original_fixture_event_ids_are_identical_on_fresh_databases(self):
        runs = []
        with tempfile.TemporaryDirectory() as directory:
            for index, wall in enumerate(("2026-01-01T00:00:00Z", "2026-11-02T00:00:00Z")):
                db = Path(directory) / f"fresh-{index}.sqlite"
                with patch("app.storage.processing_time", return_value=wall):
                    replay(INPUT, CONFIG, db)
                with SQLiteStore(db) as store:
                    runs.append([{key: value for key, value in event.items() if key != "processed_at_utc"}
                                 for event in store.events()])
        self.assertEqual(*runs)
