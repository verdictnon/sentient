"""Deterministic analytics over normalized internal inputs, not wire records."""

from dataclasses import asdict, dataclass
import json

from app.domain import Health, Observation
from app.geometry import Line, Polygon
from app.queues import QueueVisit
from app.storage import SQLiteStore, canonical, stable_id

ENGINE_REVISION = 2


@dataclass(frozen=True)
class AnalyticsConfig:
    line: Line
    zone: Polygon
    max_gap_ms: int = 1500

    def __post_init__(self):
        if type(self.max_gap_ms) is not int or self.max_gap_ms <= 0:
            raise ValueError("Maximum observation gap must be positive integer ms")


class Analytics:
    def __init__(self, store: SQLiteStore, config: AnalyticsConfig):
        self.store = store
        self.config = config
        with store.atomic():
            revision = store.get_state("config", "engine_revision")
            if revision != ENGINE_REVISION and (revision is not None or store.has_history()):
                raise ValueError("Analytics semantics changed; replay into a new database")
            previous = store.get_state("config", "analytics")
            # Round-trip tuple/list normalization through canonical JSON.
            current = json.loads(canonical(asdict(config)))
            if previous is not None and previous != current:
                raise ValueError("Analytics configuration changed; use a new database")
            store.put_state("config", "analytics", current)
            store.put_state("config", "engine_revision", ENGINE_REVISION)

    def ingest(self, item: Observation | Health, *, source_payload: dict | None = None) -> bool:
        if not isinstance(item, (Observation, Health)):
            raise TypeError("Expected an internal Observation or Health instance")
        payload = asdict(item)
        if isinstance(item, Observation):
            if item.queue_position is None:
                payload.pop("queue_position")
            key = [item.key.store, item.key.camera, item.key.session, item.key.track]
            source = tuple(key[:3])
            kind = "observation"
        else:
            if not item.continuity_lost:
                payload.pop("continuity_lost")
            if not item.terminal:
                payload.pop("terminal")
            key = None
            source = (item.store, item.camera, item.session)
            kind = "health"
        if source_payload is not None:
            if (source_payload.get("record_id") != item.record_id
                    or source_payload.get("media_time_ms") != item.media_time_ms
                    or source_payload.get("capture_time_utc") != item.capture_time_utc):
                raise ValueError("Source payload does not match the normalized observation")
            payload = source_payload
        with self.store.atomic():
            if self.store.existing_input(item.record_id, payload):
                return False
            stream = self.store.get_state("stream", source, {
                "last_time": -1, "retired": False, "available": True,
            })
            if stream["retired"]:
                raise ValueError("Cannot reopen a retired stream session")
            if item.media_time_ms < stream["last_time"]:
                raise ValueError("Out-of-order source time")
            state = self.store.get_state("track", key, {}) if key is not None else None
            if state and item.media_time_ms <= state["last_sample"]:
                raise ValueError("Repeated or decreasing track time with a new record ID")
            if kind == "health":
                if self.store.has_input_at("health", source, item.media_time_ms):
                    raise ValueError("Repeated health time with a new record ID")
            if kind == "observation" and not stream["available"]:
                raise ValueError("Observation received while stream is unavailable")

            camera = source[:2]
            old_session = self.store.get_state("camera", camera)
            if old_session is not None and old_session != source[2]:
                old_source = (*camera, old_session)
                self._close_source(old_source, "reconnect")
                old_stream = self.store.get_state("stream", old_source)
                old_stream["retired"] = True
                self.store.put_state("stream", old_source, old_stream)
            self.store.put_state("camera", camera, source[2])

            # Advancing source time also expires missing tracks that never return.
            for other_key, other in self.store.track_states(source):
                last = other.get("last_observed")
                if last is not None and item.media_time_ms - last > self.config.max_gap_ms:
                    self._censor(other_key, other, "gap")
                else:
                    queue_last = other.get("queue_last_observed")
                    if queue_last is not None and item.media_time_ms - queue_last > self.config.max_gap_ms:
                        self._censor_queue(other_key, other, "gap")
                        self.store.put_state("track", other_key, other)
            if isinstance(item, Health):
                if not item.available or item.continuity_lost:
                    self._close_source(source, "health")
                stream["available"] = item.available
                if item.terminal:
                    stream["retired"] = True
            else:
                state = self.store.get_state("track", key, {})
                if item.observed:
                    self._observe(item, key, state)
                state["last_sample"] = item.media_time_ms
                self.store.put_state("track", key, state)
            stream["last_time"] = item.media_time_ms
            self.store.put_state("stream", source, stream)
            self.store.put_input(payload, kind, source, key[3] if key else None)
        return True

    def _observe(self, item: Observation, key: list, state: dict):
        side = self.config.line.side(item.point)
        old_side = state.get("side", 0)
        if side:
            if (old_side and old_side != side
                    and self.config.line.crossed_segment(state["stable_point"], item.point)):
                self.store.event(
                    "entrance.inward" if side == 1 else "entrance.outward",
                    key, item.media_time_ms, item.record_id,
                    {"from_side": old_side, "to_side": side},
                )
            state["side"] = side
            state["stable_point"] = item.point
        inside = self.config.zone.contains(item.point)
        visit = state.get("visit")
        if inside and visit is None:
            visit = {
                "start_ms": item.media_time_ms,
                "start_record_id": item.record_id,
                "entry_observed": state.get("last_observed") is not None,
            }
            state["visit"] = visit
            self.store.event("zone.visit_started", key, item.media_time_ms, item.record_id, visit)
        elif not inside and visit is not None:
            self._close_visit(key, state, item.media_time_ms, "exit", item.record_id)
        state["last_observed"] = item.media_time_ms
        state["last_observed_record_id"] = item.record_id
        if item.queue_position is not None:
            self._observe_queue(item, key, state)

    def _observe_queue(self, item: Observation, key, state):
        position = item.queue_position
        visit = state.get("queue_visit")
        previous_time = state.get("queue_last_observed")
        occupied = position.in_queue or position.in_service
        if visit is None and occupied:
            lifecycle = QueueVisit(
                item.media_time_ms,
                entry_observed=previous_time is not None and not position.in_service,
            )
            visit = {"start_record_id": item.record_id, "lifecycle": lifecycle.to_state()}
            state["queue_visit"] = visit
            self.store.event("queue.visit_started", key, item.media_time_ms, item.record_id, {
                "start_record_id": item.record_id, "start_ms": item.media_time_ms,
                "entry_observed": lifecycle.entry_observed,
            })
        if visit is not None:
            lifecycle = QueueVisit.from_state(visit["lifecycle"])
            if position.in_service and lifecycle.service_started_ms is None:
                lifecycle.start_service(item.media_time_ms, start_observed=previous_time is not None)
                visit["lifecycle"] = lifecycle.to_state()
                self.store.event("queue.service_started", key, item.media_time_ms, item.record_id, {
                    "start_record_id": visit["start_record_id"],
                    "start_ms": lifecycle.entered_ms, "service_started_ms": item.media_time_ms,
                    "entry_observed": lifecycle.entry_observed,
                    "service_start_observed": lifecycle.service_start_observed,
                    "waiting_ms": item.media_time_ms - lifecycle.entered_ms if (
                        lifecycle.entry_observed and lifecycle.service_start_observed
                    ) else None,
                })
            if not occupied:
                self._close_queue(key, state, item.media_time_ms, "departure", item.record_id)
        state["queue_last_observed"] = item.media_time_ms
        state["queue_last_record_id"] = item.record_id

    def _close_queue(self, key, state, end_ms, reason, end_record_id):
        visit = state.get("queue_visit")
        if visit is None:
            return
        lifecycle = QueueVisit.from_state(visit["lifecycle"])
        result = lifecycle.end(end_ms, departure_observed=reason == "departure")
        cause = stable_id([visit["start_record_id"], end_record_id, reason])
        self.store.event("queue.visit_closed", key, end_ms, cause, {
            **result, "start_record_id": visit["start_record_id"],
            "end_record_id": end_record_id, "start_ms": lifecycle.entered_ms,
            "service_started_ms": lifecycle.service_started_ms,
            "end_ms": end_ms, "reason": reason,
        })
        state["queue_visit"] = None

    def _censor_queue(self, key, state, reason):
        last = state.get("queue_last_observed")
        if last is not None:
            self._close_queue(key, state, last, reason, state["queue_last_record_id"])
        state.update(queue_visit=None, queue_last_observed=None, queue_last_record_id=None)

    def _close_visit(self, key, state, end_ms: int, reason: str, cause: str):
        visit = state.get("visit")
        if visit is None:
            return
        self.store.event("zone.visit_closed", key, end_ms, cause, {
            **visit, "end_ms": end_ms,
            "observed_dwell_ms": end_ms - visit["start_ms"],
            "exit_observed": reason == "exit",
            "complete": visit["entry_observed"] and reason == "exit",
            "reason": reason,
        })
        state["visit"] = None

    def _censor(self, key, state, reason: str):
        last = state.get("last_observed")
        if last is not None:
            # Expiration identity must not depend on which unrelated frame first
            # advances time beyond the gap. Use the affected track's endpoint.
            endpoint = stable_id([state["last_observed_record_id"], reason])
            self._close_visit(key, state, last, reason, endpoint)
        self._censor_queue(key, state, reason)
        state.update(side=0, stable_point=None, visit=None, last_observed=None)
        self.store.put_state("track", key, state)

    def _close_source(self, source, reason: str):
        for key, state in self.store.track_states(source):
            self._censor(key, state, reason)

    def finish(self):
        """Explicit end-of-input: censor at last observed time, never wall time."""
        with self.store.atomic():
            for key, state in self.store.iter_states("track"):
                self._censor(key, state, "end_of_input")

    def summary(self) -> dict:
        with self.store.atomic(write=False):
            events = self.store.events()
            active_queues = [state["queue_visit"]["lifecycle"]
                             for _, state in self.store.states("track") if state.get("queue_visit")]
        visits = [e["payload"] for e in events if e["kind"] == "zone.visit_closed"]
        complete = [v for v in visits if v["complete"]]
        queues = [e["payload"] for e in events if e["kind"] == "queue.visit_closed"]
        waits = [e["payload"]["waiting_ms"] for e in events
                 if e["kind"] == "queue.service_started" and e["payload"]["waiting_ms"] is not None]
        return {
            "inward_crossings": sum(e["kind"] == "entrance.inward" for e in events),
            "outward_crossings": sum(e["kind"] == "entrance.outward" for e in events),
            "complete_visits": len(complete),
            "censored_visits": len(visits) - len(complete),
            "complete_observed_dwell_ms": sum(v["observed_dwell_ms"] for v in complete),
            "censored_observed_dwell_ms": sum(v["observed_dwell_ms"] for v in visits if not v["complete"]),
            "queue_completed_visits": sum(q["outcome"] == "completed" for q in queues),
            "queue_abandoned_visits": sum(q["outcome"] == "abandoned" for q in queues),
            "queue_censored_visits": sum(q["outcome"] == "censored" for q in queues),
            "queue_completed_waits": len(waits),
            "queue_completed_waiting_ms": sum(waits),
            "queue_open_waits": sum(q["service_started_ms"] is None for q in active_queues),
            "queue_open_service_visits": sum(q["service_started_ms"] is not None for q in active_queues),
        }
