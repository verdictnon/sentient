# First training experiment

Decision date: 29 October 2026. The first frozen-head engineering pilot has now completed; see [TRAINING_RESULT.md](TRAINING_RESULT.md). The plan below records the experiment design.

## Selected task and model

**Task:** recognize `take`, `put`, `touch`, and `background` for an observed shopper. This first experiment classifies short windows. Event timing, interaction location, and cross-camera identity are separate evaluations.

**Starting model:** PyTorchVideo **X3D-S**, initialized from its official Kinetics-400 checkpoint, with a new four-class output head. The official model uses 13 frames and 160×160 spatial inputs and has approximately 3.79 million parameters. These settings are a low-cost baseline; small items may require larger inputs later. [Official builder](https://github.com/facebookresearch/pytorchvideo/blob/main/pytorchvideo/models/hub/x3d.py), [model zoo](https://github.com/facebookresearch/pytorchvideo/blob/main/docs/source/model_zoo.md).

Checkpoint: `https://dl.fbaipublicfiles.com/pytorchvideo/model_zoo/kinetics/X3D_S.pyth`

Pin PyTorchVideo source to revision `2ac6ed361309cb25144636ad82dd8bd6a4970e0c`. Code is Apache-2.0; record the exact checkpoint's provenance and applicable asset terms before commercial distribution. PyTorchVideo supplies models and training examples; we need a small task-specific trainer. [Repository](https://github.com/facebookresearch/pytorchvideo), [training tutorial](https://pytorchvideo.org/docs/tutorial_classification).

Local hardware verified: RTX 4050 Laptop, 6,141 MiB VRAM, approximately 15 GiB system RAM. Python 3.14.8 is present; PyTorch is not installed. Use an isolated Python 3.11/3.12 environment to reduce compatibility risk with older dependencies. Feasibility and batch size still require a memory smoke test.

## Dataset selection

| Dataset | Fit | Decision |
|---|---|---|
| [RetailAction](https://huggingface.co/datasets/standard-cognition/RetailAction) | Paired overhead videos; take/put/touch points and intervals; 21,000 samples | Primary interaction experiment, under its custom terms |
| [PoseLift](https://github.com/TeCSAR-UNCC/PoseLift) | Six-camera retail pose sequences and anomaly labels; no raw RGB | Later, separate pose-based anomaly experiment |
| [RetailS](https://github.com/TeCSAR-UNCC/RetailS) | Nearly 20M normal frames represented as poses, plus anomalous events | Later research; external archive/license scope needs verification |
| [MERL Shopping](https://merl.com/research/highlights/merl-shopping-dataset) | 106 videos; reaching and inspection actions | Optional auxiliary research data; labels differ |
| [MNNIT staged shoplifting](https://data.mendeley.com/datasets/r3yjf35hzr/1) | CC BY 4.0 staged RGB normal/concealment scenes | Later diagnostic experiment; laboratory domain differs |
| Local store recordings | Real deployment views and natural background behavior | Required for adaptation and deployment acceptance |

Do not pool these into one label set. An action such as `take` is not the same supervision as an anomaly label. Shelf-image datasets also cannot directly train temporal pickup recognition; commercial restrictions for SKU-110K/RPC are documented in the broader research report.

RetailAction revision: `49cb590723db921a4bd5a38adea92f6abd3f7a00`. Published split counts: 17,222 train / 1,277 validation / 2,501 test. API-verified TAR sizes: 26.082 GB / 1.938 GB / 3.838 GB respectively, approximately 31.86 GB total, decimal units. Keeping archives and extracted data needs roughly twice that space plus working caches. [File inventory](https://huggingface.co/datasets/standard-cognition/RetailAction/tree/main/data).

Its custom license requires attribution/deidentification and a separate express grant once revenue from products/services using the materials exceeds USD 10,000. This condition must be incorporated into commercialization planning; fine-tuning does not remove it. Authorized local data is the alternative production lineage. [License](https://huggingface.co/datasets/standard-cognition/RetailAction/blob/main/LICENSE).

## Data checks before training

I inspected one actual metadata JSON from a bounded 5 MiB prefix of the validation archive. It has nested `content.action_cam.rank0/rank1`, frame timestamps, poses, action labels with start/end and spatial data, and segment bounds. This checks one schema example, not dataset-wide quality. Evidence: `research/retailaction_sample_schema.json`.

- Audit every sample's labels, timestamps, pose schema, decoding, and corrupt/missing views. Ignore macOS `._*` archive sidecars.
- Respect the non-uniform sampling; nominal MP4 FPS does not determine original elapsed time.
- Keep both views and every derived window of one sample in its original split. Do not train on validation/test samples.
- Use shopper regions derived from available poses/detections with enough hand/shelf context. Ground-truth interaction points must not become an oracle input for deployment evaluation.
- Build event-centered diagnostic windows from annotations, but separately evaluate complete sequences without known event boundaries. Classifier scores on trimmed events do not establish live detection quality.
- Audit background windows for other shoppers' unannotated actions. `Take` dominates the published labels; report rare-class metrics and uncertainty.

## Fine-tuning sequence

1. Load pretrained X3D-S, remove its original classification output, and fit a four-class head on frozen features. Verify the loader by overfitting a tiny training subset.
2. If validation supports it, unfreeze the last backbone block with a smaller learning rate. Start with mixed precision, batch size 1–2, gradient accumulation, and frozen batch-normalization statistics. These are proposed settings, not measured memory results.
3. Compare one-view output with aligned two-view late fusion. Train a pose-only temporal baseline if RGB results are weak or crop quality is insufficient.
4. Add event-localization heads and continuous-window evaluation after classification works. Evaluate spatial localization separately if added.
5. Adapt on local camera recordings and test on held-out days. Report per-class precision/recall, macro-F1, false events per camera-hour, latency, and peak VRAM. Keep a fixed test set and a natural-prevalence background set.

## Available alternatives

| Model | Use / decision |
|---|---|
| X3D-S/M | Efficient pretrained video encoders; S is the local baseline |
| [Torchvision R3D-18](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.video.r3d_18.html) | Straightforward pretrained fallback if PyTorchVideo compatibility is costly |
| [MViTv2-S](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.video.mvit_v2_s.html) | Larger-capacity comparison after baseline; benchmark memory first |
| SlowFast | Established video/action models; higher compute than the selected baseline |
| [VideoMAE v1](https://huggingface.co/MCG-NJU/videomae-base-finetuned-kinetics), [VideoMAEv2-Base](https://huggingface.co/OpenGVLab/VideoMAEv2-Base) | Checked weights declare CC BY-NC 4.0. Keep research-only or obtain suitable rights; MIT v2 code does not change weight terms |
| RF-DETR / RT-DETRv2 | Pretrained people/object detection; retain as separate pipeline components |
| Qwen video VLMs | Optional clip-description comparison; not the first local fine-tuning target |

Commercial products also exist: [Veesion](https://veesion.io/en/) markets gesture alerts; [Everseen](https://everseen.com/) markets retail loss-prevention systems. Their reviewed pages offer deployed products, not a public fine-tuning checkpoint. Vendor performance claims are not comparable to our unmeasured experiment.

Initial execution completed locally with no paid compute: isolated environment, bounded data subset, checkpoint, preprocessing checks, frozen-head baselines, and inference profile. Next priority is adequate minority-class supervision and a larger independent validation set.
