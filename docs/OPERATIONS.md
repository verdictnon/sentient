# Sentient operations workspace

The existing detector, tracker, local video catalogue and action model power the
v2 workspace. Start with `.venv-app/bin/python scripts/start_local_workspace.py`
and open http://127.0.0.1:8766. No frontend build or external service is needed.
Without models or footage, `./run_demo.sh` or `run_demo.bat` opens the simulated store.

## Store view

**Store** opens first. It shows one store for one day or seven days: a summary
sentence, the Z-report, a floor map, the week grid and what to do next. Stores with
dated recordings open before undated ones. A store switcher appears with two or more.

`app/rollups.py` cuts each recording into local-time hours, using the same single
run per recording that reports use. A recording that crosses midnight splits at
midnight. Undated recordings have no hours, so the store shows totals and asks for
a start time. Unknown stays unknown: without declared door lines **Came in** is a
dash, without a queue mapping the queue block disappears, and an unscored run shows
no pickup numbers.

A doorway line must declare what crossing it means. In **Camera setup**, set whether
the arrow points into or out of the store. Until then the line counts crossings
only. A person who returns is counted again.

**Wait at the peak** is the highest hourly mean wait among hours with at least five
observed waits. **Time in store** is the mean observed time per track. Tracks
fragment, so it can run shorter than a real visit.

**What to do next** comes from fixed rules, and each finding shows its numbers:

| Finding | Rule |
| --- | --- |
| Pickups need a look | A candidate left without a till, or its track ended first |
| Open a second till | An hour stayed over the queue limit for at least 5 minutes a day |
| People stop but rarely take | A shelf has dwell above 1.25× the median and a pickup rate below 0.7× the median, with at least 3 shelves of 20+ visits |
| Few people reach a shelf | Its reach is under 35% of the busiest shelf, with the same minimums |
| Staff for an hour | The busiest hour of the period |
| Footfall up or down | A change of at least 3% against the previous period of the same length |

A real store that is not ready lists its missing steps here instead.

## Pickup cross-check

`app/journeys.py` follows each pickup candidate along its camera-local track:

| Status | Meaning |
| --- | --- |
| Reached a till | The track entered a checkout area after the pickup began |
| Left without a till | The track left through a door or exit area with no checkout visit |
| Track lost | The track ended more than 3 seconds before the run did, with no checkout visit |
| Still in view | The run ended while the track was visible |
| Checkout not mapped | The camera has no checkout area, so nothing can be concluded |
| Unavailable | The source has no usable physical time |

**Left without a till** and **Track lost** need review. Tracks fragment, and a
checkout area shows presence, not payment. No till or POS data is used. **Value to review**
multiplies pickups left without a till by the average item value in System & models,
and is hidden when that value is zero. It sizes the review work and does not measure
a loss.

The funnel is nested over tracks: observed, spent at least 8 seconds at a shelf, then
of those, reached a till. Door entries sit above it when doors are declared. Pickup
candidates sit outside it because a model miss is not a shopper who took nothing.
Routes skip shelf and floor areas visited for under 6 seconds. A track that leaves the
queue area without reaching a till counts as gave up.

## Simulated store

`app/simulation.py` runs a deterministic crowd of shoppers over a fixed floor plan.
`app/demo_store.py` stores 14 days of rollups and one two-minute clip from the last
evening. Their tracks pass through the same `analyse()` and `day_rollups()` as real
runs. No detector or action model runs, and **Start simulated live run** replays
the clip at source pace.

Simulated data is labelled in its source profile, its time basis and its rollups, on
the hazard tape, in the video stamp and on the Z-report. Report centre documents carry
a banner, a `simulated` field and a `basis` column. Simulated and real recordings
never share a rollup. An explicit report selection that mixes them is refused, and an
open scope such as a schedule covers only the real ones. Rebuilding replaces only the
simulated store.

## Shelf operations and camera simulation

**Camera setup** gives a shelf two labels: **Area name** identifies the physical
bay, and **Product type** records what belongs there. Existing geometry without a
product field remains valid. Labels survive geometry import/export and feed
shelf cards, analytics, area CSV/JSON and PDFs.

**Shelves** selects a recording and uses its best completed run for visits,
camera-local tracks and total observed dwell. Sort by refill priority, visits,
tracks or dwell. **View paths** opens Track explorer filtered to that shelf.
Analytics also shows visits and total dwell alongside time per visit.

Record a counted quantity, shelf capacity and a refill threshold. A stock count
at or below the threshold enters the refill queue; zero is marked empty. **Add
refill units** increases stock and **Remove confirmed units** decreases it. Every
successful update records a revision, before/after quantities, time and notes.
Conflicting edits are rejected rather than silently overwriting a count. Counts
persist across restarts and recordings with the same store, camera and shelf ID.
Changing a shelf ID creates a separate stock identity. Different camera IDs have
separate inventories; overlapping cameras must not be treated as separate stock.

The stock ledger is operator-confirmed. The existing person/pickup models cannot
measure remaining product quantities. Candidates never decrement inventory.
The simulated store seeds explicitly simulated counts, including empty and low
shelves, so the full count/refill workflow can be demonstrated.

**Overall PDF** reports the selected recording; Report centre can include multiple
recordings and calendar scopes. PDFs include shelf product labels, route counts
and stock at generation time, clearly separate from the activity period. JSON
freezes the stock snapshot and its recent audit history. Historical reports never
change after a refill. **Stock CSV** exports counts with their simulated flag.

**Camera simulation** opens three virtual crops of the demo source: shelves,
checkout/queue and exit. A single video clock controls playback, seeking and speed.
All views show observed IDs and optional 12-second trails; event buttons seek every
view together. Shelf counts overlay the same geometry used by Shelf operations.
These are views of one overhead simulation, not independent physical camera feeds
or cross-camera re-identification. No extra observations enter rollups or reports.
Use **Explore source tracks** to investigate the underlying saved run. Real
recordings and simulated data remain separate.

## Monitor and tracking

Select a recording from the searchable strip under the camera. The saved-analysis selector
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
- `app/journeys.py`: footfall, routes, funnel, queue outcomes and the pickup cross-check.
- `app/rollups.py`: hourly store rollups, KPIs and recommendations.
- `app/simulation.py`, `app/demo_store.py`: the simulated store.
- `app/operations.py`: run discovery, review/context persistence, reports and scheduler.
- `app/report_documents.py`: PDF and Word rendering from immutable report snapshots.
- `web/store.js`: the Store view and the store half of Analytics.
- `web/charts.js`, `web/i18n.js`, `web/i18n.ru.js`: charts and translations.
- `web/operations.js`: investigation, analytics, reporting, context and system views.
- `web/workspace.js` and `web/dashboard.js`: the retained playback/inference/editor UI.
- `tests/p1/test_operations.py`: analytics, periods, reviews, reports and scheduler checks.
- `tests/p1/test_journeys.py`, `test_store_overview.py`, `test_interface_language.py`:
  cross-checks, rollups, the simulated store and translation coverage.

Existing catalogues and inference outputs are preserved. New operations state
uses additive `ops_*` SQLite tables. Generated reports are under
`runs/review_workspace/reports/`. Application and report dependencies are recorded
in the app requirements and platform lock files.
