"""Map validated original v1 records into the existing analytics and storage."""

from dataclasses import asdict

from app.analytics import Analytics
from app.config import ReplayConfig
from app.contracts import ContractError, ContractValidator
from app.domain import Health, Observation, QueuePosition, TrackKey
from app.storage import SQLiteStore, canonical, stable_id


def source_key(row: dict) -> tuple[str, str, str]:
    return row["store_id"], row["camera_id"], row["stream_session_id"]


class ContractAdapter:
    def __init__(self, store: SQLiteStore, config: ReplayConfig, validator: ContractValidator):
        self.store, self.config, self.validator = store, config, validator
        with store.atomic():
            binding = {"schema_sha256": stable_id(validator.schema), "config": asdict(config)}
            previous = store.get_state("config", "wire_replay")
            if previous is None and store.has_history():
                raise ContractError("Wire replay requires a fresh database; existing history has no contract binding")
            if previous is not None and canonical(previous) != canonical(binding):
                raise ContractError("Replay schema/configuration changed; use a new database")
            self.engine = Analytics(store, config.analytics)
            store.put_state("config", "wire_replay", binding)

    def ingest(self, row: dict) -> bool:
        self.validator.validate(row)
        with self.store.atomic():
            # Canonical ORIGINAL payload is the conflict key, before normalization.
            if self.store.existing_input(row["record_id"], row):
                return False
            kind, source = row["type"], source_key(row)
            if kind == "track.observed":
                self._track(row, source)
            elif kind == "stream.health":
                state = row["state"]
                terminal = state in ("disconnected", "ended")
                self.engine.ingest(Health(
                    row["record_id"], *source, row["media_time_ms"],
                    available=not terminal, capture_time_utc=row["capture_time_utc"],
                    continuity_lost=state != "healthy" or row["dropped_frames"] > 0,
                    terminal=terminal,
                ), source_payload=row)
            else:
                # Delayed worker records do not change active camera sessions,
                # stream ordering, coverage, line/zone state or physical metrics.
                self._action(row, source)
                self.store.put_input(row, kind, source, row["local_track_id"])
        return True

    def _track(self, row, source):
        key = (*source, row["local_track_id"])
        frame, time = row["frame_index"], row["media_time_ms"]
        previous = self.store.get_state("wire_track", key)
        if previous is not None and (frame <= previous["frame"] or time <= previous["time"]):
            raise ContractError("Track frame index and source time must strictly increase for new records")
        last_frame = self.store.get_state("wire_frame", source)
        if last_frame is not None:
            if frame < last_frame["frame"] or time < last_frame["time"]:
                raise ContractError("Out-of-order stream frame/time")
            if frame == last_frame["frame"]:
                if time != last_frame["time"] or row["frame_dims_px"] != last_frame["dimensions"]:
                    raise ContractError("Tracks sharing a frame must agree on its time and dimensions")
            elif time <= last_frame["time"]:
                raise ContractError("New stream frames must have increasing media time")
        x1, _, x2, y2 = row["bbox_xyxy_px"]
        point = ((x1 + x2) / 2, y2)
        queue = self.config.queue
        position = QueuePosition(queue.waiting_zone.contains(point), queue.service_zone.contains(point)) if queue else None
        self.engine.ingest(Observation(
            row["record_id"], TrackKey(*key), time, point,
            observed=row["position_status"] == "observed",
            capture_time_utc=row["capture_time_utc"], queue_position=position,
        ), source_payload=row)
        self.store.put_state("wire_track", key, {"frame": frame, "time": time})
        self.store.put_state("wire_frame", source, {
            "frame": frame, "time": time, "dimensions": row["frame_dims_px"],
        })

    def _action(self, row, source):
        candidate_key = (*source, row["candidate_id"])
        if row["type"] == "action.candidate":
            if self.store.get_state("wire_candidate", candidate_key) is not None:
                raise ContractError("candidate_id already exists; retry its stable record_id")
            self.store.put_state("wire_candidate", candidate_key, row)
            return
        candidate = self.store.get_state("wire_candidate", candidate_key)
        if candidate is None:
            raise ContractError("Scored action references an unavailable candidate in this source/session")
        for field in ("local_track_id", "global_visit_id"):
            if row[field] != candidate[field]:
                raise ContractError("Scored action must preserve the candidate's track identity")
        start, end = row["start_media_ms"], row["end_media_ms"]
        if start < candidate["start_media_ms"] or end > candidate["end_media_ms"]:
            raise ContractError("Scored action interval must be within its candidate")
        if row["task"] == "window_classification" and (
            start != candidate["start_media_ms"] or end != candidate["end_media_ms"]
        ):
            raise ContractError("A window classification labels the complete candidate window")
        result_key = (*candidate_key, row["model_id"], row["task"], row["action"], start, end)
        if self.store.get_state("wire_result", result_key) is not None:
            raise ContractError("Worker result identity already exists; retry its stable record_id")
        self.store.put_state("wire_result", result_key, row["record_id"])
