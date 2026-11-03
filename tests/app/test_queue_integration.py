"""Internal membership scenarios; no missing external schema/fixture is assumed."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.analytics import Analytics
from app.config import load_config
from app.domain import Health, Observation, QueuePosition, TrackKey
from app.queues import QueueVisit
from app.storage import SQLiteStore

CONFIG = load_config(Path(__file__).resolve().parents[2] / "configs/app.example.json")
OUTSIDE = QueuePosition(False, False)
WAITING = QueuePosition(True, False)
SERVING = QueuePosition(False, True)


def sample(time, position, *, session="a", track="7", observed=True, record_id=None):
    return Observation(
        record_id or f"{session}:{track}:{time}", TrackKey("store", "camera", session, track),
        time, (10, 50), observed=observed, queue_position=position,
    )


def completed_visit():
    return [sample(t, p) for t, p in (
        (0, OUTSIDE), (1000, WAITING), (2000, WAITING),
        (3000, SERVING), (4000, SERVING), (5000, OUTSIDE),
    )]


class QueueIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.addCleanup(self.store.close)
        self.engine = Analytics(self.store, CONFIG)

    def ingest(self, items):
        for item in items:
            self.engine.ingest(item)

    def closed(self):
        return [e for e in self.store.events() if e["kind"] == "queue.visit_closed"]

    def test_service_completion_persists_durations_and_source_times(self):
        self.ingest(completed_visit())
        events = [e for e in self.store.events() if e["kind"].startswith("queue.")]
        self.assertEqual([e["kind"] for e in events], [
            "queue.visit_started", "queue.service_started", "queue.visit_closed",
        ])
        self.assertEqual([e["media_time_ms"] for e in events], [1000, 3000, 5000])
        result = self.closed()[0]["payload"]
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["waiting_ms"], 2000)
        self.assertEqual(result["service_ms"], 2000)
        self.assertTrue(result["observation_complete"])
        self.assertTrue(result["waiting_complete"])
        self.assertTrue(result["service_complete"])
        self.assertEqual(self.engine.summary()["queue_completed_waiting_ms"], 2000)

    def test_abandonment_is_distinct_from_completed_wait(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, WAITING), sample(2000, OUTSIDE)])
        result = self.closed()[0]["payload"]
        self.assertEqual(result["outcome"], "abandoned")
        self.assertEqual(result["observed_waiting_ms"], 1000)
        self.assertIsNone(result["waiting_ms"])
        self.assertEqual(self.engine.summary()["queue_completed_waits"], 0)
        self.assertEqual(self.engine.summary()["queue_abandoned_visits"], 1)

    def test_summary_separates_open_waits_and_reports_wait_at_service_start(self):
        self.ingest(completed_visit()[:2])
        summary = self.engine.summary()
        self.assertEqual(summary["queue_open_waits"], 1)
        self.assertEqual(summary["queue_completed_waits"], 0)
        self.ingest(completed_visit()[2:4])
        summary = self.engine.summary()
        self.assertEqual(summary["queue_open_waits"], 0)
        self.assertEqual(summary["queue_open_service_visits"], 1)
        self.assertEqual(summary["queue_completed_waits"], 1)
        self.assertEqual(summary["queue_completed_waiting_ms"], 2000)
        self.assertEqual(summary["queue_completed_visits"], 0)

    def test_eof_censors_wait_at_last_known_sample_and_is_idempotent(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, WAITING), sample(2000, WAITING)])
        self.engine.finish()
        before = self.store.events()
        self.engine.finish()
        self.assertEqual(self.store.events(), before)
        result = self.closed()[0]["payload"]
        self.assertEqual(result["outcome"], "censored")
        self.assertEqual(result["end_ms"], 2000)
        self.assertEqual(result["reason"], "end_of_input")
        self.assertIsNone(result["waiting_ms"])

    def test_first_seen_waiting_does_not_invent_complete_wait(self):
        self.ingest([sample(0, WAITING), sample(1000, SERVING), sample(2000, OUTSIDE)])
        result = self.closed()[0]["payload"]
        self.assertFalse(result["entry_observed"])
        self.assertIsNone(result["waiting_ms"])
        self.assertEqual(result["observed_waiting_ms"], 1000)
        self.assertEqual(result["service_ms"], 1000)

    def test_first_seen_serving_keeps_service_start_censored(self):
        self.ingest([sample(0, SERVING), sample(1000, OUTSIDE)])
        result = self.closed()[0]["payload"]
        self.assertFalse(result["service_start_observed"])
        self.assertIsNone(result["waiting_ms"])
        self.assertIsNone(result["service_ms"])
        self.assertEqual(result["observed_service_ms"], 1000)
        self.assertFalse(result["observation_complete"])
        self.assertFalse(result["waiting_complete"])
        self.assertFalse(result["service_complete"])

    def test_direct_outside_to_service_does_not_invent_zero_wait(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, SERVING), sample(2000, OUTSIDE)])
        result = self.closed()[0]["payload"]
        self.assertIsNone(result["waiting_ms"])
        self.assertTrue(result["service_start_observed"])
        self.assertEqual(result["service_ms"], 1000)

    def test_absent_membership_is_unknown_not_outside(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, WAITING), sample(2000, None)])
        self.assertEqual(self.closed(), [])
        self.engine.ingest(sample(3000, None))
        result = self.closed()[0]["payload"]
        self.assertEqual(result["reason"], "gap")
        self.assertEqual(result["end_ms"], 1000)
        self.assertEqual(result["outcome"], "censored")

    def test_prior_generic_observation_does_not_establish_queue_entry(self):
        self.ingest([sample(0, None), sample(1000, WAITING), sample(2000, SERVING), sample(3000, OUTSIDE)])
        self.assertFalse(self.closed()[0]["payload"]["entry_observed"])

    def test_predictions_do_not_start_service_or_depart(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, WAITING),
                     sample(1500, SERVING, observed=False), sample(2000, OUTSIDE, observed=False)])
        self.assertEqual(self.closed(), [])
        self.assertFalse(any(e["kind"] == "queue.service_started" for e in self.store.events()))
        self.engine.finish()
        self.assertEqual(self.closed()[0]["payload"]["end_ms"], 1000)

    def test_queue_gap_does_not_break_independently_observed_line(self):
        samples = [sample(0, OUTSIDE), sample(1000, WAITING), sample(2000, None), sample(3000, None)]
        samples[-1] = replace(samples[-1], point=(60, 50))
        self.ingest(samples)
        self.assertEqual(self.engine.summary()["inward_crossings"], 1)
        self.assertEqual(self.closed()[0]["payload"]["outcome"], "censored")

    def test_long_gap_censors_old_visit_and_new_visit_is_partial(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, WAITING), sample(3000, WAITING), sample(4000, OUTSIDE)])
        results = [e["payload"] for e in self.closed()]
        self.assertEqual([r["outcome"] for r in results], ["censored", "abandoned"])
        self.assertEqual(results[0]["end_ms"], 1000)
        self.assertFalse(results[1]["entry_observed"])

    def test_health_outage_during_service_is_not_completion(self):
        self.ingest(completed_visit()[:4])
        self.engine.ingest(Health("down", "store", "camera", "a", 3100, False))
        result = self.closed()[0]["payload"]
        self.assertEqual(result["outcome"], "censored")
        self.assertEqual(result["reason"], "health")
        self.assertEqual(result["end_ms"], 3000)
        self.assertEqual(result["waiting_ms"], 2000)
        self.assertIsNone(result["service_ms"])
        self.assertTrue(result["waiting_complete"])
        self.assertFalse(result["observation_complete"])
        self.assertFalse(result["service_complete"])

    def test_reconnect_censors_old_id_and_new_id_is_separate(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, WAITING),
                     sample(0, WAITING, session="b"), sample(1000, OUTSIDE, session="b")])
        events = self.closed()
        self.assertEqual([e["track_key"][2] for e in events], ["a", "b"])
        self.assertEqual(events[0]["payload"]["reason"], "reconnect")
        self.assertFalse(events[1]["payload"]["entry_observed"])
        self.assertNotEqual(events[0]["event_id"], events[1]["event_id"])
        before = self.store.events()
        self.assertFalse(self.engine.ingest(sample(1000, WAITING)))
        self.assertEqual(self.store.events(), before)

    def test_multiple_visits_have_unique_events_and_no_repeated_service_start(self):
        self.ingest(completed_visit())
        self.ingest([sample(6000, WAITING), sample(7000, SERVING),
                     sample(8000, WAITING), sample(9000, SERVING), sample(10000, OUTSIDE)])
        queue_events = [e for e in self.store.events() if e["kind"].startswith("queue.")]
        self.assertEqual(len(queue_events), 6)
        self.assertEqual(len({e["event_id"] for e in queue_events}), 6)

    def test_overlapping_memberships_prioritize_service(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, WAITING),
                     sample(2000, QueuePosition(True, True)), sample(3000, OUTSIDE)])
        self.assertEqual(self.closed()[0]["payload"]["waiting_ms"], 1000)

    def test_membership_conflict_and_repeated_time_are_rejected_atomically(self):
        first = sample(0, WAITING)
        self.engine.ingest(first)
        before = self.store.events()
        with self.assertRaisesRegex(ValueError, "Conflicting payload"):
            self.engine.ingest(replace(first, queue_position=SERVING))
        with self.assertRaisesRegex(ValueError, "Repeated"):
            self.engine.ingest(sample(0, OUTSIDE, record_id="different"))
        self.assertEqual(self.store.events(), before)

    def test_queue_write_failure_rolls_back_lifecycle_and_can_retry(self):
        self.ingest([sample(0, OUTSIDE), sample(1000, WAITING)])
        before = self.store.events()
        with patch.object(self.store, "put_input", side_effect=RuntimeError("disk failure")):
            with self.assertRaisesRegex(RuntimeError, "disk failure"):
                self.engine.ingest(sample(2000, SERVING))
        self.assertEqual(self.store.events(), before)
        self.engine.ingest(sample(2000, SERVING))
        self.engine.ingest(sample(3000, OUTSIDE))
        self.assertEqual(self.closed()[0]["payload"]["waiting_ms"], 1000)


class QueueDurabilityTests(unittest.TestCase):
    def test_reconnect_and_gap_history_remains_immutable_on_restart_and_retry(self):
        items = [sample(0, OUTSIDE), sample(1000, WAITING), sample(3000, WAITING),
                 sample(0, SERVING, session="b"), sample(1000, OUTSIDE, session="b")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                for item in items:
                    engine.ingest(item)
                engine.finish()
                before = store.events()
                closed = [e["payload"] for e in before if e["kind"] == "queue.visit_closed"]
                self.assertEqual([v["reason"] for v in closed], ["gap", "reconnect", "departure"])
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                for item in items:
                    self.assertFalse(engine.ingest(item))
                engine.finish()
                self.assertEqual(store.events(), before)

    def test_restart_during_wait_and_service_and_double_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.sqlite"
            samples = completed_visit()
            for stop in (2, 4, 6):
                with SQLiteStore(path) as store:
                    engine = Analytics(store, CONFIG)
                    for item in samples[:stop]:
                        engine.ingest(item)
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                engine.finish()
                before = store.events()
                for item in samples:
                    self.assertFalse(engine.ingest(item))
                engine.finish()
                self.assertEqual(store.events(), before)
                self.assertEqual(engine.summary()["queue_completed_visits"], 1)
                self.assertEqual(engine.summary()["queue_completed_waiting_ms"], 2000)

    def test_event_ids_and_payloads_ignore_processing_clock(self):
        runs = []
        for wall in ("2026-01-01T00:00:00Z", "2026-11-02T00:00:00Z"):
            with SQLiteStore(":memory:") as store, patch("app.storage.processing_time", return_value=wall):
                engine = Analytics(store, CONFIG)
                for item in completed_visit():
                    engine.ingest(item)
                runs.append([{k: v for k, v in e.items() if k != "processed_at_utc"} for e in store.events()])
        self.assertEqual(*runs)

    def test_gap_event_ids_do_not_depend_on_unrelated_track_order(self):
        runs = []
        for order in (("8", "9"), ("9", "8")):
            with SQLiteStore(":memory:") as store:
                engine = Analytics(store, CONFIG)
                engine.ingest(replace(sample(0, OUTSIDE), point=(10, 50)))
                engine.ingest(replace(sample(1000, WAITING), point=(60, 50)))
                for track in order:
                    engine.ingest(sample(3000, None, track=track))
                runs.append([{k: v for k, v in e.items() if k != "processed_at_utc"}
                             for e in store.events() if e["kind"].endswith("visit_closed")])
        self.assertEqual(*runs)
        self.assertEqual(len(runs[0]), 2)

    def test_active_queue_state_round_trip_preserves_censoring(self):
        visit = QueueVisit(1000, entry_observed=False)
        visit.start_service(2000, start_observed=False)
        restored = QueueVisit.from_state(visit.to_state())
        result = restored.end(3000, departure_observed=True)
        self.assertIsNone(result["waiting_ms"])
        self.assertIsNone(result["service_ms"])
        self.assertEqual(result["observed_service_ms"], 1000)
