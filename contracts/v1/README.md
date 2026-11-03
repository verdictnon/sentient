# Sentient integration contract — proposed v1.0

Both workstreams should implement this boundary before coupling application and model code. Transport starts as JSONL; the same records can later cross a process queue or API. `messages.schema.json` defines structural types. Runtime semantic checks below are also required.

Validate the supplied examples in an isolated tool environment:

```sh
.tools/bin/uv run --no-project --python .venv/bin/python --with jsonschema==4.25.1 python scripts/validate_handoff.py
```

Alternatively install `requirements-check.txt` into the separate application environment, then run `scripts/validate_handoff.py`. This checks the examples and schema, not the partner's future replay implementation.

## Shared rules

- Every record has `schema_version`, `type`, stable `record_id`, `store_id`, `camera_id`, `stream_session_id`, `media_time_ms`, `capture_time_utc` and `clock_status`.
- `media_time_ms` is source presentation/replay time in milliseconds, not processing time. Frame observations are monotonic within a stream session; delayed candidate/results may refer to older intervals. Retain actual capture times separately when available. `clock_status` is `verified`, `unverified` or `playback_only`. Verified UTC uses a trailing `Z`; unknown capture UTC is null. RetailAction's anonymized epoch timestamps are not actual common store UTC.
- Frame indices are zero-based. Boxes are original-image pixel `xyxy`. Dimensions are `[width,height]`. Validate `x1<x2`, `y1<y2` and bounds; do not silently change coordinate conventions.
- Track identity is the tuple `(store_id,camera_id,stream_session_id,local_track_id)`. A local ID is never a whole-store identity. `global_visit_id` is nullable. A new stream session cannot reuse an old identity merely because local IDs repeat.
- `record_id` must survive replay/retry. An input key can be a hash of type/store/camera/session/frame/track. Keep processing timestamps outside that key. Worker result identity includes candidate, model version, task and scored action/span.
- Logical asset references such as `asset://...` resolve inside the configured evidence store. They do not authorize arbitrary filesystem paths or fetching arbitrary URLs.
- Store proposed association corrections and human reviews as versioned records; preserve evidence and previous provenance. Do not overwrite model scores with human labels.

## Records

| Type | Producer → consumer | Additional fields |
|---|---|---|
| `track.observed` | Detector/tracker → analytics and proposal worker | `frame_index`, `local_track_id`, nullable `global_visit_id`, `association_status`, `bbox_xyxy_px`, `frame_dims_px`, `score`, `position_status`, nullable `world_xy_m`, nullable `calibration_version`, nullable `pose_ref`, `model_id` |
| `action.candidate` | Proposal worker → action model worker | `candidate_id`, track IDs, `start_media_ms`, `end_media_ms`, `frame_indices`, `frame_media_times_ms`, `capture_elapsed_ms`, `sampling_time_known`, `source_kind`, `video_ref`, `observations_ref` |
| `action.scored` | Action worker → review/event storage | `candidate_id`, track IDs, `model_id`, `action`, `task`, scored interval, `score_semantics`, nullable `scores`, `abstained`, `quality`, `reason`, `evidence_ref` |
| `stream.health` | Ingestion → analytics/dashboard | `state`, `dropped_frames`, `reason` |

Scores are model outputs, not proven probabilities of criminal activity. Softmax single-label and independent sigmoid scores are distinct. For sigmoid models, background may be null; missing inference has null scores rather than invented probabilities. A V2 result labels its whole window (`task=window_classification`), not a discovered action boundary. A temporal worker may emit several independently identified action-span results for a candidate. `action` is take/put/touch/background/unknown. Unknown or abstained outputs must not become confirmed actions. `quality` distinguishes a normal result from a mock, missing pose, insufficient visibility, unknown timing or an error.

An action candidate supplies frames and pose/track observations, not an oracle action location. Its frame arrays must have equal lengths and increasing indices/times. `capture_elapsed_ms` is either a same-length array of measured elapsed capture times or null. Playback times from a sparse export do not establish physical sampling intervals: set `sampling_time_known=false` if original intervals are unavailable. Cross-view alignment needs measured common timing; do not match frame number 10 to frame number 10 automatically.

`position_status=predicted` is a tracker prediction, not a new observed customer location. Ground-plane coordinates require valid calibration; they do not describe the hand's position on an elevated shelf. Unknown world position is null.

## Runtime semantic checks

Validate frame/box bounds, candidate start ≤ end, equal array lengths, frame-observation time monotonicity and score conventions for each model. Require non-null capture UTC for `clock_status=verified`; a world point requires a calibration version. Confirmed cross-camera association additionally requires external timing/geometry evidence; the schema alone does not establish it.

Deduplicate by stable record IDs and reject conflicting payloads with the same ID. Define an explicit out-of-order policy. A model request may fail cleanly instead of inventing a pose, timestamp or identity.

## Fixture and exact P0 expectation

`examples.jsonl` is **synthetic**, with a 100×100 image. Use a vertical entrance line x=50, positive direction left→right, 5-pixel hysteresis, and zone polygon `[(55,0),(85,0),(85,100),(55,100)]`. Track position is the box's bottom center. Maximum allowed observation gap is 1,500 ms.

Session `session-a` / track `7` has bottom-center x positions 10,40,60,70,90 at media times 0,1000,2000,3000,4000 ms. Count **one inward crossing**, at the first observed stable right-side sample (2000 ms). Zone entry is 2000 ms and exit is 4000 ms, so observed dwell is **2.0 seconds**. No sub-frame interpolation is assumed. The later `session-b` track `7` is a separate track and has not crossed or completed dwell. Its global visit ID remains null.

The health record closes session A. The action request/result are mock integration examples; their asset references are placeholders. P0 may validate/store these but must not open nonexistent evidence or include them in physical action metrics. Replaying every line twice must produce the same events as replaying once.

The fixture tests counting/persistence logic, not detector accuracy, live synchronization, re-identification or action recognition. Add separate queue, jitter, gap and abandoned-visit fixtures during partner implementation.
