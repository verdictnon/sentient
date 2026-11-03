# Playback UI fix for the partner

2 November 2026. The user reported a stationary overlay after selecting a video and explicitly requested a fix. This is a narrow frontend correction after the already-shared work split; the partner's P2 tracking assignment remains the same.

## What was wrong

Every clip opened Geometry with generic entrance/floor shapes. Those are fixed camera guides, not detections, and never followed people. Nothing in the player made that distinction clear.

Two actual loading bugs were also reproduced against the original uploaded frontend: a delayed frame request paused active playback and covered it with a still; delayed metadata from an earlier clip replaced the most recently selected clip.

## Changed source

- `Sentinel_partner/web/app.js`: Watch is the initial mode; fixed geometry is optional through Show zones and editable through Geometry. Switching clips clears stale imagery and invalidates pending clip/frame/geometry/analysis/review responses. Starting playback invalidates pending frame requests. Frame stepping after playback uses the current source position. Navigation highlights and playback frame text follow the current mode.
- `Sentinel_partner/web/index.html`: Watch instructions, optional zone control, explicit tracking status, explanation of starter geometry, asset version queries for reloads.
- `Sentinel_partner/web/style.css`: styling for the control and Watch instructions.
- `Sentinel_partner/tests/p1/test_browser.py`: existing drawing workflow explicitly enters Geometry.
- `Sentinel_partner/tests/p1/test_playback_regressions.py`: four launched-browser regressions for optional guides, delayed frames, delayed clip metadata and delayed geometry.

No backend API, transport schema, geometry format, saved camera geometry, model weights or training data was changed. The original archive manifest remains historical evidence and will now report four deliberately changed archived files plus the new test file.

## Verification

The original page fails the delayed-frame and delayed-metadata tests for the reported reasons (`runs/ui_playback_fix/baseline-original.log`). The fixed application passes all **146 application checks**, including the previous playback/edit/review/export workflows and four new regressions (`runs/ui_playback_fix/all-checks.log`). These are engineering checks, not model-accuracy results.

The real RetailAction clip -> browser review -> exported revisions -> validated dataset round trip also passes with the fixed frontend (`runs/ui_playback_fix/bridge_verification/verification.json`). Its decisions are isolated automated fixtures, never human adjudications. The verifier's older `app_code_modified=false` field means it did not edit its input app during that run; the input app was already patched. Exact patched input hashes are recorded in `runs/ui_playback_fix/summary.json`.

Tests ran in `runs/ui_playback_fix/test_app/` with separate temporary workspaces. Historical uploaded demo screenshots and the operational review history were preserved. The operational workspace is still `runs/review_workspace/` at http://127.0.0.1:8766 while the server runs. Visual verification: `runs/ui_playback_fix/fixed-workspace.jpg`.

## Integrating with P2

A scoped source bundle is at `runs/ui_playback_fix/partner-ui-fix.zip` (five source files plus hashes). Merge these changes with any P2 edits, especially `web/app.js` and `web/index.html`, rather than replacing your newer P2 implementation wholesale. Keep person boxes/IDs separate from these optional fixed scene guides. Replace the tracking-not-connected text once a real tracking action and frame-aligned outputs exist.

Run from the application checkout with its isolated environment:

```sh
.venv-app/bin/python -m app.checks
```

To run the operational review workspace from this workspace:

```sh
cd Sentinel_partner
.venv-app/bin/python -m app.p1.server --port 8766 --data-dir ../runs/review_workspace
```

## Local server lifetime

The live app now runs as the transient user service `sentient-review-workspace.service`, outside temporary command sessions. It listens on 127.0.0.1:8766 and restarts on failure. This does not install a boot/login service; after logout or reboot, use the foreground launch command above or create the transient service again.

```sh
systemctl --user status sentient-review-workspace
systemctl --user stop sentient-review-workspace
journalctl --user -u sentient-review-workspace -n 50
```

The final read-only check found all 85 registered videos and zero review revisions in the operational workspace.
