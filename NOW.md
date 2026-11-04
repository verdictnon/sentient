# Current operations workspace

Use http://127.0.0.1:8766. Source remains app/ and web/; the existing
sentient-review-workspace.service runs the application. On any other laptop,
./run_demo.sh or run_demo.bat opens the simulated store. No models or footage needed.

The Store view is the home screen: one day or week, a Z-report, a floor map, the
pickup cross-check and rule-based next steps, in light or dark and English or
Russian. Simulated data is labelled everywhere and never shares a report with
real recordings. docs/PITCH_DEMO.md is the pitch run-through.

The v2 workspace adds track inspection and evidence, area transitions and signals,
persistent incident reviews, recording dates, shift/month/collection reporting in
PDF/Word/CSV/JSON, local schedules, system telemetry and model readiness.
See docs/OPERATIONS.md and README.md. The existing catalogue, geometry and models
are preserved; operations storage is additive.

All seven demo recordings remain independent research camera scenes. Capture
dates are unknown until explicitly assigned. Pickup transfer accuracy is still
unmeasured. RTSP ingestion, cross-camera identities, product/payment integration
and verified theft detection are not implemented. No new hour-long synchronized
market dataset or independent accuracy evaluation is claimed.

Run .venv-app/bin/python -m app.checks for application checks.
