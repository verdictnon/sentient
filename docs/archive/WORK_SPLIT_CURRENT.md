# Current work split

Updated 2 November 2026 after inspecting and executing the partner upload.

**Decision:** put new action-model training runs aside for this milestone. Keep V2/V5 and the research artifacts intact. Focus on a usable recorded-video pipeline and reviewed data.

## Partner: automatic people detection and tracking

Assignment: [PARTNER_NEXT_TASK.md](PARTNER_NEXT_TASK.md).

Deliver MP4 -> pretrained detector/tracker -> v1 records -> existing counts/dwell/queue logic and video overlays. The partner owns the application source under `Sentinel_partner/`. Completion needs a manually checked permitted continuous clip, not a synthetic accuracy claim.

## This model-workstream owner: review-to-dataset bridge

Own new root `scripts/` and corresponding root `tests/`; preserve partner application code and schemas.

Deliver:

1. A review-pack adapter for the existing 46 training clip groups in `runs/hand_detail_v6_scale512/touch_review_queue.jsonl`, using the P1 application's supported import/replay/review interfaces. Keep paired views linked by source group, retain media hashes/pose paths, and label source annotations as unverified suggestions. P1 is currently single-video review; do not claim synchronized paired-view review exists.
2. A reader for the application's `sentient.review/1` JSONL that validates the schema and frame/time/media/subject references, resolves latest review revisions and preserves reviewer provenance.
3. A separate model-readable dataset manifest for eligible reviewed action labels. Pending/rejected/unknown or unobservable cases require explicit handling; never turn an unreviewed source label or a mock model output into verified ground truth. Preserve source grouping and split roles; do not silently reassign development/test groups to training.
4. A small actual round trip: source training clip -> app review target -> saved review export -> validated manifest record. Automated/synthetic verification is labeled accordingly; real human adjudication requires an actual reviewer.

This closes the gap the partner explicitly documents: its exporter exists, but the model team's training-loader adapter has not been implemented. No new training run is part of this task.

## Shared integration milestone

One permitted continuous video -> automatic person tracks -> correctable count/dwell evidence -> review export. Separately, one existing action-review example must round-trip into the model-readable manifest. Both pieces use stable identities and actual source timing.

Keep the v1 transport and `sentient.review/1` stable. Use separate clones/branches or an explicit source handoff before simultaneous changes; this root directory still has no Git repository. Partner edits application files; this workstream edits root integration tools.

## Upload review completed

- All 70 archived source hashes match the supplied archive manifest.
- Original plan/contracts/fixture/validator match this workspace byte for byte.
- Fresh isolated Python 3.12 application environment installed successfully.
- Executed P0 replay/analytics checks, P1 HTTP/media checks and both launched Chromium playback/geometry/review/export workflows successfully. Total: 142 checks, no failures/skips.
- Tests ran in `Sentinel_partner/.local/review-copy/` so the uploaded source and historical browser evidence remain unchanged. Review logs are in `Sentinel_partner/.local/review-*-tests.log` and `review-p1-integration.log`.
- No person detector, tracker, real-video accuracy validation, paired-view synchronization or model-loader bridge is present yet. The app already supplies replay, SQLite persistence, MP4 indexing/playback, geometry editing and immutable human-review exports.
