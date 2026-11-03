"""Internal unit scenarios, not replacement JSONL contract fixtures."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from app.analytics import Analytics
from app.config import load_config
from app.domain import Health, Observation, TrackKey
from app.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_config(ROOT / "configs/app.example.json")


def observation(time_ms, x, *, track="7", session="session-a", camera="camera", observed=True, record_id=None, y=50):
    return Observation(
        record_id or f"{camera}:{session}:{track}:{time_ms}",
        TrackKey("store", camera, session, track), time_ms, (x, y), observed,
        "2026-11-02T10:00:00Z",
    )


def trajectory():
    # Positions/times explicitly stated in the original contract README.
    # These Python objects do not claim the missing JSONL schema or fixture.
    return [observation(t, x) for t, x in zip(
        (0, 1000, 2000, 3000, 4000), (10, 40, 60, 70, 90)
    )]


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.addCleanup(self.store.close)
        self.engine = Analytics(self.store, CONFIG)

    def ingest(self, items):
        for item in items:
            self.engine.ingest(item)

    def closed(self):
        return [event["payload"] for event in self.store.events()
                if event["kind"] == "zone.visit_closed"]

    def test_documented_trajectory_inward_at_2000_and_2_second_dwell(self):
        self.ingest(trajectory())
        self.engine.finish()
        inward = [e for e in self.store.events() if e["kind"] == "entrance.inward"]
        self.assertEqual(len(inward), 1)
        self.assertEqual(inward[0]["media_time_ms"], 2000)
        self.assertEqual(self.closed()[0]["start_ms"], 2000)
        self.assertEqual(self.closed()[0]["end_ms"], 4000)
        self.assertEqual(self.closed()[0]["observed_dwell_ms"], 2000)
        self.assertTrue(self.closed()[0]["complete"])

    def test_exact_retry_changes_neither_events_nor_processing_times(self):
        samples = trajectory()
        self.ingest(samples)
        self.engine.finish()
        before = self.store.events()
        for sample in samples:
            self.assertFalse(self.engine.ingest(sample))
        self.engine.finish()
        self.assertEqual(self.store.events(), before)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM inputs").fetchone()[0], 5)

    def test_retry_conflict_is_rejected_without_altering_state(self):
        first = observation(0, 10)
        self.engine.ingest(first)
        with self.assertRaisesRegex(ValueError, "Conflicting payload"):
            self.engine.ingest(replace(first, point=(90, 50)))
        self.engine.ingest(observation(1000, 60))
        self.assertEqual(self.engine.summary()["inward_crossings"], 1)

    def test_repeated_time_with_new_id_is_rejected(self):
        self.engine.ingest(observation(0, 10))
        with self.assertRaisesRegex(ValueError, "Repeated"):
            self.engine.ingest(observation(0, 60, record_id="different"))
        self.assertEqual(self.engine.summary()["inward_crossings"], 0)

    def test_same_frame_time_for_different_tracks_is_allowed(self):
        self.engine.ingest(observation(0, 10))
        self.engine.ingest(observation(0, 60, track="8"))

    def test_out_of_order_across_tracks_is_rejected_atomically(self):
        self.engine.ingest(observation(1000, 10))
        with self.assertRaisesRegex(ValueError, "Out-of-order"):
            self.engine.ingest(observation(500, 60, track="8"))
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM inputs").fetchone()[0], 1)

    def test_hovering_on_line_does_not_create_extra_crossings(self):
        for t, x in enumerate((40, 49, 51, 48, 52, 54, 60, 51, 49, 60)):
            self.engine.ingest(observation(t * 100, x))
        self.assertEqual(self.engine.summary()["inward_crossings"], 1)
        self.assertEqual(self.engine.summary()["outward_crossings"], 0)

    def test_start_on_neutral_line_requires_two_stable_sides(self):
        for t, x in enumerate((50, 51, 60)):
            self.engine.ingest(observation(t * 100, x))
        self.assertEqual(self.engine.summary()["inward_crossings"], 0)

    def test_reverse_crossing_is_outward(self):
        self.ingest([observation(0, 90), observation(1000, 10)])
        self.assertEqual(self.engine.summary()["outward_crossings"], 1)

    def test_passing_beyond_line_segment_is_not_counted(self):
        self.ingest([observation(0, 10, y=110), observation(1000, 90, y=110)])
        self.assertEqual(self.engine.summary()["inward_crossings"], 0)

    def test_first_seen_inside_has_left_censored_dwell(self):
        self.ingest([observation(0, 60), observation(1000, 90)])
        visit = self.closed()[0]
        self.assertFalse(visit["complete"])
        self.assertFalse(visit["entry_observed"])
        self.assertTrue(visit["exit_observed"])
        self.assertEqual(visit["observed_dwell_ms"], 1000)

    def test_incomplete_track_at_end_is_censored(self):
        self.ingest([observation(0, 10), observation(1000, 60), observation(2000, 70)])
        self.engine.finish()
        visit = self.closed()[0]
        self.assertEqual(visit["observed_dwell_ms"], 1000)
        self.assertEqual(visit["reason"], "end_of_input")
        self.assertFalse(visit["complete"])
        before = self.store.events()
        self.engine.finish()
        self.assertEqual(self.store.events(), before)

    def test_long_gap_censors_and_does_not_infer_crossing(self):
        self.ingest([observation(0, 10), observation(2000, 60), observation(4000, 90)])
        self.assertEqual(self.engine.summary()["inward_crossings"], 0)
        visit = self.closed()[0]
        self.assertEqual(visit["reason"], "gap")
        self.assertEqual(visit["observed_dwell_ms"], 0)
        self.assertFalse(visit["complete"])

    def test_missing_track_expires_when_other_track_advances_time(self):
        self.ingest([observation(0, 60), observation(1000, 70),
                     observation(3000, 10, track="8")])
        self.assertEqual(self.closed()[0]["end_ms"], 1000)
        self.assertEqual(self.closed()[0]["observed_dwell_ms"], 1000)
        self.assertEqual(self.closed()[0]["reason"], "gap")

    def test_gap_equal_to_limit_is_allowed(self):
        self.ingest([observation(0, 10), observation(1500, 60)])
        self.assertEqual(self.engine.summary()["inward_crossings"], 1)

    def test_predicted_positions_never_enter_or_cross(self):
        self.ingest([observation(0, 10), observation(500, 60, observed=False),
                     observation(1000, 90)])
        self.assertEqual(self.engine.summary()["inward_crossings"], 1)
        self.assertEqual(len(self.closed()), 0)
        self.assertEqual(len(self.store.events()), 1)

    def test_predictions_do_not_extend_observation_coverage(self):
        self.ingest([observation(0, 60), observation(1000, 70, observed=False),
                     observation(2000, 90)])
        self.assertEqual(self.closed()[0]["end_ms"], 0)
        self.assertEqual(self.closed()[0]["observed_dwell_ms"], 0)

    def test_health_outage_censors_without_inventing_exit(self):
        self.ingest([observation(0, 10), observation(1000, 60),
                     Health("down", "store", "camera", "session-a", 1100, False)])
        self.assertEqual(self.closed()[0]["end_ms"], 1000)
        self.assertEqual(self.closed()[0]["reason"], "health")
        self.assertFalse(self.closed()[0]["exit_observed"])
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.engine.ingest(observation(1200, 90))
        self.engine.ingest(Health("up", "store", "camera", "session-a", 1300, True))
        self.engine.ingest(observation(1400, 90))
        self.assertEqual(self.engine.summary()["outward_crossings"], 0)

    def test_health_persistence_retries_and_conflicting_repeated_time(self):
        health = Health("down", "store", "camera", "session-a", 0, False)
        self.assertTrue(self.engine.ingest(health))
        before = dict(self.store.connection.execute("SELECT * FROM inputs").fetchone())
        self.assertEqual(before["kind"], "health")
        self.assertFalse(self.engine.ingest(health))
        self.assertEqual(dict(self.store.connection.execute("SELECT * FROM inputs").fetchone()), before)
        with self.assertRaisesRegex(ValueError, "Repeated health time"):
            self.engine.ingest(replace(health, record_id="up", available=True))

    def test_reconnect_resets_identity_and_retires_previous_session(self):
        self.ingest([observation(0, 10), observation(1000, 60),
                     observation(0, 70, session="session-b"),
                     observation(1000, 70, session="session-b")])
        self.assertEqual(self.engine.summary()["inward_crossings"], 1)
        visit = self.closed()[0]
        self.assertEqual(visit["reason"], "reconnect")
        self.assertFalse(visit["complete"])
        with self.assertRaisesRegex(ValueError, "retired"):
            self.engine.ingest(observation(2000, 90))
        self.assertFalse(self.engine.ingest(observation(0, 10)))

    def test_independent_cameras_do_not_share_clocks_or_ids(self):
        self.ingest([observation(1000, 10), observation(0, 60, camera="other")])
        self.assertEqual(self.engine.summary()["inward_crossings"], 0)

    def test_media_capture_processing_times_are_separate(self):
        self.engine.ingest(observation(1000, 60))
        row = self.store.connection.execute("SELECT * FROM inputs").fetchone()
        self.assertEqual(row["media_time_ms"], 1000)
        self.assertEqual(row["capture_time_utc"], "2026-11-02T10:00:00Z")
        self.assertNotEqual(row["processed_at_utc"], row["capture_time_utc"])
        self.assertTrue(row["processed_at_utc"].endswith("Z"))

    def test_unknown_capture_time_remains_null(self):
        self.engine.ingest(replace(observation(0, 60), capture_time_utc=None))
        row = self.store.connection.execute("SELECT capture_time_utc FROM inputs").fetchone()
        self.assertIsNone(row[0])

    def test_config_change_requires_new_database(self):
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            Analytics(self.store, replace(CONFIG, max_gap_ms=2000))

    def test_multiple_visits_and_crossings_have_distinct_ids(self):
        self.ingest([observation(t * 1000, x) for t, x in enumerate((10, 60, 90, 60, 10))])
        events = self.store.events()
        self.assertEqual(len(self.closed()), 2)
        self.assertEqual(len({e["event_id"] for e in events}), len(events))
        self.assertEqual(self.engine.summary()["complete_observed_dwell_ms"], 2000)


class RestartTests(unittest.TestCase):
    def test_state_survives_restart_and_replay_remains_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            samples = trajectory()
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                for sample in samples[:3]:
                    engine.ingest(sample)
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                for sample in samples:
                    engine.ingest(sample)
                engine.finish()
                before = store.events()
                self.assertEqual(engine.summary()["complete_observed_dwell_ms"], 2000)
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                for sample in samples:
                    self.assertFalse(engine.ingest(sample))
                engine.finish()
                self.assertEqual(store.events(), before)

    def test_injected_failure_rolls_back_input_events_and_state(self):
        with SQLiteStore(":memory:") as store:
            engine = Analytics(store, CONFIG)
            engine.ingest(observation(0, 10))
            from unittest.mock import patch
            with patch.object(store, "put_input", side_effect=RuntimeError("write failed")):
                with self.assertRaisesRegex(RuntimeError, "write failed"):
                    engine.ingest(observation(1000, 60))
            self.assertEqual(store.events(), [])
            engine.ingest(observation(1000, 60))
            self.assertEqual(engine.summary()["inward_crossings"], 1)

    def test_event_ids_ignore_wall_clock_time(self):
        from unittest.mock import patch
        event_sets = []
        for wall in ("2026-01-01T00:00:00Z", "2026-11-02T00:00:00Z"):
            with SQLiteStore(":memory:") as store:
                engine = Analytics(store, CONFIG)
                with patch("app.storage.processing_time", return_value=wall):
                    for sample in trajectory():
                        engine.ingest(sample)
                event_sets.append([{k: v for k, v in event.items() if k != "processed_at_utc"}
                                   for event in store.events()])
        self.assertEqual(*event_sets)

    def test_unfinished_visit_and_health_recovery_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            down = Health("down", "store", "camera", "session-a", 1100, False)
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                engine.ingest(observation(0, 10))
                engine.ingest(observation(1000, 60))
                engine.ingest(down)
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                self.assertFalse(engine.ingest(down))
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    engine.ingest(observation(1200, 90))
                engine.ingest(Health("up", "store", "camera", "session-a", 1300, True))
                engine.ingest(observation(1400, 90))
                self.assertEqual(engine.summary()["inward_crossings"], 1)
                self.assertEqual(engine.summary()["complete_visits"], 0)
                self.assertEqual(engine.summary()["censored_visits"], 1)
