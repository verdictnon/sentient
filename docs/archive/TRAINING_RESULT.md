# First training results

Completed locally on 29 October 2026 using the RTX 4050 Laptop GPU. No paid compute was used.

## Result

The training pipeline and checkpoint reload work. The frozen backbone gives a useful initial take-versus-background baseline. The four-class result is weak, with insufficient put/touch support for a reliable conclusion. Neither checkpoint meets deployment acceptance criteria.

| Experiment | Validation windows | Accuracy | Macro-F1 | Majority accuracy | Majority macro-F1 |
|---|---:|---:|---:|---:|---:|
| Four classes | 333 | 59.5% | 0.454 (supported classes) | 61.6% | 0.254 |
| Take / background | 332 | 71.4% | 0.708 | 61.7% | 0.382 |

Both experiments used frozen Kinetics-pretrained X3D-S features and small linear heads, trained for 60 epochs with the best epoch selected on validation. The binary run excludes put/touch examples; it is a separate task. The test split was not downloaded or used.

## Data and limitations

512 training samples and 128 validation samples were downloaded from the pinned original archives, retaining both views. Archive-order selection is a convenience sample, not representative sampling.

| Class | Training windows | Validation windows |
|---|---:|---:|
| take | 740 | 205 |
| put | 9 | 1 |
| touch | 4 | 0 |
| background | 497 | 127 |

Counts above are derived view/window observations, not independent shoppers. There is just one usable put validation window and no touch validation window. Scores for these rare classes must not be interpreted as validated performance.

Inputs use supplied shopper poses and annotation-centered event windows; background windows are generated outside the labeled subject’s events with a guard interval. Missing timestamps and insufficient frames/poses were skipped. Consequently these scores do not measure event discovery, continuous false-alert rates, cross-camera identity, or theft detection. Validation selected the epoch and is not an untouched final quality estimate.

The binary model's take precision is 81.6%, with recall 69.3%. Data coverage is the first issue to address before increasing model capacity.

## Runtime and verification

- Feature extraction: 80.0 seconds of recorded extraction time across resumed chunks; the interrupted first pass and setup are excluded.
- Four-class head fitting: 0.72 seconds; binary head fitting: 2.25 seconds on CPU.
- Warm batch-one GPU forward median: 10.74 ms; p95: 11.86 ms over 20 measurements.
- Profiled PyTorch peak allocated memory: 46.4 MiB; reserved: 52.0 MiB. Excludes CUDA context and non-PyTorch allocations. This is a separate inference profile, not the lost peak across the original extraction chunks.
- Five preprocessing/metrics unit checks passed. Reloading the saved four-class head reproduced all cached validation predictions exactly.
- One crop was visually checked against the source image; the preview is saved with the run.
- Inference timing excludes decoding, cropping, tracking, network streams, and multi-camera association. It is not a store camera-capacity estimate.

## Artifacts

- `runs/x3d_s_pilot/head.pt`: four-class trained head, feature normalization and model provenance.
- `runs/x3d_s_pilot/take_background_head.pt`: two-class diagnostic head.
- `models/X3D_S.pyth`: required pretrained backbone.
- `runs/x3d_s_pilot/metrics.json`, `binary_metrics.json`, `profile.json`: machine-readable measurements.
- `runs/x3d_s_pilot/windows.json`: derived windows and skip reasons.
- `requirements.lock.txt`, dataset manifests, and README: reproducibility details.

Next: obtain adequate put/touch examples and independent validation support, then compare partial-backbone fine-tuning on the same defined task. Retain the local-camera validation requirement. RetailAction license conditions remain applicable.
