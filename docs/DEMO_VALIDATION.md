# Store view validation — 4 November 2026

This covers the Store view, pickup cross-check, simulated store, light theme and
Russian interface. It was checked on Linux with Python 3.13, without the project's
models or footage.

## Automated checks

`python -m app.checks` ran 245 tests and 243 passed. The other two need the installed
models and fail the same way without this work:
`test_pinned_original_weights_load_and_run_on_cpu` and
`test_known_sparse_source_rejects_continuous_tracking_and_action_requests`.

New coverage includes rollups split at local midnight, unknown values staying unknown,
real and simulated stores staying apart in rollups and reports, cross-check outcomes,
Russian coverage of the interface, the first-recording steps, both themes, the map
layers, the Z-report PDF and 390 px layouts with no horizontal overflow.

## Clean run

`./run_demo.sh` ran from a clean copy with no environment: 100 seconds to a working
store, with a 380 MB Python environment and a 5.5 MB workspace. The script then rebuilt
in 77 seconds with review clips included.

## Rehearsal

The steps in docs/PITCH_DEMO.md were walked with real clicks at 1366×768 and
1920×1080 on a freshly built store, with no JavaScript errors or failed requests. The
case clip played as the dialog opened. In a full simulated live run, the first
"left without reaching a till" notice appeared 56 seconds in.

Text contrast was computed for both themes: every text and background pair is at
least 4.5:1. Report centre PDF, Word, CSV and JSON for the demo store carry the
simulated label; the PDF and Z-report were rendered and read.

## Not checked

`run_demo.bat` has not been run, and neither has any launcher on macOS. The new views
have not seen real footage through the real detector and pickup model, because neither
is available here. The simulation exercises the analysis code with generated tracks, so
it says nothing about detector accuracy, tracker identity switches or pickup precision.

# Operations workspace validation — 3 November 2026

The existing application was extended in app/ and web/. The service remains
available at http://127.0.0.1:8766.

## Automated checks

`.venv-app/bin/python -m app.checks` — 207 tests passed.

Coverage includes the retained analytics/tracking/media contracts plus observed
track histories, gaps and sparse-time handling, area transitions, queue-to-checkout
observations, overnight/month-end scheduling, explicit recording dates, clipping
calendar windows at midnight, duplicate-run selection, optimistic reviews,
immutable report snapshots, scheduler idempotency, missing artifacts and PNG
source evidence. Browser checks cover import, geometry editing, saved live runs,
heatmaps, seeking, tracking investigation, reviews, PDF/Word downloads, report
previews, schedules and mobile navigation.

## Actual-model smoke test

The default real 720p CARR recording completed source-paced live inference with
40 sampled frames, one observed local track and one pickup candidate. A person
box was selected during inference and its focused state rendered. The saved run
was then available for investigation.

A separate full-video person-tracking job with confidence 0.55 completed against
the real detector. Its track history was inspected and its saved URL restored
after reload. Pickup scoring correctly remained unavailable for that run.
No JavaScript errors or failed HTTP requests were observed in this smoke test.

Monitor, Track explorer, Analytics, Investigations, Report centre, Camera setup
and System views were checked at 390px; none overflowed horizontally. Desktop
tracking, analytics and reporting views were visually inspected at 1512px.

## Documents

A collection report was generated from all seven real demo recordings. Its
snapshot contains 16 observed camera-local IDs, seven pickup candidates and
96.0 seconds of analysed source-time intervals. These recordings are separate
research scenes; the report is not attributed to one store shift or month.
The report is saved in Report centre in PDF, Word, CSV and JSON formats.

Both PDF and Word render to three pages. All Word pages were rendered and
visually inspected, including table wrapping, title, page flow and footers.
The PDF was rendered and checked. Word's template title border was removed and
portable PDF fonts are bundled. Source runs, model IDs, geometry and reviews are
included in the JSON snapshot.

## Limits

This verifies execution, integration and reporting semantics, not real-store
pickup accuracy or unique-customer counting. Current limits and the operating
workflow are documented in docs/OPERATIONS.md and docs/DATA_SOURCES.md.
