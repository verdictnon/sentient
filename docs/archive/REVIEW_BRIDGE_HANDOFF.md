# Interface note for the partner — existing assignment unchanged

The P2 detector/tracker task in `PARTNER_NEXT_TASK.md` and ownership in `WORK_SPLIT_CURRENT.md` remain unchanged. This note documents the completed model-workstream bridge; no new app feature is requested by it.

The bridge uses only these existing interfaces:

- `POST /api/videos/import` with source path, store/camera/session and collection grouping.
- `GET /api/videos/{id}/frames` to verify original index/PTS/time base.
- `POST /api/replays` with `video_id` and a v1 JSONL upload.
- `GET /api/analyses/{id}/events` to bind saved review targets.
- Existing review form / `POST /api/videos/{id}/reviews`.
- `GET /api/annotations/export?video_id=...` returning `sentient.review/1` revisions.

Public-source suggestions enter as `action.candidate` plus abstained `action.scored` records with `action=unknown`, `scores=null`, `quality=mock`, `score_semantics=unavailable`. These records create review targets only; they generate no physical action confirmations or person-track analytics. Source annotations remain unverified sidecar suggestions. Supplied-subject previews live in the external pack.

The training side verifies source/media/subject/frame/analysis bindings and uses the latest review revision, including retractions. Human origin declarations are stored separately and bound to exact review payload hashes. No schema field was added to the app. Unreviewed intervals are masked; they are not background. Automated integration reviews are excluded from training.

Please preserve these interfaces and revision/source-timing semantics while adding P2. If an intentional incompatible change is needed, document the versioned boundary so the bridge can adapt; do not edit root bridge tools or model data as part of P2.

Operational instructions and completed verification: `REVIEW_BRIDGE.md`. The ready review pack contains 46 groups / 85 views / 171 source-event suggestions. It is separate from the synthetic P1 demo and from the isolated automated verification workspace.
