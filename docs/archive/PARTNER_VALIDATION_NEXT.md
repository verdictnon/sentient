# Partner next task: validate P2 on independent public footage

Your P2 implementation has arrived in the receiving workspace. The receiving owner is finishing the single Analyze video -> tracking -> action scores -> review flow. Own validation while that UI/API integration is in progress.

## Copy-paste assignment

> Validate the existing P2 detector/tracker on a different permitted public continuous clip, without rebuilding the application or training a model. The receiving owner already ran MERL Shopping 1_1_crop.mp4 and inspected it, so do not use that clip as an independent test. Select another continuous clip; a different MERL subject/session is a small starting point, and a small WEPDTOF sequence is a stronger later crowded/overhead check once its terms are understood. Record original publisher, exact file/revision/hash and permitted use. Freeze a short evaluation interval, diagnostic geometry and reference protocol before comparing settings. Establish visible-person/count references and identity continuity over that interval; identify the assessor honestly as human or automated. Automated inspection is useful but is not independent human ground truth. Compare the original RT-DETRv2-S/ByteTrack-style configuration with the optional scene filters (min_person_area_ratio=0.002, person_nms_iou=0.4) that helped the inspected MERL clip. Do not retune those filters on the new check interval and then report it as an independent gain. Report observed coverage, missed people, duplicate/false boxes, ID fragmentation/switches where the reference supports it, counting errors, source-time gaps, processing time and peak memory. Distinguish predicted boxes from detections. A virtual line in a cropped shopping scene is not a store entrance; do not call its counts customer entries/exits unless the scene and reference support that interpretation. Return a runnable validation command, reference/provenance files, both configurations, a machine-readable report, a few timestamped failure examples, and an explicit acceptance decision/remaining limitations.

## Ownership while integration finishes

- Work in your own checkout and new `scripts/validation/`, `tests/validation/`, `docs/app/validation/` paths, or equivalent isolated validation folders.
- Avoid concurrent changes to `web/`, app APIs, tracking implementation/config dataclasses, root model code, schemas and checkpoints. Record a blocker as a focused proposed patch instead of mixing it into the UI integration.
- Read `ACTION_WORKER_HANDOFF.md` and the current integration overlay when transferring code. The receiving build has CUDA workspace initialization, optional filters and isolated replay fixes; merge those deliberately. The original uploaded `Sentinel/` is untouched here.
- Keep action-model training, model sweeps, multi-camera association, RTSP, payment/theft conclusions and new dashboard features outside this task.

## Done when

1. A different public continuous source is actually processed by the existing real pretrained detector/tracker.
2. Baseline and scene-filter results are compared against a stated reference with uncertainty/unknown intervals preserved.
3. Failures and identity/count errors are recorded; no unsupported accuracy percentage or P2 PASS is claimed.
4. The validation can be reproduced from pinned source/model/config/video hashes without private footage or multi-gigabyte data transfer.

Source leads and terms: `OPEN_SOURCE_ROUTE_2026-11-02.md`. Actual receiving integration evidence: `ACTION_WORKER_MILESTONE.md`.
