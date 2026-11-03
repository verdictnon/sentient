# V6: hand-detail development experiment

Completed 2 November 2026 on the local RTX 4050 Laptop. No paid compute. Selected architecture: **detail**, using mean selection AP across seeds 43/44. Full export averages the two selected heads; table values are the mean of per-seed metrics, not ensemble metrics.

## Controlled result

| Inputs | Mean selection macro AP | Mean development-check macro AP |
|---|---:|---:|
| context | 0.260 | 0.241 |
| body | 0.285 | 0.239 |
| detail | 0.309 | 0.250 |

| Action | Body selection AP | Detail selection AP | Body check AP | Detail check AP |
|---|---:|---:|---:|---:|
| take | 0.404 | 0.370 | 0.329 | 0.345 |
| put | 0.360 | 0.475 | 0.290 | 0.283 |
| touch | 0.090 | 0.081 | 0.097 | 0.122 |

Same training and development examples, labels, loss and temporal-head family are used for each feature ablation. `context` uses original Kinetics X3D body/pose features; `body` adds per-frame DINOv2 fixed-body embeddings; `detail` additionally adds separate left/right hand-region embeddings and validity masks. This tests feature augmentation, with different input-layer parameter counts, not identical capacity.

Training: 512 source clip groups / 955 views. Selection and development-check: 64 and 64 groups. Each development half has 9 touch-positive groups. The 256-group pilot preceded this bounded 512-group expansion. Existing features were reused; see `parent_cache_reused.json`.

Detail-minus-body mean selection macro AP: +0.024; check: +0.011. The predefined detail-expansion gate is True. Touch may move differently from the aggregate; inspect its row rather than claiming all classes improved.

For the averaged-head predictions, a 300-resample paired-clip bootstrap estimates the check macro-AP difference (detail minus body) interval as [-0.03525122871938345, 0.04515866391973285]. This describes conditional development uncertainty and excludes adaptive model/data selection effects.

## Scope and safeguards

These are rare-enriched **development** results. Selection/check groups were already V3 tuning; neither is a new independent test. No official-test model predictions were computed in this run. Scores are not comparable with V5's 84.6% annotation-centered window accuracy, natural-prevalence alert precision or the RetailAction localization benchmark.

Paired source clip groups stay in one role. Encoders are untouched Kinetics X3D-S and original DINOv2 ViT-S/14, not V2/V5 supervised features. No ground-truth interaction point is used for an input crop. The model retains nonuniform sampled frame order; original capture-time alignment is not fixed in this crop experiment.

The tiny-set learnability check passed before expansion; it is training memorization, not validation accuracy. Four training-category examples were visually inspected. Source labels have not been certified by a human annotation campaign.

DINOv2 code revision: `7764ea0f912e53c92e82eb78a2a1631e92725fc8`. Original ViT-S/14 checkpoint SHA-256: `b938bf1bc15cd2ec0feacfe3a1bb553fe8ea9ca46a7e1d8d00217f29aef60cd9`. The downloaded checkpoint is `models/dinov2_vits14_pretrain.pth`, from `https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_pretrain.pth`. The full export includes these weights; no xFormers installation was needed for the measured run.

## Crop and export checks

Median separate-hand crop side is 258.0 source pixels; median side ratio to the old broad hand crop is 0.887. Training contact-point inclusion by either hand region is 89.1%. These are geometric diagnostics, not measured object visibility/recall.

Complete experimental export: `runs/hand_detail_v6_scale512/selected_full.pt` (98,682,047 bytes), including required encoder weights, both temporal heads, normalizers, provenance and selection-fitted thresholds. `audit_v6.load_full(path)` loads it. Raw-clip reload on 3 cases gives max probability error 0.00068; threshold agreement [1.0, 1.0, 1.0].

Prepared-input GPU forward median: 141.1 ms, excluding decoding, cropping, pose estimation and tracking. This is not camera-capacity or continuous-stream latency.

Existing checks cover hand coordinate conventions, missing-hand flags, fixed crops and group isolation. Model/data terms remain applicable: DINOv2's original Apache-2.0 assets and RetailAction's Standard.AI Dataset License. Retain notices and source provenance.

## Next work

Keep V2/V5 available for their window-classification task. V6 shows small, uncertain development gains and is not a deployment replacement. The full-clip hand crops are only modestly tighter; a shorter-time stabilized crop is a candidate for the next controlled input experiment. Avoid another broad sweep. Prioritize human review of touch/return ambiguity and per-view visibility, plus original timestamp handling, before further scaling. A training-only touch review queue is available as `touch_review_queue.jsonl` in the run directory. The partner should complete P0 replay/persistence and P1 review export from `PARTNER_START_HERE.md` concurrently.
