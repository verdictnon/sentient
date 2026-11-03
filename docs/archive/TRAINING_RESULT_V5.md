# V5: improving the V2 classifier

Completed locally on 29 October 2026. Selected **knn:31:0.15**, calibration **uncalibrated**, using tuning data only. The 13-frame V2 input, supplied subject-pose crop and four-class classification task are preserved. V3's different frame/event task is not used for this comparison.

## Paired development comparison

| Model | Per-view accuracy | Four-class macro F1 | Known-pair fused accuracy | Fused macro F1 |
|---|---:|---:|---:|---:|
| Original V2 | 83.7% | 0.477 | 86.4% | 0.536 |
| Selected V5 | 84.6% | 0.484 | 87.4% | 0.525 |

| Class | V2 precision / recall / F1 | V5 precision / recall / F1 |
|---|---:|---:|
| take | 83.1% / 92.0% / 0.873 | 84.1% / 92.0% / 0.879 |
| put | 17.9% / 33.3% / 0.233 | 19.2% / 33.3% / 0.244 |
| touch | 0.0% / 0.0% / 0.000 | 0.0% / 0.0% / 0.000 |
| background | 88.2% / 73.2% / 0.800 | 88.5% / 75.4% / 0.815 |

These are **reused V2 holdout results**, not a new unseen test. They describe a paired development comparison on 1879 windows from 572 source clip groups. Both views/windows are clustered together. Only eight holdout groups contain put-back and one contains touch, so rare-class generalization is unproven. Provided poses and annotation-centered windows remain required; this is not continuous action discovery or theft detection.

The 500 paired-group bootstrap 95% interval for the selected-minus-V2 four-class macro-F1 difference is [-0.001, 0.017]. It includes zero and does not establish a macro-F1 gain. This interval does not account for choosing experiments after seeing earlier results or for undisclosed store/session dependence. The official test is not consulted in this V5 run.

Known-pair fusion averages logits for already matched windows; it does not measure live cross-camera identity consistency. V5 improves per-view point estimates and fused accuracy, but fused macro F1 falls from 0.536 to 0.525; it does not improve every objective.

## Experiments and selection

| Candidate | Raw tuning F1 (take/put/background) | Grouped OOF calibrated tuning F1 |
|---|---:|---:|
| v2 | 0.663 | 0.641 |
| soup:0.5 | 0.638 | 0.635 |
| soup:0.75 | 0.650 | 0.644 |
| shrink_:linear_refine:0.25 | 0.653 | 0.643 |
| shrink_:linear_refine:0.5 | 0.652 | 0.650 |
| shrink_:residual:0.25 | 0.653 | 0.645 |
| shrink_:residual:0.5 | 0.655 | 0.656 |
| shrink_:residual_mixup:0.25 | 0.655 | 0.648 |
| shrink_:residual_mixup:0.5 | 0.654 | 0.648 |
| knn:5:0.15 | 0.648 | 0.646 |
| knn:5:0.3 | 0.667 | 0.601 |
| knn:15:0.15 | 0.664 | 0.651 |
| knn:15:0.3 | 0.672 | 0.621 |
| knn:31:0.15 | 0.678 | 0.674 |
| knn:31:0.3 | 0.665 | 0.635 |
| prototypes:0.15 | 0.669 | 0.658 |
| prototypes:0.3 | 0.658 | 0.661 |

Tested weight averaging between the original Kinetics tail/frozen classifier and V2, shrunken residual-head ensembles, class-prototype correction, and nearest-neighbor probability correction. Nearest neighbors use only training embeddings and labels. The selected correction blends 85% V2 probabilities with 15% votes from 31 training neighbors in the learned feature space; it does not estimate customer identity. The memory bank is included in the complete export.

Checkpoint and candidate selection use tuning mean F1 over take/put/background; touch has only one usable tuning source group. Put/background logit calibration is evaluated out of fold across two paired-group folds, then fitted on all tuning only for the selected calibrated candidate. Touch bias is fixed at zero to avoid fitting the singleton. A candidate must improve the tuning selection score by at least 0.005 over V2. Selection is saved before evaluation; the evaluated experiment is locked against further fitting.

Selected logit bias in class order take/put/touch/background: `[0.0, 0.0, 0.0, 0.0]`. This is an additive decision correction, not calibrated payment/theft probability.

## Artifact and verification

Complete model: `runs/x3d_s_v5/selected_full.pt` (69,843,963 bytes). It includes the backbone, selected head, logit bias, classes and input specification. `audit_v4.load_full(path, device)` loads it without the separate pretrained checkpoint.

Reloaded on 3 real clips: maximum logit difference 0.0041, identical class predictions. Warm full-model batch-one GPU forward median: 10.03 ms, excluding decode, pose extraction, cropping, tracking and stream handling.

Measurements are in `runs/x3d_s_v5/`: `protocol.json`, `selection_before_evaluation.json`, `evaluation.json`, `uncertainty.json`, `export_checks.json` and `comparison_predictions.npz`. V4 training histories remain in `runs/x3d_s_v4/`. Original V2/V3 artifacts remain available. RetailAction's Standard.AI Dataset License and attribution requirements still apply.
