"""Application-owned geometry config; unrelated to the missing input schema."""

import json
from dataclasses import dataclass
from pathlib import Path

from app.analytics import AnalyticsConfig
from app.geometry import Line, Polygon


@dataclass(frozen=True)
class QueueGeometry:
    waiting_zone: Polygon
    service_zone: Polygon


@dataclass(frozen=True)
class ReplayConfig:
    analytics: AnalyticsConfig
    queue: QueueGeometry | None = None


def load_replay_config(path: str | Path) -> ReplayConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"application_config_version", "line", "zone", "max_gap_ms"}
    if not required <= set(raw) or set(raw) - required - {"queue"}:
        raise ValueError("Unexpected application config keys")
    if type(raw["application_config_version"]) is not int or raw["application_config_version"] != 1:
        raise ValueError("Unsupported application config version")
    line = raw["line"]
    if set(line) != {"start", "end", "hysteresis_px"}:
        raise ValueError("Unexpected line config keys")
    analytics = AnalyticsConfig(
        Line(tuple(line["start"]), tuple(line["end"]), line["hysteresis_px"]),
        Polygon(tuple(tuple(point) for point in raw["zone"])), raw["max_gap_ms"],
    )
    queue = None
    if "queue" in raw:
        if set(raw["queue"]) != {"waiting_zone", "service_zone"}:
            raise ValueError("Unexpected queue geometry keys")
        queue = QueueGeometry(**{
            key: Polygon(tuple(tuple(point) for point in raw["queue"][key]))
            for key in ("waiting_zone", "service_zone")
        })
    return ReplayConfig(analytics, queue)


def load_config(path: str | Path) -> AnalyticsConfig:
    """Retain the existing analytics-only API for kernel callers."""
    return load_replay_config(path).analytics
