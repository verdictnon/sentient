"""Application-authored queue scenarios using the real original track schema."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.replay import replay
from app.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[2]
BASE = json.loads((ROOT / "contracts/v1/examples.jsonl").read_text(encoding="utf-8").splitlines()[0])
CONFIG = ROOT / "configs/app.queue.example.json"


def tracks(points, *, session="queue-session"):
    result = []
    for index, (time, x) in enumerate(points):
        row = deepcopy(BASE)
        row.update(record_id=f"queue:{session}:{index}", stream_session_id=session,
                   media_time_ms=time, frame_index=index,
                   bbox_xyxy_px=[x - 5, 20, x + 5, 80])
        result.append(row)
    return result


class QueueReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.input = self.path / "queue.jsonl"
        self.db = self.path / "queue.sqlite"

    def run_rows(self, rows):
        self.input.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        return replay(self.input, CONFIG, self.db)

    def visits(self):
        with SQLiteStore(self.db) as store:
            return [e for e in store.events() if e["kind"] == "queue.visit_closed"]

    def test_configured_queue_service_completion_and_double_replay(self):
        rows = tracks([(0, 10), (1000, 30), (2000, 40), (3000, 60), (4000, 70), (5000, 90)])
        first = self.run_rows(rows)
        self.assertEqual(first["queue_completed_visits"], 1)
        self.assertEqual(first["queue_completed_waiting_ms"], 2000)
        self.assertEqual(self.visits()[0]["payload"]["service_ms"], 2000)
        before = self.visits()
        second = self.run_rows(rows)
        self.assertEqual(second["events_added"], 0)
        self.assertEqual(second["records_inserted"], 0)
        self.assertEqual(self.visits(), before)

    def test_configured_queue_abandonment_is_not_a_completed_wait(self):
        summary = self.run_rows(tracks([(0, 10), (1000, 30), (2000, 40), (3000, 10)]))
        self.assertEqual(summary["queue_abandoned_visits"], 1)
        self.assertEqual(summary["queue_completed_waits"], 0)
        self.assertEqual(self.visits()[0]["payload"]["observed_waiting_ms"], 2000)

    def test_incomplete_queue_track_is_censored_on_eof(self):
        summary = self.run_rows(tracks([(0, 10), (1000, 30), (2000, 40)]))
        self.assertEqual(summary["queue_censored_visits"], 1)
        self.assertEqual(summary["queue_abandoned_visits"], 0)
        self.assertEqual(self.visits()[0]["payload"]["end_ms"], 2000)

    def test_gap_and_new_stream_session_do_not_complete_a_wait(self):
        rows = tracks([(0, 10), (1000, 30), (3000, 40)]) + tracks([(0, 30)], session="reconnected")
        summary = self.run_rows(rows)
        self.assertEqual(summary["queue_completed_waits"], 0)
        self.assertEqual(summary["queue_censored_visits"], 3)
        self.assertEqual([v["payload"]["reason"] for v in self.visits()], ["gap", "reconnect", "end_of_input"])

    def test_fractional_queue_wait_is_not_rounded(self):
        self.run_rows(tracks([(0.125, 10), (100.25, 30), (250.5, 60), (500.75, 90)]))
        visit = self.visits()[0]["payload"]
        self.assertEqual(visit["waiting_ms"], 150.25)
        self.assertEqual(visit["service_ms"], 250.25)

    def test_wire_line_jitter_counts_once_only_after_stable_side(self):
        rows = tracks(list(enumerate((40, 49, 51, 48, 52, 54, 60, 51, 49, 60))))
        summary = self.run_rows(rows)
        self.assertEqual(summary["inward_crossings"], 1)
        self.assertEqual(summary["outward_crossings"], 0)
        with SQLiteStore(self.db) as store:
            inward = [e for e in store.events() if e["kind"] == "entrance.inward"]
        self.assertEqual(inward[0]["media_time_ms"], 6)
