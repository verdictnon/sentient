# V4: improving the V2 classifier

Completed locally on 29 October 2026. Selected **v2**, calibration **uncalibrated**, using tuning data only. The 13-frame V2 input, supplied subject-pose crop and four-class classification task are preserved. V3's different frame/event task is not used for this comparison.

## Paired development comparison

| Model | Per-view accuracy | Four-class macro F1 | Known-pair fused accuracy | Fused macro F1 |
|---|---:|---:|---:|---:|
| Original V2 | 83.7% | 0.477 | 86.4% | 0.536 |
| Selected V4 | 83.7% | 0.477 | 86.4% | 0.536 |

| Class | V2 precision / recall / F1 | V4 precision / recall / F1 |
|---|---:|---:|
| take | 83.1% / 92.0% / 0.873 | 83.1% / 92.0% / 0.873 |
| put | 17.9% / 33.3% / 0.233 | 17.9% / 33.3% / 0.233 |
| touch | 0.0% / 0.0% / 0.000 | 0.0% / 0.0% / 0.000 |
| background | 88.2% / 73.2% / 0.800 | 88.2% / 73.2% / 0.800 |

These are **reused V2 holdout results**, not a new unseen test. They describe a paired development comparison on 1879 windows from 572 source clip groups. Both views/windows are clustered together. Only eight holdout groups contain put-back and one contains touch, so rare-class generalization is unproven. Provided poses and annotation-centered windows remain required; this is not continuous action discovery or theft detection.

The 500 paired-group bootstrap 95% interval for the selected-minus-V2 four-class macro-F1 difference is [0.000, 0.000]. It includes zero and does not establish a macro-F1 gain. This interval does not account for choosing experiments after seeing earlier results or for undisclosed store/session dependence. The official test is not consulted in this V4 run.

Known-pair fusion averages logits for already matched windows; it does not measure live cross-camera identity consistency.

## Experiments and selection

| Candidate | Raw tuning F1 (take/put/background) | Grouped OOF calibrated tuning F1 |
|---|---:|---:|
| v2 | 0.663 | 0.641 |
| linear_refine | 0.634 | 0.620 |
| residual | 0.637 | 0.627 |
| residual_mixup | 0.632 | 0.623 |

Reused the trained V2 backbone rather than restarting from Kinetics. Cached its learned 2048-dimensional features once. Tested linear-head refinement, a residual MLP, feature mixup and continued low-rate fine-tuning with frozen batch normalization, class weighting, label smoothing and mild focal emphasis. None cleared the minimum tuning gain, so original V2 was retained.

Checkpoint and candidate selection use tuning mean F1 over take/put/background; touch has only one usable tuning source group. Put/background logit calibration is evaluated out of fold across two paired-group folds, then fitted on all tuning only for the selected calibrated candidate. Touch bias is fixed at zero to avoid fitting the singleton. A candidate must improve the tuning selection score by at least 0.005 over V2. Selection is saved before evaluation; the evaluated experiment is locked against further fitting.

Selected logit bias in class order take/put/touch/background: `[0.0, 0.0, 0.0, 0.0]`. This is an additive decision correction, not calibrated payment/theft probability.

## Artifact and verification

Complete model: `runs/x3d_s_v4/selected_full.pt` (12,262,575 bytes). It includes the backbone, selected head, logit bias, classes and input specification. `audit_v4.load_full(path, device)` loads it without the separate pretrained checkpoint.

Reloaded on 3 real clips: maximum logit difference 0.0034, identical class predictions. Warm full-model batch-one GPU forward median: 10.77 ms, excluding decode, pose extraction, cropping, tracking and stream handling.

Measurements are in `runs/x3d_s_v4/`: `protocol.json`, `selection_before_evaluation.json`, `evaluation.json`, `uncertainty.json`, `export_checks.json` and `comparison_predictions.npz`. V4 training histories remain in `runs/x3d_s_v4/`. Original V2/V3 artifacts remain available. RetailAction's Standard.AI Dataset License and attribution requirements still apply.
