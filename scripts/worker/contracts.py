"""Original v1 transport validation without importing the partner application."""
import json
import math
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT/'contracts/v1/messages.schema.json'


def strict_json(text):
    def reject(value):
        raise ValueError(f'Non-finite JSON constant: {value}')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(text, parse_constant=reject, object_pairs_hook=unique)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def track_key(row):
    return tuple(row[k] for k in ('store_id','camera_id','stream_session_id','local_track_id'))


class Validator:
    def __init__(self):
        self.schema = strict_json(SCHEMA.read_text())
        checker = FormatChecker()
        if 'date-time' not in checker.checkers:
            raise RuntimeError('Install rfc3339-validator for contract time validation')
        self.validator = Draft202012Validator(self.schema, format_checker=checker)

    def validate(self, row):
        canonical(row)  # rejects non-finite floats even when supplied as Python values
        self.validator.validate(row)
        if row['clock_status']=='verified' and row['capture_time_utc'] is None:
            raise ValueError('Verified capture clock needs UTC')
        if row['type']=='track.observed':
            a,b,c,d = row['bbox_xyxy_px']
            w,h = row['frame_dims_px']
            if not 0<=a<c<=w or not 0<=b<d<=h:
                raise ValueError('Track box is outside original frame bounds')
            if row['world_xy_m'] is not None and not row['calibration_version']:
                raise ValueError('World position requires calibration')
        if row['type'] in ('action.candidate','action.scored'):
            if row['start_media_ms']>row['end_media_ms']:
                raise ValueError('Action interval is reversed')
        if row['type']=='action.candidate':
            indices, times = row['frame_indices'], row['frame_media_times_ms']
            if len(indices)!=len(times) or any(b<=a for a,b in zip(indices,indices[1:])) or any(b<=a for a,b in zip(times,times[1:])):
                raise ValueError('Candidate frame indices/times must strictly increase together')
            if times[0]<row['start_media_ms'] or times[-1]>row['end_media_ms']:
                raise ValueError('Candidate source frames are outside its interval')
            elapsed = row['capture_elapsed_ms']
            if row['sampling_time_known'] and elapsed is None:
                raise ValueError('Known sampling requires measured elapsed capture times')
            if elapsed is not None and (len(elapsed)!=len(indices) or any(b<=a for a,b in zip(elapsed,elapsed[1:]))):
                raise ValueError('Invalid measured capture intervals')
        if row['type']=='action.scored':
            if row['abstained'] and row['action']!='unknown':
                raise ValueError('Abstained result must be unknown')
            scores = row['scores']
            if scores is None:
                if not row['abstained'] or row['score_semantics']!='unavailable':
                    raise ValueError('Missing inference cannot invent scores')
            elif row['score_semantics']=='unavailable':
                raise ValueError('Unavailable inference cannot contain scores')
            elif row['score_semantics']=='softmax_single_label' and (any(v is None for v in scores.values()) or abs(sum(scores.values())-1)>1e-5):
                raise ValueError('Softmax scores must sum to one')

    def read_tracks(self, path, max_records=500000):
        rows, seen, last = [], {}, {}
        with Path(path).open() as stream:
            for number, line in enumerate(stream,1):
                if not line.strip():continue
                row = strict_json(line)
                self.validate(row)
                if row['type'] not in ('track.observed','stream.health'):
                    raise ValueError(f'Input line {number}: expected tracks/health, not action labels')
                encoded = canonical(row)
                if row['record_id'] in seen:
                    if seen[row['record_id']]!=encoded:
                        raise ValueError('Conflicting duplicate record_id')
                    continue
                seen[row['record_id']] = encoded
                if row['type']=='track.observed':
                    key = track_key(row)
                    pair = row['frame_index'], row['media_time_ms']
                    if key in last and (pair[0]<=last[key][0] or pair[1]<=last[key][1]):
                        raise ValueError('Track frame indices and media times must increase within each stream/local ID')
                    last[key] = pair
                rows.append(row)
                if len(rows)>max_records:
                    raise ValueError('Track input exceeds the configured record bound')
        return rows
