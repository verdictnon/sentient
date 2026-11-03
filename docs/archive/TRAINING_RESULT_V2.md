# Expanded training results — V2

Completed locally on 29 October 2026 using the RTX 4050 Laptop GPU. No paid compute was used.

## Outcome

Later-layer fine-tuning improves the frozen baseline on the same fresh holdout. Pickup/background classification is substantially stronger. Put-back precision is still low and touch recognition failed on the tiny usable holdout support; this is not a production-ready interaction detector.

| Model / evaluation | Accuracy | Four-class macro-F1 |
|---|---:|---:|
| Frozen baseline, per view | 75.0% | 0.427 |
| Fine-tuned, per view | 83.7% | 0.477 |
| Frozen baseline, paired-view fusion | 78.8% | 0.491 |
| Fine-tuned, paired-view fusion | 86.4% | 0.536 |

The tuning-only rule selected **finetuned**. Its selection score (macro-F1 over take/put/background) improved from 0.599 to 0.663. Touch was excluded from checkpoint selection because tuning contained only one usable touch visit. The best fine-tuned checkpoint came from epoch 7 of 8.

Paired-view fusion averages logits for known corresponding labeled windows. It does not measure live cross-camera identity association. V2 uses converted frame-indexed labels and a larger cohort, so its scores should not be compared directly with V1’s timestamp-window binary experiment.

## Data and separation

- Planned groups: 2,280 training, 702 tuning, 575 fresh holdout. The original 128 pilot validation groups stayed in tuning.
- Usable windows: 7,022 training, 2,291 tuning, 1,879 holdout across 572 usable holdout visits.
- All source training groups containing put/touch were selected. Some yield no usable rare-action window because of conflicting actions in the short window or missing/insufficient poses.
- Exact-video SHA-256 overlap is zero between each pair of training, tuning, and holdout roles. Both views and all windows of a visit retain one role. The official test split remains unused.
- Model/epoch selection used tuning only, and the selection was recorded before holdout predictions. The evaluated run is now locked against further fitting.

| Class | Training windows / visits | Holdout windows / visits | Fine-tuned precision | Fine-tuned recall |
|---|---:|---:|---:|---:|
| take | 3601 / 1715 | 1091 / 510 | 83.1% | 92.0% |
| put | 522 / 238 | 15 / 8 | 17.9% | 33.3% |
| touch | 62 / 32 | 4 / 1 | 0.0% | 0.0% |
| background | 2837 / 1445 | 769 / 385 | 88.2% | 73.2% |

The four usable touch windows come from just one visit. Zero touch recall is a failed diagnostic, and this sample is far too small for a general performance estimate. Put-back has eight usable holdout visits; its precision is also below a usable operational threshold.

## Uncertainty, verification and resources

- Visit-cluster bootstrap (500 resamples) estimates the selected four-class macro-F1 95% interval as [0.436, 0.511]. The paired improvement over the frozen baseline has interval [0.016, 0.079]. Views/windows are clustered together; this does not fix rare-class or deployment-domain coverage.
- Original/mirror annotation audit: 2,128 checked event endpoints, median nearest-frame difference 0, maximum 1 frame. Views without original timestamps cannot pass this alignment audit.
- Eight preprocessing/split-integrity unit checks passed. Complete model export was reloaded and matched predictions on three real holdout clips; maximum logit difference was 0.0034.
- Eight fine-tuning epochs took 145.2 seconds after activation caching. Peak recorded training allocation was 348.0 MiB. These exclude preparation and download time.
- Warm full-model GPU forward: median 9.05 ms, p95 9.96 ms, batch one. Decode/crop/streaming are excluded; this is not a store camera-capacity estimate.

## Artifacts

- `runs/x3d_s_v2/selected_full.pt`: complete selected backbone + classifier weights, class names, input specification, source signature. The separate pretrained checkpoint is not needed when loading this full export.
- `runs/x3d_s_v2/selection_before_holdout.json`: model choice fixed before evaluation.
- `runs/x3d_s_v2/evaluation.json`, `uncertainty.json`, `annotation_audit.json`, and `export_profile.json`: measurements and audits.
- `runs/x3d_s_v2/finetune_history.json`: all eight epochs, including regressions.

## Next accuracy work

Prioritize temporal or multi-label supervision for conflicting rare-action windows, additional independent put/touch visits, and visibility of the actual item/hand interaction. The fixed single-label window filter currently discards some scarce examples. Keep this holdout as a reported result; reserve a new evaluation set for the next model design. Then validate with local camera footage and continuous event-level false-alert metrics.

Current scores use supplied subject poses and annotation-centered windows from the same public retail dataset domain. They do not validate event discovery, SKU recognition, live tracking, payment status, or theft detection. RetailAction attribution and commercial license conditions remain applicable.
