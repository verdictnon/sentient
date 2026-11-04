# Sentient · Aster Market

Sentient reads footage from a shop's cameras and answers what a manager asks at
closing time: how many people came in, when the line ran too long, which shelves
people stopped at, and which pickups never reached a till. It runs on one computer
and the video stays there.

## Run it

With the installed models and recordings:

```sh
.venv-app/bin/python scripts/start_local_workspace.py
```

Open **http://127.0.0.1:8766**. The control room automatically plays HD recordings
from one store and runs person tracking and pickup analysis. Models, footage and
runtime data are kept outside Git. Dependencies are in `requirements-app.txt`.

## What you get

- **Control room:** automatic same-store HD playlists with live person/pickup
  inference, clickable camera map, saved layout edits and signal-to-evidence
  navigation. Recorded footage is labelled explicitly; no public CCTV feed is
  implied. The quieter interface keeps explanations behind an info toggle.

- **Store:** one day or week on one screen, with a plain summary, a till-roll
  Z-report with PDF, a heat, route or zone map, a week grid and what to do next.
- **Pickup cross-check:** each pickup candidate is followed to the checkout area or
  the door. A pickup that never reached a till goes to review with a short clip.
  It is a reason to look, not proof of theft.
- **Analytics:** footfall and queue by hour, wait times, shelf ranking, the funnel
  from door to till, and the most common routes.
- **Cameras, Tracks, Investigations and Reports:** source-paced analysis, track
  histories, operator reviews, and PDF, Word, CSV and JSON reports on a schedule.
- **Shelves:** separate area and product labels, confirmed stock counts, capacity,
  refill thresholds, a prioritized refill queue and an auditable movement history.
  Rank shelves by visits, tracks or total observed dwell; open their source paths.
- **Overall PDF:** shelf activity, popular routes, investigations and a frozen
  stock snapshot at generation time. Reports remain available in Report centre.
- **Operations board:** persistent assigned work, due times, refill/audit tasks,
  scheduled shelf checks, atomic stock completion and a complete work history.
- **Shift handover:** download active work, shelf counts and completed tasks as
  Markdown or export task CSV. New reports freeze the current workload.
- **Store identity:** editable display names without changing footage or history.
- Light and dark themes, in English and Russian.

## Recognition

The **Recognition** tab plays real store footage with what YOLOv8n-pose and ByteTrack
found in it. The model ran ahead of time through `scripts/build_showcase.py`, so the
tab needs no GPU. `scripts/run_demo.py` adds these clips as recordings, and they take
the place of the drawn store clip. Clips are read from `app/assets/real` and the
git-ignored `data/real-footage`. The generated store numbers have no video.

## Real recordings

The detector, pickup model and footage are not in this repository. Where they are
installed, start the workspace with:

```sh
.venv-app/bin/python scripts/start_local_workspace.py
```

Open **http://127.0.0.1:8766**. Import an MP4 through **Add video**. The Store view
then lists what the recording still needs: mapped areas, an analysis and a start
time. **Start live analysis** performs inference during source-paced playback,
sampling the detector at 5 fps and using past frames for pickup scores. It is not
a connected CCTV stream. **Analyze full video** provides persistent, seekable
tracking and action results. Seven public 720p recordings make up the installed
demo library, including a 59.8-second contiguous sequence.

Use **Camera setup** to draw named floor strips in front of shelves, doorway lines,
waiting and checkout areas. **Demo guide** explains the workflow inside the
application. Shelf visits, observed dwell, occupancy and nearby pickup alerts appear
together and export to CSV/JSON.

Read [the operator guide](docs/DEMO_GUIDE.md) for operation, geometry, timing and
metric definitions. [Data sources](docs/DATA_SOURCES.md) records the footage
search and access constraints.

## Operations workspace v2

The existing inference application now has distinct Monitor, Track explorer,
Analytics, Investigations, Report centre, Camera setup and System views. It adds
clickable track focus, trajectory and evidence exports, per-track visit histories,
queue/crowding/dwell signals, persistent operator reviews and configurable rules.

Reports support selected recordings, shifts, days, months and custom intervals,
with previews and durable PDF, Word, CSV and JSON downloads. Local schedules can
generate shift, daily and month-end reports while the app runs. Calendar scopes
require explicitly assigned recording times; undated footage remains undated.
Repeated analyses are not counted as additional recordings.

Read the [operations guide](docs/OPERATIONS.md) for features, report semantics,
scheduling and model limits. The retained models do not gain cross-camera identity,
RTSP ingestion or payment verification from this UI upgrade.

## Integrated workspace

The responsive dashboard includes searchable recording thumbnails, observed-track
and pickup totals, area dwell and visits, a track-presence chart, ranked areas and
a footpoint-density heatmap. Every value is derived from the selected recording
and analysis run. Navigation, import validation and geometry editing work on
desktop and mobile. No frontend build step or external asset service is required.

Saved live runs are discoverable from the activity feed and restored on reopening
a recording. Refreshing during active live inference reconnects to that session.
Changing saved geometry recalculates area results from saved observations without
rerunning the models. JSON reports include source evidence, pickup candidates,
current area metrics and their geometry; CSV exports the area table.

## Current limits

The action head's earlier 97.3% precision / 49.3% recall was measured on reused
RetailAction development windows. It is **not CCTV accuracy**. The new live
runs expose false pickup candidates on some CARR activity clips. Pickup alerts
are model predictions; shelf presence does not prove a purchase or payment.

The seven recordings cover four camera scenes in separate stores. They are not
synchronized views of one market. An hour of real, synchronized, HD market
footage covering shelves, doors and checkout has **not been obtained**. The
source search must not be represented as satisfying that requirement.

## Directory

- `app/`, `web/`, `configs/`: the canonical running application.
- `run_demo.sh`, `run_demo.bat`, `scripts/run_demo.py`: the one-command demo.
- `scripts/`: acquisition, training, model export, review bridge and demo tools.
- `tests/`: application, media, tracking, geometry and browser checks.
- `runs/review_workspace/`: active catalog, registered media references, saved
  geometry, offline jobs and live results. Preserve this directory.
- `runs/take_demo_v1/`: selected pickup checkpoint, exported model and evaluation.
- `runs/action_worker_v1/p2_models/`: pinned RT-DETR weights and source.
- `runs/demo_live_v2/`: actual seven-video inference receipts and cleanup log.
- `data/`, `models/`, `vendor/`: retained source datasets and training assets.
- `docs/`: current guides; `docs/archive/` contains historical reports and compact
  original partner handoffs. Archived documents describe earlier versions.
- `.venv-app/`: application/inference environment. `.venv/`: training environment.

The application was consolidated out of generated `runs/.../app` copies.
Obsolete builds, failed exports and reproducible caches were pruned. All 97
registered videos remained available after cleanup. The removed 2.84 GB early
activation cache can be rebuilt with `scripts/cache_expanded.py`; pooled features
and current take-head training inputs remain.

## Useful commands

```sh
# Re-run the current application checks.
.venv-app/bin/python -m app.checks
# Run actual causal inference on all seven HD demo recordings (~2 minutes).
.venv-app/bin/python scripts/verify_live_demo.py
# Seed the provided scenes, preserving any already customized v2 geometry.
.venv-app/bin/python scripts/seed_demo_geometry.py
```

Python dependencies are recorded in `requirements-app.txt`, `requirements-p2.txt`
and `requirements-integration-linux.lock.txt`; action worker dependencies also
have separate lock files. Existing local environments are installed. No model
or dataset downloads occur just by opening the application.
