# Sentient: two-person delivery plan

Updated 2 November 2026. This is a proposed work split and sequence, not a claim that the application is implemented.

## Decision

Keep this workstream on **data quality, action models and evaluation**. Give the partner **video ingestion, tracking adapters, analytics, review/data collection and the application**. Freeze the interface in [contracts/v1/README.md](contracts/v1/README.md) so both can work independently. Neither side needs to wait for a reliable touch detector.

The first product slice is **recorded video → people tracks → doorway counts/zone dwell/queue state → replayable evidence**. Security actions remain experimental observations for human review. Checkout-zone visits are observable; payment status is outside scope.

## Ownership

| Owner | Owns | First useful output | Avoid editing concurrently |
|---|---|---|---|
| Us / this model workstream | `scripts/`, `models/`, `runs/`, training manifests, model reports, dataset adapters | Audited training/validation examples; a model-worker adapter with explicit uncertainty | Partner application folders |
| Partner | New `app/`, `web/`, `configs/`, `tests/app/`, `docs/app/`; separate application dependency lock | Offline replay and deterministic analytics, then detection/tracking | Existing experiments, original data/manifests and the model `.venv` |
| Shared, deliberate updates | `contracts/`, root README, repository setup | Agreed versioned messages and one integration fixture | Simultaneous schema edits |
| Human owners | Camera access, placement, recorded examples, label adjudication and deployment goals | A small real footage pack and reviewed examples | Treating machine-generated labels as ground truth |

This directory is currently **not a Git repository**. Before simultaneous code changes, establish a shared remote and separate clones/branches. Do not share a writable checkout or copy entire environments. Keep videos, checkpoints, caches and credentials out of Git; exchange a manifest with hashes and authorized artifact access. Until repository setup is ready, the partner can start from the brief and contracts in an independent directory. Sharing a Codex subscription does not automatically share files, chats or credentials.

## Critical path and task order

| Order | Partner task | Model task in parallel | Integration gate |
|---|---|---|---|
| 0 | Read the partner brief, set up separate application environment, implement contract validation | Preserve V2/V5 as baselines; record the new audit | Both can read the same JSONL fixture |
| 1 | Deterministic track replay + SQLite event persistence | Review minority labels and correct crop/time handling | Rerunning a fixture never duplicates events |
| 2 | MP4 ingestion, zone editor and pretrained person detector/tracker adapter | Tiny-set learnability check; fixed per-hand crops + context | Actual recorded video plays with local tracks and honest timing |
| 3 | Counts, dwell, queue transitions; review and annotation export | Controlled V6 appearance/time ablations | Review export can be consumed by the model loader |
| 4 | Evidence dashboard and a mock action-worker adapter | Compare against V2/V5 on the same task; freeze candidate before evaluation | Replace mock with actual worker without changing analytics |
| 5 | Two-camera timing/calibration and association spike | Visibility-aware/event-level two-view fusion | Unknown links stay unknown; overlap does not double-count |
| 6 | RTSP resilience and measured pilot performance | Adaptation on authorized store recordings | A recorded pilot report with coverage, errors and latency |

These are ordered work packages, not guaranteed dates. Run a small demo at each gate. Implement one thin path before adding a second frontend, distributed services, automatic shelf recognition or an entire loss-prevention product.

## Efficiency rules

- CPU replay, application logic and review tools can run while the GPU trains. If both use one GPU, reserve it for one heavy job at a time.
- Start with MP4 and JSONL. Add RTSP after replay works. Keep inference behind adapters so deployment need not inherit the research environment.
- Use one Python API/service and SQLite for the first local pilot. Add a larger database or broker only when measured ingestion or querying requires it.
- A detector/tracker benchmark is a bounded comparison, not a separate model-training project. Start with original RT-DETRv2 plus detector-independent ByteTrack; consider an existing multi-camera stack when real synchronized cameras exist.
- Classifier accuracy, continuous event detection and identity continuity get separate evaluations. Never combine their percentages into one product accuracy.
- Partner analytics should expose uptime, unknown timing and uncertain identity. Do not fill missing intervals with zero customers.

## First things to hand to your partner

Send [PARTNER_START_HERE.md](PARTNER_START_HERE.md), the complete `contracts/v1/` directory, and `plan.txt`. No multi-gigabyte dataset transfer is needed for their first task. The brief contains a copy-paste Codex prompt, task boundaries and acceptance criteria.

Our next model work is specified in [MODEL_ACCURACY_RESEARCH_2026-11-02.md](MODEL_ACCURACY_RESEARCH_2026-11-02.md). The new reproducible diagnosis is [research/model_bottleneck_audit_2026-11-02.json](research/model_bottleneck_audit_2026-11-02.json).
