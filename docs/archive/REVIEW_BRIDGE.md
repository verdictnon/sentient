# Review-to-dataset bridge

The original unscored pack remains available as described below. The newer real-score queue and continuous integration are documented in [ACTION_WORKER_MILESTONE.md](ACTION_WORKER_MILESTONE.md).

The root model workstream owns this tool. It uses the partner application's existing P1 HTTP APIs and `sentient.review/1`; no partner source or contract changes are required. Model training remains set aside.

## Ready now

- Review pack: `runs/review_pack_v2/pack.json` — **46 source-training groups, 85 views, 171 unverified source-event suggestions**.
- Subject-pose contact sheets: open `runs/review_pack_v2/index.html` in a browser. Red rectangles/yellow points identify the **supplied**, unverified subject pose. Both views share a source group; review them separately. P1 does not synchronize paired views.
- Populated app workspace: `runs/review_workspace/`, with receipt `runs/review_workspace_receipt.json`. It contains all 85 registered videos and 171 unscored review targets, **no human decisions** and no eligible new training labels.
- Local app: **http://127.0.0.1:8766**. It currently runs as the transient user service `sentient-review-workspace` so it survives chat turns. After logout/reboot, use the launch command below. See [UI_PLAYBACK_FIX_HANDOFF.md](UI_PLAYBACK_FIX_HANDOFF.md) for service status/stop/log commands.
- Clips open in Watch with scene guides hidden. Press Play, then select an event below to open Review. Show zones reveals fixed camera guides; Track people now runs the integrated P2 detector/tracker; action scores are loaded separately as review suggestions.

Videos are referenced in place under `data/retailaction_v3/`; the pack/workspace do not duplicate the MP4 files. Keep the project/data available. Camera labels such as `train-003157-rank0` distinguish public example views; these are review identifiers, not recovered physical camera identities.

The original source labels remain unverified suggestions in the pack and review-target reason. Every submitted target is `action=unknown`, `scores=null`, `abstained=true`, `quality=mock`, `score_semantics=unavailable`. No fake person detections or confirmed physical events are generated.

## Environment and launch

Run commands from the project root unless a `cd` is shown. The existing isolated Python 3.12 application environment contains the bridge dependencies. The model Torch/checkpoint versions are preserved; worker HTTP/validation dependencies were added separately. The current application/bridge environment lives at root `.venv-app`.

```sh
# Start or restart the populated local review workspace.
python scripts/start_local_workspace.py
```

For a separate bridge environment, install `requirements-review.txt`. Browser verification additionally uses `requirements-review-test.txt` and `python -m playwright install chromium --only-shell`. Verified versions are Python 3.12, PyAV 19.0.2, Pillow 13.0.0, jsonschema 4.25.1, httpx 0.28.1 and Playwright 1.64.1. The CLI itself does not require a GPU, Torch or Playwright.

## Review an example

1. Read its paired-view cards in `runs/review_pack_v2/index.html`. Check the designated person, item visibility, whether the item stays/leaves/returns, and boundaries. A source suggestion is not a verified label.
2. In the app select the matching camera label from the media library. Wait for the recording to finish loading. Select an `action.scored / unknown` row in its event table.
3. Inspect before/during/after and the relevant actual frames. Choose the action and correct the span where needed. Enter a real reviewer name and appropriate visibility/uncertainty. Save a revision. If the action cannot be adjudicated, keep it pending/unknown or mark it unobservable; do not force a class.
4. Review the other view independently. A clear action in one camera does not automatically label an occluded camera.

MP4 PTS/media time is preserved exactly. These public clips were sparsely sampled; playback cadence does **not** establish original capture intervals. Capture UTC and elapsed capture times remain unknown. Frame indices are zero-based and source poses remain separately checksummed.

## Export, declare review origin, build

Export through the app's button, or use the bridge to collect all registered pack-view exports:

```sh
.venv-app/bin/python scripts/review_bridge.py export \
  --receipt runs/review_workspace_receipt.json \
  --output runs/review_workspace_exports.jsonl
```

The app calls its envelope `human`, but an HTTP client or automated test can submit that same shape. The bridge therefore also requires an explicit declaration of review origin for training eligibility. After an actual human has checked the designated subject, class, visibility and span, record that declaration for their latest submitted revisions:

```sh
.venv-app/bin/python scripts/review_bridge.py attest \
  --reviews runs/review_workspace_exports.jsonl \
  --reviewer "YOUR REVIEWER NAME" \
  --kind human --attested-by "PERSON CONFIRMING THE REVIEW" \
  --output runs/review_human_attestations.jsonl
```

Use this only for actual human adjudication. An attestation is a named provenance assertion bound to the exact review payload, not an authenticated signature. A later edit/revision needs its own declaration. `--annotation-id ID` can be repeated instead of selecting a reviewer. Reviewer selection happens **after** latest-revision resolution; it never resurrects an older decision owned by that reviewer. Automated verification uses `--kind automated_fixture` and is excluded from training.

```sh
.venv-app/bin/python scripts/review_bridge.py build \
  --pack runs/review_pack_v2/pack.json \
  --receipt runs/review_workspace_receipt.json \
  --reviews runs/review_workspace_exports.jsonl \
  --attestations runs/review_human_attestations.jsonl \
  --output runs/reviewed_training_manifest.json
```

`--reviews` may be repeated to combine exports. Export all revisions or the latest-only snapshot; the reader resolves the greatest revision per target/analysis, irrespective of file order. Include the newest export when rebuilding: a manifest is an immutable snapshot and does not monitor later app edits.

Only **accepted/corrected**, supported action/background labels with **clear** visibility, **low** uncertainty and an exact **human** origin declaration enter the training manifest. Pending/rejected/unknown/non-action labels, partial/occluded views, uncertain reviews, undeclared origins and automated fixtures have explicit exclusion reasons. Rejection does not become background. Old accepted decisions do not survive a later pending/rejected revision. Conflicting current decisions across geometry analyses require explicit adjudication.

## Model-readable supervision

```python
import sys
sys.path.insert(0, "scripts")
from review_bridge import load_dataset

examples = load_dataset(
    "runs/reviewed_training_manifest.json",
    "runs/review_pack_v2/pack.json",
    "runs/review_workspace_receipt.json",
)
```

Each returned example identifies the original MP4/pose paths, media/pose hashes, designated subject, camera view, immutable source group and role, actual frame presentation times, annotation IDs, three-class `targets` and equally shaped boolean `label_mask` arrays. Shapes are `[3, original_frame_count]`; classes are `take`, `put`, `touch`.

- A reviewed action sets a positive target/mask for **that action and reviewed frames only**. Other actions/frames stay unknown.
- Explicitly reviewed background sets negative targets for all three classes **within that reviewed span only**.
- Overlapping positive and background constraints fail before output is committed.
- No label propagates to the paired camera, no unreviewed interval becomes background, and group roles are verified against the frozen V6 source index. A `train/` filename alone cannot promote a development example.

This is the data-loader boundary for future masked multi-label training. It does not modify the locked V2–V8 experiment loaders or run an optimizer. Any future loss must use `label_mask`; zero target values outside the mask are not negative labels. New evaluation folds still need their own explicit, group-preserving plan.

The reader validates envelope/schema, finite values, media/pose hashes, source grouping, subject identity, PTS/time bases, exact frame membership in the reviewed span, immutable embedded source/model records, analysis/geometry bindings and revision conflicts. Tampered or mismatched input fails closed. Group/session dependence is preserved; this does not create new shopper-independent data.

## Prepare/import another copy

```sh
.venv-app/bin/python scripts/review_bridge.py prepare \
  --output runs/another_review_pack
.venv-app/bin/python scripts/review_bridge.py import \
  --pack runs/another_review_pack/pack.json \
  --receipt runs/another_review_receipt.json \
  --app-url http://127.0.0.1:8766
```

Use `--limit-groups 1` for a small pack/import. Repeating the same preparation is deterministic; different inputs require a new output directory. Reimport is idempotent, including a stable receipt on exact retries. A changed geometry creates a separate analysis binding. Preserve the receipt with its pack/export. A source schema/index/hash change intentionally requires a new verified pack. Relative pack asset references resolve inside this project; path traversal and missing assets are rejected.

## Executed verification

The successful real-source-clip browser/API round trip is recorded in `runs/review_bridge_verification_final/verification.json`, with a screenshot and three saved review revisions. It uses a separate app workspace and the previously verified application snapshot, preserving both the partner's source and historical evidence.

It verifies import/save retries, exact original media identity/timing, browser review submission, export validation, latest pending retraction, mask construction and fixture exclusion. The decisions deliberately say **AUTOMATED BRIDGE INTEGRATION FIXTURE**. There were **zero human adjudications and zero eligible training records**. Its separate `integration-fixture-dataset.json` contains one validated diagnostic record and is rejected by the default training loader; loading it requires explicit `allow_fixtures=True`.

Repeat the browser/API check into a **new** directory:

```sh
.venv-app/bin/python scripts/verify_review_bridge.py \
  --output runs/review_bridge_check_new
.venv-app/bin/python -m unittest discover -s tests/review -v
```

The regression suite covers source-role leakage, malformed/duplicate/nonfinite JSON, media/pose changes, frame/subject/time/source-record mismatches, stale/conflicting revisions and attestations, missing origins, fixture separation, cross-view independence, background masks, overlapping contradictions and manifest tampering. These are data-integrity checks, not recognition accuracy measurements.

## Scope kept separate

Partner owns detection/tracking, app UI and application schemas. This work adds only root bridge tools/tests/docs, generated review artifacts and a separate workspace. No messages were sent to the partner, no original labels/checkpoints were changed and no model was retrained. RetailAction's existing license/provenance remain applicable to the media and derived data.
