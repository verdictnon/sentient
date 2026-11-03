# Partner next task: P2 automatic people tracking

Assignment agreed 2 November 2026. The application upload is at `Sentinel_partner/` in this workspace; use the equivalent application checkout on your machine.

## Starting point

P0 replay/persistence and P1 video/geometry/review are implemented. The receiving model-workstream machine reproduced all 119 P0 checks, 21 HTTP/media integration checks and both launched-browser workflows on Linux/Python 3.12. Original handoff files match byte for byte. This verifies application behavior on synthetic examples, not detector accuracy.

The largest missing link is **MP4 -> automatic person detections/tracks -> existing analytics and evidence UI**. Work on that link; do not rebuild P0/P1 or train an action model.

## Copy-paste assignment

> Implement P2 automatic person detection and camera-local tracking in the existing Sentient application. Read this file, docs/app/P1.md, PARTNER_START_HERE.md P2, and contracts/v1/. Preserve the existing replay, timing, geometry, review history and exports. Add a pretrained person detector behind an adapter and ByteTrack-style tracking; start with the implementation/checkpoint choice documented in the partner brief, record exact versions/weight provenance, and document one fallback if compatibility requires it. Do not start a detector/model sweep or train weights. A recorded MP4 must produce actual frame-aligned track.observed records and stream.health records under the unchanged v1 contract, then run through the current P0/P1 analysis flow. Add a clear Track people action, progress/error state and visible bounding boxes/local IDs during playback/inspection. Keep action results explicitly unknown unless a real worker supplies them. Finish with one runnable demo command and an honestly measured result on one permitted continuous video containing people.

## Ownership

- Own `Sentinel_partner/app/`, `web/`, `configs/`, application dependency manifests, `tests/` and `docs/app/`. Prefer a new `app/p2/` adapter/job package and corresponding tests.
- Keep the application environment separate from the root training `.venv`.
- Do not edit root model scripts, datasets, checkpoints, experiments or the original v1 schema/fixture.
- The model-workstream owner builds the external review import/export bridge in root `scripts/` and `tests/`. Preserve P1 APIs and `sentient.review/1`; flag a required interface change before implementing it independently.

## Done when

1. A user imports an MP4, draws/saves geometry and runs automatic tracking without manually supplying tracking JSONL.
2. Person boxes and camera-local IDs appear on the correct original frames. Resize/letterbox output is mapped back to original image coordinates.
3. The adapter preserves frame index and actual source PTS/media time under the existing contract. Sample by elapsed media time, not assumed 30 FPS. If the wire timestamp precision and exact P1 attachment disagree, document and resolve that interface explicitly; do not silently round or invent capture UTC.
4. Observed detector positions and tracker-only predictions remain distinct. Local IDs are scoped by store/camera/stream session; no cross-camera identity claim.
5. Generated records drive existing entrance/dwell/queue logic. Replaying the same output creates no duplicate inputs/events; source/model/config changes produce a separate analysis.
6. One small permitted **continuous** video with actual people is manually checked: report entrance/exit counts versus the manual count, obvious ID switches, missed people, processing duration and memory. Report actual errors; no arbitrary accuracy target is already achieved. Sparse RetailAction clips and the synthetic dot demo are not continuous people-tracking accuracy evidence.
7. P0/P1 workflows remain passing, P2 checks run through the application check command, and inference failures/empty scenes/missing weights have explicit outcomes.

Keep processing offline and bounded for this milestone. Cross-camera association, RTSP, cloud hosting, SKU recognition and action-model training come later.

Return changed paths, setup/run instructions, one small sample output, detector/weight provenance and measured limitations. Do not package environments or private recordings.
