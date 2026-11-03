"""Internal normalized types, NOT a definition of the external JSON contract.

The contract adapter validates messages.schema.json before mapping inputs here.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TrackKey:
    store: str
    camera: str
    session: str
    track: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value for value in (
            self.store, self.camera, self.session, self.track
        )):
            raise ValueError("Internal track keys must contain nonempty strings")


def check_time(value: int | float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Media time must be a finite nonnegative number in ms")


def check_point(point: tuple[float, float]) -> None:
    if len(point) != 2 or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) for value in point
    ):
        raise ValueError("Points require two finite coordinates")


@dataclass(frozen=True)
class QueuePosition:
    """Explicit internal memberships, never inferred from missing input.

    Service membership takes precedence when the two regions overlap.
    This is not part of the external JSON contract.
    """

    in_queue: bool
    in_service: bool

    def __post_init__(self):
        if type(self.in_queue) is not bool or type(self.in_service) is not bool:
            raise ValueError("Queue memberships must be boolean")


@dataclass(frozen=True)
class Observation:
    record_id: str
    key: TrackKey
    media_time_ms: int | float
    point: tuple[float, float]
    observed: bool = True
    capture_time_utc: str | None = None
    queue_position: QueuePosition | None = None

    def __post_init__(self):
        if not isinstance(self.key, TrackKey):
            raise ValueError("Observation requires a normalized TrackKey")
        if not isinstance(self.record_id, str) or not self.record_id:
            raise ValueError("A stable record ID is required")
        check_time(self.media_time_ms)
        check_point(self.point)
        if type(self.observed) is not bool:
            raise ValueError("observed must be boolean")
        if self.queue_position is not None and not isinstance(self.queue_position, QueuePosition):
            raise ValueError("queue_position must be QueuePosition or None")


@dataclass(frozen=True)
class Health:
    """Internal availability and continuity signal mapped by the adapter."""

    record_id: str
    store: str
    camera: str
    session: str
    media_time_ms: int | float
    available: bool
    capture_time_utc: str | None = None
    continuity_lost: bool = False
    terminal: bool = False

    def __post_init__(self):
        if any(not isinstance(value, str) or not value for value in (
            self.record_id, self.store, self.camera, self.session
        )):
            raise ValueError("A stable record ID and source identity are required")
        check_time(self.media_time_ms)
        if type(self.available) is not bool:
            raise ValueError("available must be boolean")
        if type(self.continuity_lost) is not bool or type(self.terminal) is not bool:
            raise ValueError("Health continuity flags must be boolean")
        if self.terminal and self.available:
            raise ValueError("A terminal stream cannot be available")
