# Aster Market operations guide

The interface adds Store, Track explorer, Investigations, Report centre and System
views. See docs/OPERATIONS.md for the store view, cross-checks, tracking tools,
reviews, calendar reports and automatic schedules. The inference and original
geometry workflows below remain available.

With the installed models and recordings, start from the project root:

    .venv-app/bin/python scripts/start_local_workspace.py

Open http://127.0.0.1:8766. The application source lives in `app/` and `web/`.
Runtime jobs, the catalog, saved geometry and live results live in
`runs/review_workspace/`. Keep that folder and the videos registered in its catalog.

## Run analysis

1. Choose one of seven 720p CARR recordings. Six are short activity clips; the
   seventh is a 59.8 second contiguous sequence. These are not synchronized
   views of one store. Their names are publisher activity categories, not
   frame-level ground truth for our pickup model.
2. Click **Start live analysis**. The server decodes frames in source order,
   runs RT-DETRv2 R34 and tracking at 5 samples/second, and scores pickup windows
   from 13 previous observed samples. First score is possible after 2.4 seconds
   of observations. Each shown JPEG and overlay refer to the same frame.
3. Timing shows actual processing fps, inference latency and lag against the
   source clock. Slow hardware can fall behind. This is source-paced inference
   on a recording, not a connection to an IP camera or a claim of 30 fps.
4. Stop or let it finish. **Export results** saves pickup and area metrics.
   **Playback** returns to the original MP4. **Analyze full video** creates
   persistent tracking and action runs, with frame-aligned playback overlays.
   Live and offline windows can differ: offline analysis can use later frames;
   live inference never does. Offline mode is useful for seeking and review.
5. Select **All recordings** to access legacy research clips or imported media.
   Live mode is disabled for motion-selected RetailAction exports because their
   playback time does not represent continuous camera time.

Only one live camera runs at a time on the 6 GB GPU. Stop before switching
videos. Model objects are cached for the next run. GPU work shares a lock with
offline analysis; a queued offline job waits until live analysis ends.

## Explore the dashboard

- Find recordings in the strip under the camera, or press Ctrl K to jump to a
  view or recording. **Store**, **Monitor**, **Analytics**, **Camera setup** and
  **Operator guide** navigate the workspace. Keys 1 to 6 switch views. On mobile, open
  the sidebar with the top-left menu button.
- Summary cards show unique observed camera-local tracks, pickup candidates,
  observed dwell divided by area visits, and mapped polygons. A dash means no
  result or an unavailable measure. Tracks are not verified unique customers.
- **Track presence** counts distinct observed track IDs within each source-time
  interval. It is not entrance traffic, an hourly store report or a daily total.
  During live inference, charts use the recent visible-track samples.
- Enable **Heatmap** to see observed footpoint sample density up to the playback
  position. This is an image-space visualization, not a calibrated floor plan
  or sales heatmap. It is hidden during geometry editing.
- **Saved live runs** restores prior observations, candidates and area results.
  Active live analysis reconnects after page refresh. Analysis continues on the
  server until completion or **Stop analysis**.
- Saving or importing new geometry recalculates area statistics from saved live
  observations or full-video tracks. The pickup predictions remain those of the
  selected source run.
- **Results** downloads a JSON report containing original run evidence plus the
  visible area metrics, their saved geometry and pickup candidates. **CSV**
  exports the area table. Exporting requires saved geometry. Direct API exports
  retain the original run and its geometry revision.

## Draw geometry

All coordinates use the original image pixels. Pause at a clear frame and open
**Geometry**. Choose the kind, click **Draw on frame**, click polygon corners,
then **Finish polygon**. Do not repeat the first vertex. Rename the shape,
drag its corner handles to adjust, and **Save geometry**. Invalid crossing
polygons or points outside the image are rejected. Changes affect the next live
run. Existing full-video tracks can be re-counted with new shelf geometry
without another model run. Import/export JSON preserves names, points and IDs.

- **Shelf approach area:** draw the floor strip where a shopper stands to reach
  that shelf. Do not outline shelf fronts, packages, heads, or upper bodies.
  A track belongs to an area when the bottom centre of its observed person box
  lies inside. Avoid overlapping shelf polygons; otherwise both areas count.
- **Floor area:** aisle, browsing or other named floor region.
- **Door / entrance / exit line:** two endpoints across a visible doorway. Its
  arrow points to the positive side. A negative-to-positive crossing adds a
  positive count. Reverse if the direction is wrong, and declare whether the arrow
  points into or out of the store to count entries and exits. Counts are local to
  this camera, not unique customers across cameras.
- **Checkout area:** the floor occupied by customers at the cashier. Presence
  alone does not establish payment or completed service.
- **Exit area:** a visible exit approach area. It reports presence/visits; use
  a line for directional crossing counts.

Leave entrances, exits or checkout areas absent when they cannot be seen.
The seven selected recordings have manually drawn shelf approach areas based on
four actual camera scenes. They have no invented entrance or cashier labels.
These image polygons are not metric calibration or a multi-camera floor plan.

## Shelf results

Areas rank by unique local tracks and then observed dwell. Visits are entries
from outside or after an observation gap. Dwell sums intervals between inside
observations no more than one second apart. Missing time is not credited.
`Now` means occupancy in the last processed source frame, not wall-clock store
occupancy after playback ends. Occluded feet or inaccurate person boxes can
misassign areas. Overlap counts in both polygons. Camera IDs can fragment;
unique tracks are not guaranteed unique people.

`Pickups nearby` counts new predicted pickup episodes whose footpoint lies in
that shelf area when the alert first fires. It does not identify a product,
measure shelf inventory, verify payment, or count sales. Export area CSV from
the results table. Export run JSON for the underlying pickup episodes and
geometry revision. Live observations and causal input frame indices are saved
in `runs/review_workspace/live/<session>/observations.jsonl`, with the summary
in `summary.json`. Stop long runs before exporting.

## Add a camera recording

**Add video** accepts an MP4 upload or an absolute local path. Give each view a
camera ID. Use the same store and recording-session label for genuinely
synchronized views. Source PTS is indexed; capture UTC is not inferred from
file creation dates. Importing several files does not automatically align
clocks or identify one person across cameras. Draw geometry for each view.
Only public datasets are in scope for this workspace; no camera recordings have
been collected by the project.

## Model and data limits

The retained pickup model is `runs/take_demo_v1/model`. Its take threshold is
0.97. The earlier 97.3% precision / 49.3% recall result is on reused curated
RetailAction development windows. It is not independently measured accuracy on
these CCTV recordings. CARR person-detector crops and continuous windows are
transfer inputs. The workspace verifies execution and causality, not real-store
pickup accuracy. No new training was used to make the selected videos look better.

CARR public source: https://doi.org/10.7910/DVN/CY24UB (CC BY-NC 4.0 research
release). Public-source constraints and the hour-long multi-camera search are
recorded in `docs/DATA_SOURCES.md`.

Geometry schema v2 allows missing entrance/dwell mappings and adds shelf and
exit polygons. The old analytics kernel receives an outside-image sentinel
for absent legacy mappings. Those legacy diagnostic zeros do not mean an
observed zero at an unlabelled door; the workspace uses only explicitly drawn areas.
