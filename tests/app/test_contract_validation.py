"""Mutations of real schema-valid records test independent rejection policies."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.adapter import ContractAdapter
from app.config import load_replay_config
from app.contracts import ContractError, ContractValidator, strict_json
from app.replay import replay
from app.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "contracts/v1/examples.jsonl"
CONFIG = ROOT / "configs/app.example.json"
ORIGINALS = [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines() if line.strip()]


def record(index=0, **updates):
    row = deepcopy(ORIGINALS[index])
    row.update(updates)
    return row


class StructuralSemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = ContractValidator()

    def rejected(self, row):
        with self.assertRaises(ContractError):
            self.validator.validate(row)

    def test_wrong_version_unknown_type_missing_fields_extra_fields_and_boolean_numbers(self):
        for changes in ({"schema_version": "2.0"}, {"type": "person"}, {"record_id": ""},
                        {"unexpected": 1}, {"frame_index": True}, {"media_time_ms": False}):
            with self.subTest(changes=changes):
                self.rejected(record(**changes))
        row = record()
        del row["local_track_id"]
        self.rejected(row)

    def test_box_inversions_zero_area_and_out_of_image_bounds(self):
        for box in ([20, 20, 10, 80], [10, 20, 10, 80], [0, 80, 20, 20],
                    [-1, 20, 20, 80], [0, 20, 101, 80], [0, 20, 20, 101]):
            with self.subTest(box=box):
                self.rejected(record(bbox_xyxy_px=box))

    def test_numeric_values_must_be_finite(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            self.rejected(record(media_time_ms=value))
            self.rejected(record(bbox_xyxy_px=[0, 20, value, 80]))
            row = record(8)
            row["scores"]["take"] = value
            self.rejected(row)

    def test_verified_capture_time_required_and_format_checked(self):
        self.rejected(record(clock_status="verified"))
        for value in ("2026-13-01T00:00:00Z", "not-a-dateZ", "2026-11-02T00:00:00+00:00"):
            self.rejected(record(capture_time_utc=value))
        self.validator.validate(record(clock_status="verified", capture_time_utc="2026-11-02T00:00:00.125Z"))

    def test_world_position_requires_calibration_and_confirmed_identity_needs_evidence(self):
        self.rejected(record(world_xy_m=[1, 2]))
        self.rejected(record(association_status="confirmed", global_visit_id="unsupported-global"))
        self.validator.validate(record(world_xy_m=[1, 2], calibration_version="cal-1"))

    def test_candidate_array_length_order_and_window_bounds(self):
        for changes in (
            {"start_media_ms": 4000}, {"frame_media_times_ms": [1000, 2000]},
            {"frame_indices": [1, 3, 2]}, {"frame_media_times_ms": [1000, 1000, 3000]},
            {"frame_media_times_ms": [0, 2000, 3000]}, {"frame_media_times_ms": [1000, 2000, 4000]},
        ):
            with self.subTest(changes=changes):
                self.rejected(record(7, **changes))

    def test_capture_elapsed_measurements_are_required_when_sampling_known(self):
        self.rejected(record(7, sampling_time_known=True))
        self.rejected(record(7, capture_elapsed_ms=[0, 100]))
        self.rejected(record(7, capture_elapsed_ms=[0, 100, 50]))
        self.validator.validate(record(7, sampling_time_known=True, capture_elapsed_ms=[0, 1000, 2000]))

    def test_softmax_semantics_abstention_and_null_scores(self):
        self.rejected(record(8, action="take"))
        self.rejected(record(8, scores={"take": .4, "put": .2, "touch": .1, "background": None}))
        self.rejected(record(8, scores={"take": .4, "put": .2, "touch": .1, "background": .4}))
        self.rejected(record(8, scores=None))
        self.rejected(record(8, score_semantics="unavailable"))
        self.validator.validate(record(8, scores=None, score_semantics="unavailable"))

    def test_independent_sigmoid_scores_are_not_forced_to_sum_to_one(self):
        self.validator.validate(record(8, score_semantics="sigmoid_multi_label",
                                       scores={"take": .8, "put": .8, "touch": .8, "background": None}))

    def test_strict_json_rejects_duplicate_properties_and_nonfinite_constants(self):
        for text in ('{"record_id":"a","record_id":"b"}', '{"score":NaN}', '{"score":Infinity}'):
            with self.assertRaises(ContractError):
                strict_json(text)


class StatefulContractTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.addCleanup(self.store.close)
        self.adapter = ContractAdapter(self.store, load_replay_config(CONFIG), ContractValidator())

    def test_original_payload_conflict_in_unused_metadata_is_not_hidden_by_normalization(self):
        self.adapter.ingest(record())
        with self.assertRaisesRegex(ValueError, "Conflicting payload"):
            self.adapter.ingest(record(model_id="different-worker"))
        self.assertEqual(len(self.store.inputs()), 1)

    def test_exact_retry_does_not_recheck_historical_frame_order(self):
        for row in ORIGINALS:
            self.adapter.ingest(row)
        before = self.store.events(), self.store.inputs(), self.store.states("wire_frame")
        for row in ORIGINALS:
            self.assertFalse(self.adapter.ingest(row))
        self.assertEqual((self.store.events(), self.store.inputs(), self.store.states("wire_frame")), before)

    def test_new_id_for_repeated_or_decreasing_track_frame_is_rejected(self):
        self.adapter.ingest(record(1))
        for changes in ({"frame_index": 1, "media_time_ms": 2000},
                        {"frame_index": 2, "media_time_ms": 1000},
                        {"frame_index": 0, "media_time_ms": 2000}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.adapter.ingest(record(2, record_id="new", **changes))

    def test_same_frame_different_local_tracks_allowed_without_merging(self):
        self.adapter.ingest(record())
        self.adapter.ingest(record(record_id="other-track", local_track_id="8"))
        self.assertEqual(len(self.store.states("track")), 2)
        self.assertEqual(self.adapter.engine.summary()["inward_crossings"], 0)

    def test_same_frame_requires_consistent_time_and_dimensions_across_tracks(self):
        self.adapter.ingest(record())
        for changes in ({"media_time_ms": 1}, {"frame_dims_px": [101, 100]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.adapter.ingest(record(record_id="other-track", local_track_id="8", **changes))

    def test_stream_frame_order_is_enforced_across_local_tracks(self):
        self.adapter.ingest(record(2))
        with self.assertRaisesRegex(ValueError, "Out-of-order"):
            self.adapter.ingest(record(1, record_id="other-track", local_track_id="8", media_time_ms=3000))

    def test_independent_camera_clocks_are_not_compared(self):
        self.adapter.ingest(record(2))
        self.adapter.ingest(record(record_id="other-camera", camera_id="camera-b"))
        self.assertEqual(len(self.store.inputs()), 2)

    def test_terminal_health_requires_a_new_session_and_retries_are_safe(self):
        for row in ORIGINALS[:6]:
            self.adapter.ingest(row)
        with self.assertRaisesRegex(ValueError, "retired"):
            self.adapter.ingest(record(4, record_id="after-end", media_time_ms=5000, frame_index=5))
        self.assertFalse(self.adapter.ingest(ORIGINALS[5]))
        self.adapter.ingest(ORIGINALS[6])
        self.assertEqual(self.store.get_state("camera", ["demo-store", "camera-a"]), "session-b")

    def test_degraded_and_dropped_frame_health_breaks_coverage_but_allows_new_observations(self):
        for state, dropped in (("degraded", 0), ("healthy", 2)):
            with self.subTest(state=state), SQLiteStore(":memory:") as store:
                adapter = ContractAdapter(store, load_replay_config(CONFIG), ContractValidator())
                adapter.ingest(record(1))
                adapter.ingest(record(2))
                adapter.ingest(record(5, state=state, dropped_frames=dropped, media_time_ms=2100))
                adapter.ingest(record(3))
                adapter.ingest(record(4))
                closed = [e["payload"] for e in store.events() if e["kind"] == "zone.visit_closed"]
                self.assertEqual(len(closed), 2)
                self.assertTrue(all(not v["complete"] for v in closed))
                self.assertEqual(closed[0]["end_ms"], 2000)

    def test_disconnected_session_cannot_resume_under_the_same_identity(self):
        self.adapter.ingest(record())
        self.adapter.ingest(record(5, state="disconnected", media_time_ms=100))
        with self.assertRaisesRegex(ValueError, "retired"):
            self.adapter.ingest(record(5, record_id="illegal-reconnect", state="healthy", media_time_ms=200))
        self.adapter.ingest(record(6))

    def test_fractional_media_time_and_actual_capture_utc_are_preserved(self):
        self.adapter.ingest(record(media_time_ms=.125, capture_time_utc="2026-11-02T00:00:00.125Z", clock_status="verified"))
        row = self.store.inputs()[0]
        self.assertEqual(row["media_time_ms"], .125)
        self.assertEqual(row["capture_time_utc"], "2026-11-02T00:00:00.125Z")
        self.assertEqual(row["payload"]["media_time_ms"], .125)
        self.assertNotEqual(row["processed_at_utc"], row["capture_time_utc"])

    def test_candidate_identity_cannot_be_renamed_to_create_a_retry(self):
        self.adapter.ingest(record(7))
        with self.assertRaisesRegex(ContractError, "candidate_id already"):
            self.adapter.ingest(record(7, record_id="unstable-candidate-record-id"))

    def test_result_requires_candidate_and_matching_source_track_and_span(self):
        with self.assertRaisesRegex(ContractError, "unavailable candidate"):
            self.adapter.ingest(record(8))
        self.adapter.ingest(record(7))
        for changes in ({"local_track_id": "8"}, {"camera_id": "other-camera"},
                        {"stream_session_id": "session-b"}, {"global_visit_id": "other-identity"},
                        {"start_media_ms": 0}, {"start_media_ms": 1500}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                self.adapter.ingest(record(8, **changes))

    def test_temporal_spans_and_different_models_have_independent_result_identity(self):
        self.adapter.ingest(record(7))
        for i, (model, start, end) in enumerate((("m1", 1000, 2000), ("m1", 2000, 3000), ("m2", 1000, 2000))):
            self.adapter.ingest(record(8, record_id=f"temporal-{i}", model_id=model,
                                       task="temporal_detection", start_media_ms=start, end_media_ms=end))
        with self.assertRaisesRegex(ContractError, "Worker result identity"):
            self.adapter.ingest(record(8, record_id="renamed", model_id="m1", task="temporal_detection",
                                       start_media_ms=1000, end_media_ms=2000))
        self.assertEqual(len(self.store.states("wire_result")), 3)

    def test_delayed_candidates_and_model_scores_never_reopen_old_session_or_confirm_actions(self):
        for row in ORIGINALS[:7]:
            self.adapter.ingest(row)
        self.adapter.ingest(record(7))
        self.adapter.ingest(record(8, quality="valid", action="take", abstained=False))
        self.assertEqual(self.store.get_state("camera", ["demo-store", "camera-a"]), "session-b")
        self.assertTrue(self.store.get_state("stream", ["demo-store", "camera-a", "session-a"])["retired"])
        self.assertFalse(any(e["kind"].startswith("action.") for e in self.store.events()))


class ReplayBatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.input = self.path / "test.jsonl"
        self.db = self.path / "test.sqlite"

    def write(self, rows):
        self.input.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    def test_duplicates_inside_one_file_are_idempotent(self):
        self.write([row for row in ORIGINALS for _ in range(2)])
        summary = replay(self.input, CONFIG, self.db)
        self.assertEqual(summary["records_read"], 18)
        self.assertEqual(summary["records_inserted"], 9)
        self.assertEqual(summary["records_duplicate"], 9)
        self.assertEqual(summary["events_total"], 3)

    def test_structural_error_reports_line_and_does_not_create_database(self):
        self.write([record(), record(1, bbox_xyxy_px=[0, 0, 0, 0])])
        with self.assertRaisesRegex(ContractError, r"test.jsonl:2"):
            replay(self.input, CONFIG, self.db)
        self.assertFalse(self.db.exists())

    def test_stateful_error_rolls_back_the_entire_new_batch(self):
        self.write([record(1), record()])
        with self.assertRaises(ContractError):
            replay(self.input, CONFIG, self.db)
        with SQLiteStore(self.db) as store:
            self.assertEqual(store.inputs(), [])
            self.assertEqual(store.events(), [])
            self.assertEqual(store.states("config"), [])

    def test_conflicting_retry_after_other_valid_records_preserves_prior_history(self):
        replay(INPUT, CONFIG, self.db)
        with SQLiteStore(self.db) as store:
            before = store.inputs(), store.events(), store.states("wire_candidate")
        self.write([record(7, candidate_id="new-candidate", record_id="new-request"), record(model_id="conflict")])
        with self.assertRaises(ContractError):
            replay(self.input, CONFIG, self.db)
        with SQLiteStore(self.db) as store:
            self.assertEqual((store.inputs(), store.events(), store.states("wire_candidate")), before)

    def test_malformed_json_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        for bad in ('{', '{"type":"track.observed","type":"stream.health"}', '{"media_time_ms":1e309}'):
            self.input.write_text(bad + "\n", encoding="utf-8")
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                replay(self.input, CONFIG, self.db)
        self.assertFalse(self.db.exists())

    def test_blank_lines_and_utf8_bom_do_not_change_records(self):
        self.input.write_text("\ufeff\n" + INPUT.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
        self.assertEqual(replay(self.input, CONFIG, self.db)["database_records"], 9)

    def test_no_finalize_preserves_an_open_visit_across_batches_and_restart(self):
        self.write(ORIGINALS[:3])
        first = replay(self.input, CONFIG, self.db, finalize=False)
        self.assertEqual(first["complete_visits"], 0)
        self.write(ORIGINALS[3:])
        second = replay(self.input, CONFIG, self.db)
        self.assertEqual(second["complete_observed_dwell_seconds"], 2.0)
        self.assertEqual(second["inward_crossings"], 1)

    def test_changed_queue_configuration_is_rejected_without_altering_history(self):
        replay(INPUT, CONFIG, self.db)
        with self.assertRaisesRegex(ContractError, "configuration changed"):
            replay(INPUT, ROOT / "configs/app.queue.example.json", self.db)
