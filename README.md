# Sentient

A local retail-camera demo: pickup candidates, observed person paths, editable
scene geometry and shelf activity. Runs on the RTX 4050 with cached local models.

## Start

```sh
.venv-app/bin/python scripts/start_local_workspace.py
```

Open **http://127.0.0.1:8766**. Select a demo recording and click **Start live
analysis**. Seven public 720p recordings are installed, including a 59.8-second
contiguous sequence. Live mode performs inference during source-paced playback;
it samples the detector at 5 fps and uses past frames for pickup scores. It is
not a connected live CCTV stream. **Analyze full video** provides persistent,
seekable tracking and action results. Import your own MP4 through **Add video**.

Use **Geometry** to draw named floor strips in front of shelves, doorway lines,
checkout areas or other floor areas. **How to label** explains the workflow
inside the application. Shelf visits, observed dwell, occupancy and nearby
pickup alerts appear together and export to CSV/JSON.

Read [the demo guide](docs/DEMO_GUIDE.md) for operation, geometry, timing and
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
