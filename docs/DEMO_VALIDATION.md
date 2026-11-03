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
