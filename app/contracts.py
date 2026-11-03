"""Structural and local semantic validation against the original v1 schema."""

import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "contracts/v1/messages.schema.json"


class ContractError(ValueError):
    """A record or JSONL batch violates the integration boundary."""


def _reject_constant(value):
    raise ContractError(f"Non-finite JSON constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"Duplicate JSON property: {key}")
        result[key] = value
    return result


def strict_json(text: str):
    return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("All numeric values must be finite")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _increasing(values) -> bool:
    return all(b > a for a, b in zip(values, values[1:]))


class ContractValidator:
    def __init__(self, schema_path: Path = SCHEMA_PATH):
        self.schema = strict_json(schema_path.read_text(encoding="utf-8-sig"))
        Draft202012Validator.check_schema(self.schema)
        checker = FormatChecker()
        if "date-time" not in checker.checkers:
            raise RuntimeError("Install application dependencies: date-time format checking is unavailable")
        self.validator = Draft202012Validator(self.schema, format_checker=checker)

    def validate(self, record: dict) -> None:
        _finite(record)
        errors = list(self.validator.iter_errors(record))
        if errors:
            identity = record.get("record_id", "<missing ID>") if isinstance(record, dict) else "<non-object>"
            raise ContractError(f"{identity}: structural validation failed against original v1 schema")
        kind = record["type"]
        if record["clock_status"] == "verified" and record["capture_time_utc"] is None:
            raise ContractError("Verified clock requires capture_time_utc")
        if kind == "track.observed":
            x1, y1, x2, y2 = record["bbox_xyxy_px"]
            width, height = record["frame_dims_px"]
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ContractError("Box must satisfy 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height")
            if record["world_xy_m"] is not None and not record["calibration_version"]:
                raise ContractError("World position requires a calibration version")
            if record["association_status"] == "confirmed":
                raise ContractError("Confirmed association requires external timing/geometry evidence; unsupported in P0")
        if kind in ("action.candidate", "action.scored"):
            if record["start_media_ms"] > record["end_media_ms"]:
                raise ContractError("Action interval start must not exceed end")
        if kind == "action.candidate":
            indices, times = record["frame_indices"], record["frame_media_times_ms"]
            if len(indices) != len(times) or not _increasing(indices) or not _increasing(times):
                raise ContractError("Candidate indices/times must have equal lengths and strictly increase")
            if times[0] < record["start_media_ms"] or times[-1] > record["end_media_ms"]:
                raise ContractError("Candidate frames must be within its media interval")
            actual = record["capture_elapsed_ms"]
            if record["sampling_time_known"] and actual is None:
                raise ContractError("Known sampling time requires measured capture elapsed times")
            if actual is not None and (len(actual) != len(indices) or not _increasing(actual)):
                raise ContractError("Capture elapsed times must match frame count and strictly increase")
        if kind == "action.scored":
            if record["abstained"] and record["action"] != "unknown":
                raise ContractError("An abstained result must have action=unknown")
            scores, convention = record["scores"], record["score_semantics"]
            if scores is None:
                if not record["abstained"] or convention != "unavailable":
                    raise ContractError("Missing scores require abstention and unavailable score semantics")
            elif convention == "unavailable":
                raise ContractError("Unavailable score semantics require null scores")
            elif convention == "softmax_single_label":
                if any(v is None for v in scores.values()) or abs(sum(scores.values()) - 1) >= 1e-5:
                    raise ContractError("Softmax scores must be non-null and sum to one")
            # Independent sigmoid scores are bounded by the schema, not normalized;
            # their background score may be null, as explicitly documented.

    def read_jsonl(self, path: Path) -> list[tuple[int, dict]]:
        records = []
        with path.open(encoding="utf-8-sig") as stream:
            for number, text in enumerate(stream, 1):
                if not text.strip():
                    continue
                try:
                    record = strict_json(text)
                    self.validate(record)
                except (ValueError, TypeError, OverflowError, RecursionError) as error:
                    raise ContractError(f"{path.name}:{number}: {error}") from error
                records.append((number, record))
        return records
