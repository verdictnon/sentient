"""Validate the partner examples; this is not an analytics implementation."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker

ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'contracts/v1';schema=json.loads((folder/'messages.schema.json').read_text())
    Draft202012Validator.check_schema(schema);validator=Draft202012Validator(schema,format_checker=FormatChecker())
    records=[json.loads(line) for line in (folder/'examples.jsonl').read_text().splitlines() if line]
    seen=set();last_frame={};candidates={}
    for row in records:
        validator.validate(row)
        if row['record_id'] in seen:raise ValueError('Duplicate example record ID')
        seen.add(row['record_id'])
        if row['clock_status']=='verified':assert row['capture_time_utc'] is not None
        kind=row['type']
        if kind=='track.observed':
            x1,y1,x2,y2=row['bbox_xyxy_px'];width,height=row['frame_dims_px']
            assert 0<=x1<x2<=width and 0<=y1<y2<=height
            if row['world_xy_m'] is not None:assert row['calibration_version'] is not None
            key=(row['store_id'],row['camera_id'],row['stream_session_id'],row['local_track_id'])
            if key in last_frame:
                frame,ms=last_frame[key];assert row['frame_index']>frame and row['media_time_ms']>ms
            last_frame[key]=(row['frame_index'],row['media_time_ms'])
        if kind in ['action.candidate','action.scored']:assert row['start_media_ms']<=row['end_media_ms']
        if kind=='action.candidate':
            indices=row['frame_indices'];times=row['frame_media_times_ms'];assert len(indices)==len(times)
            assert all(b>a for a,b in zip(indices,indices[1:])) and all(b>a for a,b in zip(times,times[1:]))
            assert times[0]>=row['start_media_ms'] and times[-1]<=row['end_media_ms']
            actual=row['capture_elapsed_ms']
            if row['sampling_time_known']:assert actual is not None
            if actual is not None:assert len(actual)==len(indices) and all(b>a for a,b in zip(actual,actual[1:]))
            candidates[row['candidate_id']]=row
        if kind=='action.scored':
            assert row['candidate_id'] in candidates
            if row['abstained']:assert row['action']=='unknown'
            if row['scores'] is None:assert row['abstained'] and row['score_semantics']=='unavailable'
            elif row['score_semantics']=='softmax_single_label':assert all(v is not None for v in row['scores'].values()) and abs(sum(row['scores'].values())-1)<1e-5
    print(f'Draft 2020-12 schema valid; {len(records)} examples pass structural and fixture-semantic checks. No live analytics or model accuracy asserted.')


if __name__=='__main__':main()
