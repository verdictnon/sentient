# Partner brief: build Sentient's application and video pipeline

Updated 2 November 2026. Read `plan.txt`, `TEAM_PLAN.md` and `contracts/v1/README.md`. This is an implementation handoff, not a request to retrain the interaction model.

## What already exists

The project currently contains experiments and data tooling, not a production application. V2/V5 classify **provided 13-frame, subject-pose-cropped windows** as take/put/touch/background. V3 is a different, sparse-frame temporal experiment. These models do not discover every person/action in raw camera video. Touch recognition is currently unreliable. Treat model results as review candidates with supporting video.

Our model workstream owns training, weights and dataset evaluation. You own new application folders and dependencies. Build against versioned messages and a mock action worker first.

## First Codex prompt — paste this with the brief and contracts available

> Build Sentient's first offline analytics slice. Read PARTNER_START_HERE.md, TEAM_PLAN.md and contracts/v1/README.md. Implement only P0 below first, then run its acceptance checks and show the demo command. Use a separate application environment and app dependency file; preserve existing model scripts, experiments, data and .venv. Create app/, tests/app/ and docs/app/ as needed. Use Python and SQLite with a JSONL replay CLI. Validate the versioned track/health records, make writes idempotent, preserve source time, and support a configurable directional line and polygon zone. The supplied trajectory fixture is synthetic and tests business logic, not detector accuracy. Add a queue fixture that follows the documented queue semantics. Do not train models or claim cross-camera identity matching. If there is no shared Git repository yet, develop in a separate directory and return the source changes and run instructions. After P0 passes, continue sequentially through the remaining tasks unless a real footage or camera dependency prevents that task; complete independent work meanwhile.

## P0 — deterministic replay, contract checks and persistence

**Deliver:** `python -m app.replay --input ... --config ... --db ...`; an example zone/line configuration; a summary JSON. A web UI is not needed yet.

Persist input observations, stream health and derived events. Derive stable event identities from the source/session/track and event transition, not wall-clock processing time. Save processing time separately. Count a directional doorway crossing after a track moves between stable sides of a line, with hysteresis to prevent jitter counts. Track zone entry, exit and observed dwell using media time. Explicitly distinguish a complete observation interval from a censored one.

**Done when:**

- The supplied synthetic fixture produces one inward crossing and 2.0 seconds of observed zone dwell for the first track. Details and expected results are in `contracts/v1/README.md`.
- Replaying the same records twice into one database leaves event counts unchanged.
- Out-of-order or repeated records are rejected or handled deterministically under a documented rule; they never silently alter history.
- The same local track ID in a new stream session is a different track. A reconnect does not imply the person left, entered or became the same person.
- Missing frames/health gaps close or censor the observation interval; no dwell is invented through a long gap.
- Tests include hovering on a line, a partial track, a queue abandonment, service completion, a replay retry and a stream reconnect.

## P1 — recorded-video ingestion, zones and review data

**Deliver:** local MP4 ingest, seekable playback, manual line/polygon configuration and evidence references. Preserve PTS/media time; keep capture UTC nullable. A browser page can draw zones on a frame and show a track/event table.

Add a lightweight review/export page early: show before/during/after video, the selected subject, both views when available, class, visibility, uncertainty and reviewer decision. Export human labels separately from model scores. Mark collection session/day/camera so the model team can split data without leakage. An annotation export needs media SHA-256, frame indices, original times when known, subject track key, action spans, visibility, provenance, reviewer and revision.

**Done when:** the same file and config replay identically; a human can correct one event and export a loader-ready record; unavailable capture times are not replaced with playback times and presented as real camera synchronization. Use JSONL and a small metadata manifest rather than duplicating all videos.

No camera access is required for the ingestion/review code. Use authorized continuous recordings for a real demonstration; RetailAction's selected sparse clips do not validate continuous counting or queues.

## P2 — pretrained person detection and per-camera tracking

**Start with:** original RT-DETRv2 through an adapter, then detector-independent ByteTrack. Record the exact implementation/checkpoint and applicable terms. Keep detection output format independent of the model. [Original RT-DETR](https://github.com/lyuwenyu/RT-DETR), [Trackers](https://github.com/roboflow/trackers).

**Deliver:** boxes, confidence, camera-local track IDs, timing and model versions in the contract format. Run detection at a measured cadence; tracking may fill gaps, but distinguish predicted from observed positions. Sample by time rather than assuming every input is 30 FPS.

**Done when:** a manually annotated entrance recording has reported count error and identity switches, and the same input has repeatable output under pinned settings. Measure complete decode → detection → tracking latency and peak memory. Do not advertise a camera capacity from model-only forward time. An entrance accuracy target must be agreed after inspecting camera coverage; no arbitrary target is already achieved.

## P3 — usable analytics

Implement entrance/exit counts and hourly totals; zone visits and person-seconds; route transitions where identity is sufficiently reliable; queue entry → service start → departure/abandonment. Waiting time is time to service start, not zone occupancy. Keep unfinished/censored waits separate from completed waits.

**Done when:** hand-labeled replay scenarios match their expected events and durations; restarting the service does not duplicate them; the API reports coverage alongside totals. Disallow whole-store unique-person counts from independent local camera IDs. Report overlap explicitly until global association exists.

Use FastAPI if an API is needed, SQLite WAL locally, and one small web interface. A video overlay plus useful event table is enough initially. Keep retention and local evidence references configurable. Do not put stream credentials or raw recordings in Git.

## P4 — action-worker integration

Produce `action.candidate` requests and consume `action.scored` results. Begin with a mock returning `unknown`/abstained; connect the real worker when the model team supplies its preprocessing and pose requirements. Run inference outside the API request loop with bounded queues/backpressure. Keep `candidate_id`, `model_id` and stable result identity through retries.

**Done when:** the worker can be swapped without changing counts/zones; timeouts and missing poses become visible quality failures; low confidence is never displayed as a confirmed action. Human acceptance verifies the observed action, not payment status or criminal intent.

## P5 — two-camera feasibility spike

Use a genuinely overlapping, fixed pair with verified relative time and calibration. Keep `(camera_id, stream_session_id, local_track_id)` separate from a temporary global visit ID. Start with world position, temporal overlap and physically plausible motion. Support unknown matches and association revisions; do not force a match to complete a route. A floor homography maps feet/floor positions, not hands on raised shelves.

Time-box an existing-stack comparison if NVIDIA runtime requirements fit: [DeepStream MV3DT](https://github.com/NVIDIA/DeepStream/blob/main/src/apps/reference_apps/deepstream-tracker-3d-multi-view/README.md) or [SceneScape](https://github.com/open-edge-platform/scenescape). Do not integrate both into the application. Preserve the adapter contract so either can be evaluated separately.

**Done when:** a recording with people crossing and shelf occlusion has a manual identity-switch/false-merge report; overlap counts are deduplicated only for sufficiently supported associations; unsynchronized input produces an explicit degraded state.

## P6 — RTSP and pilot hardening

Add reconnects, bounded buffers, dropped-frame reporting, clock/geometry health, evidence retention and measured performance. A reconnect creates a new stream session. Report freshness and coverage; keep analytics valid when one stream is down. Add observability before scaling to more cameras.

## Handoff after each task

Return changed source paths, one exact run command, test results, a short demo recording or sample output and actual limitations. Keep tasks small enough to review independently. Do not send the model workstream another full research survey or modify completed benchmark results to make a demo look better.
