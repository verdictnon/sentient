# Sentient operations workspace

The existing detector, tracker, local video catalogue and action model power the
v2 workspace. Start with `.venv-app/bin/python scripts/start_local_workspace.py`
and open http://127.0.0.1:8766. No frontend build or external service is needed.

## Monitor and tracking

Select a recording from the searchable sidebar. The saved-analysis selector
switches between full analyses, person-tracking runs and saved live runs. The
selected view and saved run are retained in the page URL.

- **Start live analysis:** source-paced inference on a recording with RT-DETRv2,
  time-aware local association and the existing causal pickup model at 5 samples
  per second. Reload reconnects to an active session. Stop to investigate.
- **Analyze full video:** detection, tracking and pickup scoring over the clip.
- **Track only:** person detection and tracking without action scoring. Pickup
  metrics are unavailable in these runs, not measured zero. Matching completed
  jobs are reused; changes to the tracking profile create a distinct run.
- **Overlays:** people, paths, floor geometry, observed footpoints and a density
  heatmap. Select a person box to focus its ID and highlight its trail. Choose
  a 5, 12, 30 or 60 second trail. Escape clears focus.
- **Playback:** original MP4 controls, 0.5/1/2 speed, 200 ms stepping and fullscreen.
  Dashed boxes are tracker predictions. Their positions do not add observed dwell.

In **Track explorer**, search by ID or area, sort by first observation, observed
time or pickup count, and filter minimum observed time. Inspect a track for its
first/last timestamps, observed time, sampled observation share, observation gaps,
image-space route length and area visit ledger. Visit rows seek the source video.
Download the trajectory CSV or an annotated evidence PNG from an actual source
frame. IDs remain anonymous and local to the run and camera.

## Geometry and profiles

**Camera setup** keeps the existing polygon editor. Draw shelf approach areas,
floor areas, doorway lines, waiting areas, checkout approaches and exits. Save
before analysing. Queue waiting/checkout mappings must be paired to calculate
observed waiting-area-to-checkout transitions. An unpaired waiting polygon can
still describe occupancy; it cannot establish a queue-to-checkout transition.

Map only visible floor regions. Footpoints are person-box bottom centres, not
metric calibration. Reverse doorway lines to change their positive side.
Geometry changes recalculate saved area analytics without rerunning the detector.

Person confidence and lost-track buffer settings apply to the next full-video or
tracking-only run. They persist in this browser. A lower confidence threshold can
add false positives; a longer lost buffer can cause incorrect associations.
The live pickup pipeline retains its verified 200 ms sampling profile because
changing that cadence also changes the action model's temporal input.

## Analytics and investigations

**Analytics** shows observed track presence, area ranking, occupancy over source
time, continuous transitions between areas, directional line crossings and queue
observations. Click the occupancy chart to inspect a moment. Unconfigured
crossings and unavailable temporal metrics are shown as unavailable.

Dwell is credited only between observed positions no more than one second apart.
Track observation share is the fraction of sampled records that are observed;
it is not detector accuracy. Route distances are image pixels, not metres.
Track counts can fragment and are not guaranteed unique customers.

**System & models** controls crowding, queue occupancy and extended-dwell
thresholds. Changing these rules recalculates signals from saved observations.
Pickup scores retain the selected model's threshold.

**Investigations** combines pickup candidates and configured dwell, crowding and
queue signals. Filter by type or review state. Open a signal to inspect evidence,
seek its source video and save an accepted, dismissed or pending decision with
notes. Concurrent edits use revisions to prevent silently overwriting another
review. Reviews appear in the activity feed, exports and newly generated reports.
Accepting a signal does not verify payment or theft. Existing report snapshots
retain the review state they had when generated.

## Shift and monthly reporting

In **Camera setup**, explicitly set a recording's start time and optional label,
shift name and notes. The start-time field uses the device timezone and is saved
as UTC with `operator_assigned` provenance. Import time and file modification time
are never substituted for capture time. The public research clips start undated.

In **Report centre**:

1. Select a collection, shift, day, month or custom interval.
2. Select a store and the recordings to include. Calendar scopes use the IANA
   timezone configured in System & models. Overnight shifts are supported.
3. Add manager notes and preview the coverage, counts and exclusions.
4. Generate PDF, editable Word, area CSV and complete JSON documents.
5. Download any format from the persistent report library.

Collection reports deliberately avoid attributing undated footage to a month or
shift. Calendar reports exclude undated and motion-sampled recordings. They clip
observations and pickup detection timestamps to the selected time interval.
If no analyses match, the report states that coverage is unavailable; it does not
claim the store had zero customers or incidents.

One completed analysis is selected per recording, favouring the greatest source
coverage, pickup-scored runs when coverage ties, then recency. Reports do not
count every repeated analysis. Track totals remain summed camera-local IDs across
recordings and cameras. Daily track presence can include the same ID on more than
one day. For dated clips, camera coverage intervals are unioned to avoid crediting
overlapping analysed time twice. Pickup counts exclude unscored recordings, which
are identified explicitly.

Each report freezes its source IDs, model IDs, saved geometry, metrics, review
state and notes. PDF and Word show a compact briefing; JSON includes the complete
signal log and provenance. The PDF generator bundles fonts for portable output.

## Automatic reports

Create end-of-shift, daily-midnight or month-end schedules in Report centre.
Schedules freeze their timezone, shift times and optional store selection at
creation. Remove/recreate a schedule to change those parameters.

A local background worker checks every 15 seconds while the application runs.
Completed reporting windows are idempotent. If the app was off at the boundary,
the saved due window runs after restart, followed by outstanding windows.
Failures retain the original reporting window and retry after five minutes.
Documents appear in the report library; no email, cloud delivery or Codex
notification is configured. For unattended operation, keep the existing Linux
user service running and ensure the machine remains awake.

## System and model limitations

System health shows measured CPU, memory and optional NVIDIA GPU telemetry,
local model readiness and report-worker status. The operator-review export makes
saved annotations available for evaluation work.

The trained pickup weights were retained. Their old curated development precision
is not a measurement of continuous CCTV accuracy. Real-store pickup precision,
recall and identity-switch rates still need independently labelled evaluation.
The current tracker uses motion/IoU association, not appearance re-identification.
RTSP camera ingestion, cross-camera identity, product recognition, POS integration
and verified theft detection are not implemented. More UI features cannot supply
those missing data and model capabilities.

## Source layout

- `app/insights.py`: observed-track, area, crossing and signal reductions.
- `app/operations.py`: run discovery, review/context persistence, reports and scheduler.
- `app/report_documents.py`: PDF and Word rendering from immutable report snapshots.
- `web/operations.js`: investigation, analytics, reporting, context and system views.
- `web/workspace.js` and `web/dashboard.js`: the retained playback/inference/editor UI.
- `tests/p1/test_operations.py`: analytics, periods, reviews, reports and scheduler checks.

Existing catalogues and inference outputs are preserved. New operations state
uses additive `ops_*` SQLite tables. Generated reports are under
`runs/review_workspace/reports/`. Application and report dependencies are recorded
in the app requirements and platform lock files.
