# Completed milestone: real video to tracks, action scores and reviewed evidence

Completed 2 November 2026, on the local RTX 4050 Laptop (6 GiB). No model was retrained and no paid/cloud compute was used. The newer `Sentinel/` P2 upload appeared during this work; its 102 archived files were verified and left byte-for-byte unchanged. The integrated application is a separate generated build.

Start here: **http://127.0.0.1:8766**. Select/import an MP4, choose continuous versus sparse sampling under Analysis settings, and click **Analyze video**. Tracking, actual action scoring and review preparation run as one durable application job with progress, cancellation and errors. On completion, the app opens the scored source window with its person boxes. No CLI handoff is required.

A fresh browser-initiated run of the 131-second public MERL clip completed in **87.38 seconds**, including uncached tracking, with **91 real scored windows**. Exact completed requests reuse the result. Job evidence: `runs/action_worker_v1/one-button-primary-job.json`; screenshot: `runs/action_worker_v1/one-button-workspace.jpg`. Scores remain unknown/abstained until reviewed.

Partner assignment: [PARTNER_VALIDATION_NEXT.md](PARTNER_VALIDATION_NEXT.md). Research: [OPEN_SOURCE_ROUTE_2026-11-02.md](OPEN_SOURCE_ROUTE_2026-11-02.md). Prior scored workspace/queue at ports 8767/8768 remain available as diagnostic archives; they are not required by the normal app flow.

The concrete path now works: **public continuous MP4 → original pretrained RT-DETRv2-S → camera-local tracks → counts/dwell evidence and annotation-free sliding windows → actual exported X3D/V5 scores → frame-aligned boxes and score display → versioned review/export**. An automated source-training review fixture also round-trips through the existing masked dataset loader. Automated fixtures and unreviewed model suggestions do not become training labels.

## What shipped

`python scripts/action_worker.py` exports the existing trusted checkpoint and runs a portable TorchScript worker on an MP4 plus original v1 track/health records. Inference needs no training-module imports. The exact selected V5 KNN bank/head and bias are retained; PyTorchVideo's custom Swish autograd wrapper is exported as the identical `x*sigmoid(x)` operation. Actual source-crop checks and eager/export checks produced zero numerical difference.

The worker proposes windows without action labels or contact points. Continuous inputs sample elapsed source time; sparse clips explicitly retain unknown physical cadence. Source PTS/dimensions/frame references are checked, predictions are excluded from observed evidence, stream gaps/session boundaries are respected, and frame/window/batch bounds prevent unbounded frame accumulation. Missing inputs produce abstention rather than background. Model outputs retain raw uncalibrated scores with `action=unknown` and `abstained=true`.

The integrated app preserves Watch mode, optional fixed geometry, and the previous playback/race fixes while adding the partner's real tracking controls, exact source-frame person overlays and analysis history. It displays the actual model scores, supports recording/analysis/target links, clears stale review forms on analysis changes and preserves model evidence in review exports. A tracking-job link can explicitly display its source-aligned boxes alongside the action analysis composed from that same output.

Three issues found during real integration were corrected:

1. CUDA deterministic inference required `CUBLAS_WORKSPACE_CONFIG` before Torch/CUDA initialization in this Linux runtime. The original real-GPU browser test failed before the fix and passed afterward.
2. Replaying another offline detector/model snapshot into the same geometry analysis attempted to reopen a retired stream. `POST /api/replays` now offers optional `isolated_run=true`, keyed by exact JSONL bytes. The default legacy append behavior and its identities remain available. `/api/status` advertises `isolated_replays`; worker packs require it instead of silently using an incompatible app.
3. Tiny false detections and overlapping person boxes created extra tracks on the overhead clip. Optional `min_person_area_ratio` and `person_nms_iou` settings are recorded in run scope, bounded/validated and disabled by default. They are available in Tracking settings. The supplied MERL diagnostic configuration uses 0.002 minimum area and 0.4 overlap suppression; this is a scene-specific diagnostic choice, not a universal setting.

## Actually measured

| Run | Result | Time / memory | Meaning |
|---|---|---|---|
| Public source-training queue | 46 groups, 85 views, 407 real scored windows | 33.70 s overall; 178.72 MiB peak PyTorch allocated GPU memory | Review triage on sparse supplied-subject clips; not continuous action accuracy |
| Original continuous detector run | 3,920 frames, 654 detector frames; 2,308 observations and 12,130 predictions | 60.05 s tracking; 185.61 MiB allocated / 234 MiB reserved GPU; 1.35 GiB peak process RSS | Baseline diagnostic exposed duplicate/tiny boxes |
| Scene-filtered continuous detector run | Same source; 589 observations, 3,142 predictions | 52.26 s tracking; actual runtime/model/config recorded in the job | No ground-truth identity/count guarantee |
| Filtered action worker | 91 windows from observed tracks; predictions excluded | 17.93 s total for this action-worker run; 178.72 MiB allocated GPU | All 91 outputs remain unknown/abstained suggestions |
| Filtered composition/replay runner | Preserved P2 analytics and all original records; retry added zero records/events | 38.34 s with an already-completed cached tracking job | This is not a cold end-to-end timing; do not add or extrapolate timings without accounting for cache/setup |

The external video is `MERL Shopping / 1_1_crop.mp4`: 920×680, 3,920 decoded frames, last source PTS 130.633 s, 15,730,436 bytes. It was downloaded from a pinned Voxel51 mirror; original publisher, revision, SHA-256 and research-use scope are recorded in `data/public_continuous/merl/provenance.json`. Its reach/retract/inspection labels were not merged into take/put/touch supervision. It remains quarantined as an external diagnostic.

A small **assistant visual count diagnostic**, not independent human ground truth, sampled 24 frames at 5-second intervals where one person was visibly present. The baseline had multiple observed boxes in 23/24 sampled frames; the configured run had 0/24 multiple-box frames, with two zero-observation frames remaining. Sampled observed-count MAE went from 2.708 to 0.083. The configuration was chosen after seeing this clip. These values are not production accuracy, box precision or an independent test gain. Whole-clip IDs fell from 15 to 4, but no human-labeled identity-switch count is claimed. Reference/samples: `runs/action_worker_v1/continuous-count-diagnostic.json`.

The virtual line is explicitly named **DIAGNOSTIC line x=460; not an entrance**. Its reported three crossings each way are algorithm results on that virtual line, not verified store entry/exit counts. Dwell is censored; it must not be treated as completed visits or shopper purchase behavior. Queue service inference remains disabled without independent service evidence.

## Verification and evidence

- **189 integrated application checks passed**, including actual pretrained CPU/GPU execution, browser playback/overlays, P0/P1/P2, four loading regressions, isolated replay, scene filters and combined-job failure/cancellation/restart checks. `runs/action_worker_v1/one-button-app-checks.log`.
- **20 worker checks passed**: original schema, exact PTS, nonzero/VFR source times, retries, crop/padding, gaps/healthy heartbeats, identity scope, prediction exclusion, missing evidence, runtime/checksum failures, and bounded memory behavior. `worker-tests-release.log`.
- **19 review-bridge checks and 24 existing model checks passed**. No failures/skips in those suites. `review-tests-release.log`, `model-tests.log`.
- Actual-source input/model parity: three windows, zero maximum input and score difference from original V5; batch-1/2/3 eager/export parity also zero. `real-input-parity.json`, `model/manifest.json`.
- Real-score browser review/export → verified masked dataset round trip, including stale-review clearing on analysis changes: `release_roundtrip/verification.json`.
- Actual continuous P2-plus-action source → isolated pending review → original review-schema export: `continuous_export_verification/verification.json`.
- Browser inspection verified playing video, moving observed/predicted boxes, hidden still frame during playback and exact overlay/frame agreement after pause. `browser-visual-verification.json`.
- Original 8766 workspace still has 85 videos and a byte-identical pre-upgrade annotation export. A backup was taken before migration. `workspace-upgrade-verification.json`.
- Operational scored queue has **zero human reviews** and **zero eligible new training labels**. `human-reviewed-dataset.json`. Automated verification decisions live in separate workspaces.

These are engineering results plus a small inspected diagnostic, not a model-accuracy percentage. P2 acceptance on intended/crowded camera footage and reliable pickup/return detection remain open.

## Run it

All current workspaces run as loopback-only transient user services, independent of chat command sessions. They are not installed as boot/login services. After logout/reboot:

```sh
python scripts/start_local_workspace.py
```

For another permitted video, use the main app’s Analyze video button. Configure saved scene geometry for the actual camera when counts/dwell matter; starter shapes have no entrance meaning. Declare sparse clips explicitly.

The CLI is also available for repeatable diagnostics:

```sh
.venv-app/bin/python scripts/run_recorded_pipeline.py \
  --video /absolute/path/to/permitted.mp4 \
  --source-kind continuous \
  --output runs/my_new_pipeline \
  --model runs/action_worker_v1/model \
  --app-url http://127.0.0.1:8767
```

Use a fresh output directory. Configure geometry for the actual camera first; otherwise inherited starter shapes have no entrance meaning. Scene filters are optional; pass a reviewed configuration with `--tracking-config` when appropriate. The runner registers the video, reuses an identical completed P2 job, downloads original records, invokes the model environment, creates an isolated combined analysis and verifies replay idempotency and unchanged tracking analytics. It writes `milestone.json` and a direct review URL.

To run the action worker independently after receiving the partner's tracks:

```sh
.venv/bin/python scripts/action_worker.py run \
  --video /absolute/path/to/permitted.mp4 \
  --tracks /absolute/path/to/tracks.jsonl \
  --source-kind continuous --model runs/action_worker_v1/model \
  --output runs/my_new_action_run
```

To regenerate source-queue scores, use `action_worker.py pack --model ... --output NEW_DIRECTORY`. Import using `review_bridge.py import`, then call `worker.pack.link_pack` to attach exact review URLs. The existing review bridge handles human origin declarations, latest revisions, retractions and masked supervision. Do not claim to have performed human review when executing verification scripts.

## Environments and source handoff

The model-worker CLI and its process-memory profiling were executed on Linux; a Windows CLI run is not claimed. The TorchScript model artifact is independent of the training scripts. The root model `.venv` keeps its original torch 2.7.1/torchvision 0.22.1; only worker validation/HTTP dependencies were added. `requirements-action-worker.lock.txt` records the resulting environment. The receiving application environment is now **root `.venv-app`**, outside replaceable uploads, using Python 3.12, torch 2.7.1+cu126 and torchvision 0.22.1+cu126. This differs from the partner's Windows torch 2.11 environment; all 186 integration checks were actually rerun on the receiving versions. Exact application packages: `runs/action_worker_v1/app-runtime.lock.txt`.

The unchanged uploaded P2 source is `Sentinel/`. The generated integrated build is `runs/action_worker_v1/app/`. Reproducible source changes live in `integration/app_overlay/`, with a pinned upstream manifest and builder. Use `scripts/build_integrated_app.py` to rebuild into a new directory; it verifies the received source before applying the overlay. The source handoff bundles contain no environments, raw video, model checkpoints, workspace DBs or credentials.

## What to do next

Use the top few groups in the linked queue for real human adjudication, preserving paired-view grouping and checking subject, visibility, item stays/leaves/returns and timing. Separately validate the detector/scene configuration on another continuous clip with independent reference counts and misses/identity review. The partner should merge the integration corrections and finish P2 acceptance rather than rebuild the pipeline. Then run one frozen-protocol visual-fine-tuning/manipulation-transfer comparison on reviewed labels. Another classifier sweep or a cloud GPU purchase is not the next bottleneck.
