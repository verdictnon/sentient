# Sentient progress and product direction — 3 November 2026

## Current stage

Sentient is an integrated recorded-video prototype. It is ready for deliberate validation toward a single-camera pilot. It has not passed acceptance as accurate store analytics or autonomous loss prevention. There is no defensible overall completion/accuracy percentage.

The original plan contains two product tracks: store operations (counts, traffic, zones, routes and queues) and item/security evidence (pickup, return, concealment and checkout context). The operations track can deliver value while action recognition improves.

## What the new partner delivery establishes

Reviewed `Sentient_P2_Validation_Review/Sentinel/`. All 150 files listed in its archive manifest match their hashes. Its 29 application Python files match the prior uploaded P2 source; this is a validation delivery, not a replacement integrated application. All 33 isolated validation tests passed locally. Recomputing `comparison.json` and `integrity-audit.json` from the bundled numeric evidence reproduced both exactly. No new model inference or download was required.

Partner processed a different MERL Shopping subject/session, `27_1_crop.mp4`, over the frozen first 30 seconds: 900 original frames, 150 inference frames per variant. This adds another subject in the same dataset/viewpoint, not independent stores or crowded-camera coverage. References are automated provisional estimates, not human-verified labels. P2 remains PARTIAL.

The fixed optional area/NMS filters produced the same sampled detection/observed-track coverage as baseline, while total local IDs increased from 2 to 3 and the provisionally followed actor changed IDs around 21.6 seconds. Four high-score detection misses were supported by low-score tracker observations; they must not be reported as four wholly lost people. Changes in diagnostic line/dwell output are not verified customer count errors. Keep filters disabled by default. Their benefit on our earlier inspected clip does not generalize from the current evidence.

Partner lacked our filter overlay and implemented a validation wrapper. Receiving review compared its exact output with our production filter code on all 300 bundled inference samples / 2,272 candidate boxes: no differences. This resolves functional filtering compatibility on those inputs, without asserting identical CUDA/runtime behavior across the two environments.

Partner also recorded an intermittent overlay timestamp assertion: one application-suite attempt failed, an unchanged rerun passed all 176 checks. The proposed snapshot coordination fix remains outstanding; it is not evidence that tracking passed. Our integrated build previously passed 189 application checks. These suites cover different snapshots and cannot be added together as unique tests or called model accuracy.

## Model integration and quality

The main app at http://127.0.0.1:8766 was restarted and checked via its APIs. It reports ready, the action worker is available, the previous completed combined run is still present, and the main workspace contains 86 videos and zero review revisions.

Operational flow: MP4 -> actual original RT-DETRv2-S -> local person tracks -> observed-only sliding windows -> actual exported X3D/V5 model -> immutable raw action scores -> source-aligned review -> versioned label export. A previous fresh browser-initiated 131-second clip completed in 87.38 seconds with 91 action windows and no cached tracking. Model outputs remain unknown/abstained, with zero confirmed physical actions. This result demonstrates execution/throughput, not recognition accuracy.

The deployed action artifact is V5, not V6/V7/V8. Later comparisons did not establish a reliable gain. Export parity and integration are verified; transfer from supplied pose crops/sparse source clips to detector crops/continuous footage is still unvalidated. V5's reused curated development results were take precision/recall 84.1%/92.0%, put 19.2%/33.3%, touch 0%/0%, macro-F1 0.484. Put/touch support was tiny. Its 84.6% window accuracy must not be advertised as continuous store or theft accuracy.

The app is a working inspection and labeling console: run video, inspect moving boxes, configure geometry, check counts/dwell and candidate action evidence, record decisions and export supervision. It is not yet the full store operations product described in plan.txt. The source-only partner snapshot does not include our combined Analyze video coordinator; continue using the receiving integrated build.

## Capabilities against the product plan

| Capability | Current state | Required improvement |
|---|---|---|
| Recorded-video ingestion, person boxes and review/export | Working integrated flow | Resolve reported snapshot race and validate difficult footage |
| Directional crossings and one active dwell zone | Deterministic logic and real tracking inputs | Human-verified doorway tests, stable identities and correctly configured geometry |
| Store occupancy, multiple active zones, routes and heatmaps | Track evidence exists; complete reporting layer absent | Observation-aware aggregation, local identity scope and coverage metadata |
| Peak hours/day/week/month | Not established on present clips | Trusted capture start/timezone and gap-aware calendar aggregation; media-relative PTS alone is insufficient |
| Queue length/crowding and waiting | Queue lifecycle engine exists; P2 disables inferred service | Start with queue-area occupancy/observed dwell; add independently supported service boundaries before service/wait claims |
| Shelf pickup/return | Actual review-only model scores | Reviewed item/hand/shelf evidence, continuous event localization, overlap merging and calibration |
| Shelf/SKU association | Not implemented | Manual shelf regions first; product recognition later if image detail supports it |
| Checkout/payment/security conclusions | Not established | Explicit evidence linkage; checkout proximity does not establish payment |
| Multiple cameras or continuous live operation | Not validated | Separate continuity/time/identity/latency work after the recorded single-camera slice |

## Next delivery order

1. Close the shared validation gate on the actual integrated source. Carry partner numeric evidence into the receiving build, resolve the reported snapshot race, and obtain human-reviewed references on a small diverse continuous pack (doorway, multi-person/occlusion and shelf scenes). Keep settings frozen on the evaluation clips. Report actual event-count errors, missed/duplicate tracks and identity fragmentation. TrackEval provides HOTA/IDF1 when suitable dense references exist; never manufacture those metrics from sparse provisional boxes.
2. Deliver a coherent store-operations report from recorded video: configured doorway crossings, observed zone occupancy, dwell distributions, common zone transitions and heatmaps, plus long-dwell/queue-crowding event clips. Multiple simultaneous zones are a real feature gap: geometry can store multiple shapes but the current P0 mapping selects one active line and one active dwell zone. Calendar summaries need verified timing. Footfall and dwell describe traffic/interest, not sales.
3. Improve action recognition through reviewed supervision. Audit the existing 46-group / 85-view / 407-window training review queue, preserve uncertainty and create an independent continuous evaluation set. Main operational workspace currently has no human revisions. Then compare one bounded visual fine-tuning candidate against V5. Report pickup/return event precision/recall, duplicate event rate and false candidates per video-hour. Existing GPU evidence supports local bounded work; this review provides no reason to buy cloud compute.
4. Add shelf-aware item event histories: manual shelf mapping, person/window association, before/after item-state evidence, pickup/return event merging and human-confirmed incident clips. Treat checkout linkage and multi-camera handoff as distinct later capabilities; payment needs its own evidence source.

Proposed next milestone: a validated single-camera recorded-video operations report with source-linked counts, multi-zone occupancy/dwell and traffic/queue-crowding evidence, plus a separately measured experimental action-review lane. Agree acceptance criteria before evaluating. A larger feature list or another epoch sweep does not by itself satisfy this milestone.

Suggested split: partner owns human-reference tracking validation and snapshot coordination; this workstream owns observed multi-zone reporting and the action-label audit/evaluation protocol. Both use the same integrated source and hashed fixtures. Existing source copies must not silently overwrite the receiving integration.

## External cross-check

NVIDIA's retail reference connects person detection/tracking and basket classification to stored inference data and KPI queries; Intel's loss-prevention reference separates tracking/zone/session rules from conditional behavioral analysis. These support the modular direction, not a claim that adopting either repository will solve our model accuracy. Intel explicitly describes its applications as reference/demonstration software. No replacement stack was installed.

- https://github.com/NVIDIA-AI-IOT/deepstream-retail-analytics
- https://github.com/intel-retail/storewide-loss-prevention
- https://github.com/JonathonLuiten/TrackEval

Receiving audit artifacts: `runs/partner_review_2026-11-03/review-summary.json`, `comparison-recomputed.json`, `integrity-recomputed.json`, `filter-compatibility.json`. Uploaded source and reports were left unchanged.
