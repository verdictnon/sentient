"""Versioned application-owned inference/tracker parameters, not wire schema."""

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class TrackingConfig:
    config_version: int = 1
    detector: str = "rtdetrv2"
    device: str = "auto"
    source_kind: str = "continuous"
    sample_period_ms: float = 200
    high_threshold: float = .5
    low_threshold: float = .1
    match_iou: float = .2
    second_match_iou: float = .1
    max_lost_ms: float = 1000
    max_tracks: int = 256
    seed: int = 0
    run_revision: int = 1
    min_person_area_ratio: float = 0.0
    person_nms_iou: float = 0.0

    def __post_init__(self):
        if type(self.config_version) is not int or self.config_version != 1:
            raise ValueError("Unsupported tracking configuration version")
        if self.detector not in ("rtdetrv2", "rtdetrv2_r34", "torchvision_mobilenet"):
            raise ValueError("Unknown detector implementation")
        if self.source_kind not in ("continuous", "sparse_clip"):
            raise ValueError("Declare continuous or sparse_clip")
        if self.device not in ("auto", "cpu", "cuda:0"):
            raise ValueError("Device must be auto, cpu or cuda:0")
        for name in ("min_person_area_ratio", "person_nms_iou", "sample_period_ms", "high_threshold", "low_threshold", "match_iou", "second_match_iou", "max_lost_ms"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 <= self.min_person_area_ratio < 1 or not 0 <= self.person_nms_iou <= 1:
            raise ValueError("Person size/NMS filters must be ratios in [0,1]; size must be < 1")
        if not 20 <= self.sample_period_ms <= 10000 or self.max_lost_ms < self.sample_period_ms:
            raise ValueError("Sampling must be 20..10000 ms and the lost buffer must cover one sampling interval")
        if not 0 < self.low_threshold <= self.high_threshold <= 1:
            raise ValueError("Require 0 < low threshold <= high threshold <= 1")
        if not 0 < self.match_iou <= 1 or not 0 < self.second_match_iou <= 1:
            raise ValueError("IoU thresholds must be in (0,1]")
        if type(self.max_tracks) is not int or not 1 <= self.max_tracks <= 512:
            raise ValueError("Track capacity must be 1..512")
        if type(self.seed) is not int or self.seed < 0 or type(self.run_revision) is not int or self.run_revision < 1:
            raise ValueError("Seed and explicit run revision must be nonnegative/positive integers")

    @classmethod
    def parse(cls, value: dict):
        if set(value) - set(cls.__dataclass_fields__):
            raise ValueError("Unexpected tracking configuration fields")
        return cls(**value)

    def to_dict(self):
        return asdict(self)
