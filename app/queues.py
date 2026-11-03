"""Independent queue lifecycle; no wire-format or polygon adapter is assumed."""

from dataclasses import dataclass

from app.domain import check_time


@dataclass
class QueueVisit:
    entered_ms: int | float
    entry_observed: bool = True
    service_started_ms: int | float | None = None
    ended: bool = False
    service_start_observed: bool = True

    def __post_init__(self):
        check_time(self.entered_ms)
        if type(self.entry_observed) is not bool:
            raise ValueError("entry_observed must be boolean")
        if type(self.ended) is not bool or type(self.service_start_observed) is not bool:
            raise ValueError("Queue observation flags must be boolean")
        if self.service_started_ms is not None or self.ended:
            raise ValueError("New visits must begin waiting")

    def _check_transition(self, time_ms):
        check_time(time_ms)
        if self.ended:
            raise ValueError("Queue visit has already ended")
        previous = self.service_started_ms if self.service_started_ms is not None else self.entered_ms
        if time_ms < previous:
            raise ValueError("Out-of-order queue transition")

    def start_service(self, time_ms: int | float, *, start_observed: bool = True):
        self._check_transition(time_ms)
        if type(start_observed) is not bool:
            raise ValueError("start_observed must be boolean")
        if self.service_started_ms is not None:
            raise ValueError("Service has already started")
        self.service_started_ms = time_ms
        self.service_start_observed = start_observed

    def to_state(self) -> dict:
        """Serialize an active lifecycle for transactional persistence."""
        if self.ended:
            raise ValueError("An ended visit cannot be persisted as active")
        return {
            "entered_ms": self.entered_ms, "entry_observed": self.entry_observed,
            "service_started_ms": self.service_started_ms,
            "service_start_observed": self.service_start_observed,
        }

    @classmethod
    def from_state(cls, state: dict) -> "QueueVisit":
        if set(state) != {"entered_ms", "entry_observed", "service_started_ms", "service_start_observed"}:
            raise ValueError("Invalid persisted queue state")
        visit = cls(state["entered_ms"], state["entry_observed"])
        if type(state["service_start_observed"]) is not bool:
            raise ValueError("Invalid persisted service observation flag")
        if state["service_started_ms"] is not None:
            visit.start_service(state["service_started_ms"], start_observed=state["service_start_observed"])
        else:
            visit.service_start_observed = state["service_start_observed"]
        return visit

    def end(self, time_ms: int | float, *, departure_observed: bool) -> dict:
        self._check_transition(time_ms)
        if type(departure_observed) is not bool:
            raise ValueError("departure_observed must be boolean")
        self.ended = True
        service = self.service_started_ms
        return {
            "outcome": "censored" if not departure_observed else (
                "completed" if service is not None else "abandoned"
            ),
            "waiting_ms": service - self.entered_ms if (
                service is not None and self.entry_observed and self.service_start_observed
            ) else None,
            "observed_waiting_ms": (service if service is not None else time_ms) - self.entered_ms,
            "service_ms": time_ms - service if (
                service is not None and departure_observed and self.service_start_observed
            ) else None,
            "observed_service_ms": time_ms - service if service is not None else None,
            "entry_observed": self.entry_observed,
            "service_start_observed": self.service_start_observed if service is not None else False,
            "departure_observed": departure_observed,
            "observation_complete": self.entry_observed and departure_observed and (
                service is None or self.service_start_observed
            ),
            "waiting_complete": service is not None and self.entry_observed and self.service_start_observed,
            "service_complete": service is not None and self.service_start_observed and departure_observed,
        }
