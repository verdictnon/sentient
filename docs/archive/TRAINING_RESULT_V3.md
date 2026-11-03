# Temporal multi-label experiment — V3

Completed locally on 29 October 2026, RTX 4050 Laptop GPU. No paid compute.

## Result

Tuning selected **tcn_dual**, before official-test predictions. The table compares all three predefined heads on the same fresh cohort. V2 used different inputs, task definitions and evaluation data, so its classification scores are not directly comparable.

The temporal dual-stream model improves event macro F1 over the frame head, but put-back precision remains low and touch recall is zero. This is a research improvement, not a deployable detector. On the naturally sampled cohort, put-back precision is only 1.8%.

| Head | Tune frame macro AP | Fresh test frame macro AP | Fresh test event macro F1 |
|---|---:|---:|---:|
| mlp_dual | 0.229 | 0.212 | 0.175 |
| tcn_body | 0.240 | 0.220 | 0.214 |
| tcn_dual | 0.242 | 0.235 | 0.239 |

The selected event macro F1 has a 500-resample paired-clip-cluster 95% percentile interval of [0.217, 0.263]. Its difference from the frame-head baseline has interval [0.041, 0.088]. Both views of each source clip group are resampled together. This does not establish performance on new stores or camera arrangements.

| Action | Train / tune / test positive clip groups | Test event precision | Recall | F1 |
|---|---:|---:|---:|---:|
| take | 1769 / 1156 / 468 | 51.9% | 65.5% | 0.579 |
| put | 293 / 68 / 45 | 10.6% | 19.5% | 0.138 |
| touch | 46 / 18 / 7 | 0.0% | 0.0% | 0.000 |

## What changed

- Retained overlapping action labels with three independent sigmoid outputs: take, put, touch. Background has no active label. Rare annotations discarded by the V2 single-class window filter are now usable.
- Used complete provided clips, up to 32 nonuniform sampled frames, with per-frame targets. No annotation-centered subwindows or ground-truth interaction-point crops.
- Reused the original Kinetics-pretrained X3D-S blocks 0–4 as a frozen encoder. Cached body RGB, hand/forearm-region RGB, supplied pose and clip metadata. No training from random video weights; no V2-trained weights transferred.
- Compared a frame MLP on both RGB streams and pose, a temporal head on body RGB and pose, and a temporal head on both streams and pose. The X3D encoder already models time; the frame-head baseline is not temporally blind. The two temporal heads isolate the incremental hand stream; this experiment does not isolate pose-only benefit.
- Used train-only channel normalization, masked weighted binary cross-entropy, rare-action oversampling capped at 6x, dropout, weight decay and early stopping. No temporal reversal augmentation that could confuse pickup and return.
- Selected checkpoints and the final head by tuning frame macro average precision across all three actions. Calibrated each sigmoid threshold on tuning event F1 only.

## Data and evaluation protocol

The deterministic manifest is `data/retailaction_v3/manifest.json`. Media hashes are verified against the pinned mirror inventory. Source: Standard Cognition RetailAction; mirror `harpreetsahota/RetailAction`, revision `24fd9c4092a7a2a677542d61b41fc2dd644ec544`. Exact video hashes and paired source clip groups do not cross training, tuning or test roles.

Usable data: 2204 training clip groups / 4314 views; 1317 tuning clip groups / 2599 views; 535 test clip groups / 1049 views. Tuning includes original validation plus 52 source-training clip groups deliberately removed from V3 training, including 12 touch clip groups. V2 validation is reused for tuning, so V3 reserves new evaluation data from the official test split.

The test plan sampled 500 official-test clip groups uniformly with seed 31029, then added all remaining official-test put/touch clip groups for rare-class diagnostics. Both views retain one role. After pose/data exclusions, the natural cohort has 496 usable clip groups. Its selected frame macro AP is 0.209 and event macro F1 is 0.209. The main table uses the rare-enriched cohort and must not be interpreted as natural-store prevalence. Per-view events are scored separately; paired views are not independent groups. A source clip group is not a verified unique person or full store visit.

Event matching uses contiguous positive spans and greedy one-to-one IoU ≥0.3 in **sampled frame indices**. The public frames are sparse and nonuniform. These numbers are not capture-time action-localization AP, real-time alert rates, or recognition on continuous camera footage. Supplied poses identify the subject; pose extraction and tracking are not learned or validated here. Provided clips also have dataset-specific segmentation and composition.

## Checks and artifact

Fourteen preprocessing, label, event-matching and split checks passed. The complete export is `runs/temporal_v3/selected_full.pt` (9,409,071 bytes), including encoder, head, normalization, feature layout, classes and thresholds. Reload on 3 real test clips gives maximum probability difference 0.00194; threshold-decision agreement: [1.0, 1.0, 1.0].

Median two-crop 32-frame encoder-plus-head GPU time: 20.9 ms. This excludes decoding, pose estimation, cropping, tracking and most feature preparation. It is not a streaming latency or camera-capacity estimate.

Measurements: `evaluation.json`, `selection_before_test.json`, `uncertainty.json`, `export_checks.json`, per-head histories and `test_predictions.npz` in `runs/temporal_v3/`. The evaluated run is locked against additional fitting.

## Next useful work

Collect a small continuous, annotated sample from the intended store cameras: ordinary browsing, pickup, returns, touching without removal, occlusion, bags/carts and crowded scenes. This enables hard-negative mining, camera-held-out evaluation and false alerts per camera-hour. Add independent rare-action examples before trusting put/touch performance. Confirm synchronized capture and cross-camera tracking separately; no receipt integration is required for this action-recognition task. Video alone does not establish payment status or theft.

RetailAction attribution and the Standard.AI Dataset License apply to the data and trained artifacts; retain LICENSE and NOTICE and review the stated commercial conditions before product use.
