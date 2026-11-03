# Partner integration handoff

The new P2 upload at `Sentinel/` was discovered during model-workstream execution. All 102 files in its archive manifest still match. No incoming application source was edited.

The tested integrated build is `runs/action_worker_v1/app/`. It merges the earlier Watch/playback fixes with P2 and adds:

- Deterministic CUDA workspace initialization for this receiving runtime.
- Optional scene size/NMS filters (disabled by default), recorded in tracking configuration and exposed in Tracking settings.
- Optional `isolated_run=true` on the existing replay route, plus `/api/status` capability detection; legacy append mode stays available.
- Direct video/analysis/target links, explicit tracking-job overlay composition, raw score display and stale-review clearing.
- Regression tests. The complete receiving application suite is 186 passing, including actual pretrained inference and launched browsers.

The raw continuous integration demo was actually run on a 131-second public MERL shopping clip. The model worker uses real exported V5 weights; it creates annotation-free proposals and abstained, uncalibrated score evidence under the unchanged v1 transport. Original tracking analytics are unchanged by action composition. No payment/theft claims or human labels were generated.

The sample scene filters remove many tiny/overlapping boxes on this inspected single-actor clip but still miss observations and fragment IDs. The 24-frame reference was assistant visual inspection, not independent human ground truth. Keep P2 release acceptance open. Validate a separate clip/reference rather than quoting this as a production accuracy gain.

Read `ACTION_WORKER_MILESTONE.md` for commands, exact runtime versions, artifacts and remaining work. Research sources and overhead-specialist alternatives are in `OPEN_SOURCE_ROUTE_2026-11-02.md`.

Reapply the source overlay to a fresh copy of this exact upstream upload using `scripts/build_integrated_app.py`; the builder verifies its hashes. If your working copy has moved beyond that upload, merge the changed files deliberately using the overlay and manifest. Do not overwrite newer P2 edits wholesale. Source bundles are under `runs/action_worker_v1/`.

The app environment on this machine now lives at root `.venv-app`, so replacing an uploaded application directory no longer deletes it. The original review workspace remains on port 8766, with all 85 videos and its original review export preserved. The scored/demo workspace is separate on port 8767; the linked priority queue is on port 8768. No external message was sent to the partner.
