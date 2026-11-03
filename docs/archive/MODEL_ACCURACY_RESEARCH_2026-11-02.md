# Model accuracy: diagnosis, dataset research and next experiments

Research and local audit: **2 November 2026**. Recommendation: retain V2/V5 as reference classifiers, change the information supplied to the next model, and let the partner build the usable video/analytics/review pipeline independently. More epochs of the existing setup have already failed to help.

## What the measurements actually establish

V5 improved per-view accuracy from 83.7% to 84.6% and four-class macro F1 from 0.477 to 0.484 on the **reused** V2 holdout. Its macro-F1 improvement interval includes zero. Known-pair fused macro F1 fell from 0.536 to 0.525. Touch recall stayed zero. Thus there is a modest point-estimate improvement, not evidence that all objectives improved. See [V5 results](TRAINING_RESULT_V5.md).

V3 performs a different task: per-frame multi-label scoring and contiguous action spans on provided sparse clips. Its event macro F1 improved from 0.175 to 0.239 over its own frame-head baseline, but touch recall remained zero and natural-cohort put precision was 1.8%. Comparing V3's 0.239 event F1 with V5's 84.6% window accuracy would be misleading. See [V3 results](TRAINING_RESULT_V3.md).

The publisher's RetailAction benchmark predicts interaction points and intervals using a multi-view localization model; its reported MViT-b mAP of 41.7 is another metric/task, not a target classifier accuracy. [Official benchmark description](https://huggingface.co/datasets/standard-cognition/RetailAction/blob/main/README.md).

## New local findings

Reproduce with `.venv/bin/python scripts/audit_bottlenecks.py`. Outputs: [machine-readable audit](research/model_bottleneck_audit_2026-11-02.json). It inspects training-role geometry and original training-pilot timing, without fitting or selecting on test predictions.

| Finding | Evidence | Implication |
|---|---|---|
| Minority supervision is tiny | Source training has 15,248 take-positive, 349 put-positive and only 64 touch-positive paired clip groups. V2 yields usable touch windows from 32 training groups and one tuning group. V3 uses 46 touch-positive training groups and 18 tuning groups after calibration reallocation/quality filtering. | Reweighting repeats the same scarce scenes. More take examples do not create new touch evidence. These are clip groups, not verified unique independent shoppers. |
| The hand branch often does not magnify hands | 39.1% of audited V3 training frames have a hand-region crop at least as wide as the body crop. Both are resized to 160×160. | A second stream can add compute without adding much object detail. The broad crop spans both arms and has a large minimum size. |
| Missing the contact point is not the whole problem | Across 91 annotated touch **view-events**, a contact point is inside a body crop in at least one support frame for 98.9%, and a hand crop for 100%. | Do not claim that zero touch recall is explained by hands falling outside the image. Coverage does not prove item visibility or enough pixels. |
| Temporal sampling varies substantially | In 888 original training-pilot camera entries with timestamps, 13-frame spans have median 2.2 seconds; p5/p95 are 1.2/6.8 seconds. | Frame count alone is not elapsed time. Fixed-rate motion assumptions and naive cross-view frame matching are unreliable. |
| V3 features already mix long context | Its frozen prefix has a convolution-only temporal receptive field of 57 input frames for clips capped at 32. Squeeze-excitation additionally pools over clip time. Median annotated support is 6–7 sampled frames. | A new temporal head on these spatially pooled features is not a clean short-time encoder. This is a design concern, not proof of the cause of errors. |

The crop audit uses ground-truth contact points **only for diagnosis**, never as inference crop centers. The 39.1% statistic is per frame; contact coverage is per correlated view-event. A hypothetical 20-source-pixel detail becomes roughly 10–11 pixels in typical audited crops; actual product sizes/visibility were not measured.

## Why gains have stalled

**Confirmed constraints:** very few independent rare-action examples; inadequate minority validation; reused evaluation sets; 160-pixel inputs; frozen/spatially pooled V3 features; and failed V4 trials. Linear refinement, residual heads, mixup, conservative continuation and bias calibration did not beat V2's tuning threshold. V5's neighbor bank mostly reduced background mistakes; it did not recover touch. See [V4](TRAINING_RESULT_V4.md) and [V5](TRAINING_RESULT_V5.md).

**Strong hypotheses to test:** fine hand/item state changes are lost by resizing/pooling; the model recognizes a reaching person rather than item removal/return; a frozen Kinetics encoder transfers poorly to overhead retail contact; moving per-frame crops obscure motion relative to the shelf; and some view labels supervise an action that is not actually visible in that view. These require ablations and human review, not confident causal claims.

**Product gap:** current experiments know the subject pose and, for V2/V5, receive annotation-centered windows. They do not establish person discovery, continuous event recall, identity consistency or false alerts per camera-hour. No dataset or model makes a checkout visit equivalent to proof of payment.

## Fastest useful improvements, in order

| Priority | Work | Bounded first experiment / decision |
|---|---|---|
| 0 | Learnability and label QA | Inspect a small training-only take/put/touch/background pack in full frame and both views. Overfit 16–32 clear training examples with augmentation disabled. If the fit fails, inspect labels, crop/pose association, gradients, class ordering and loading before scaling. Memorization success is only a pipeline check. |
| 1 | Real hand/object detail | Separate left/right wrist–forearm regions, keep a fixed or smoothly stabilized crop over a short sequence, and include nearby shelf context. Compare the same source frames with the current crop; never use annotated contact points to center inference. Try 224-pixel detail crops before a new large backbone. |
| 2 | Before/contact/after information | Pair local appearance with body context and measured elapsed times. Use per-frame image features plus a small temporal head, retaining spatial/hand distinction. Missing original times get a validity mask, not invented uniform capture times. |
| 3 | Visibility and multi-view handling | Review whether each view makes the action observable. Use visibility masks/quality flags; report difficult-view performance and abstention coverage. Align views by actual time when available; otherwise use clip/event-level association and disclose uncertainty. |
| 4 | Hard-negative selection | From training-only or newly captured footage, review high-scoring normal browsing, label inspection, shelf touching, returns, bags and occlusions. Add human-adjudicated negatives and positives. Avoid self-labeling model mistakes as ground truth. |
| 5 | Larger video encoder, only if needed | Test one MViTv2-S frozen-feature baseline on the corrected input. Profile batch one first; tune only final layers if it fits. Do not turn this into an unbounded model sweep. |

For a cheap local appearance branch, test **original DINOv2 ViT-S/14** frozen on separate hand/context crops, then train a small temporal fusion head. Its original code/model card uses Apache-2.0; this does not describe newer Cell-DINO/XRay model licenses. [DINOv2 model card](https://github.com/facebookresearch/dinov2/blob/main/MODEL_CARD.md).

**HandFormer** is a useful implementation/design reference for pose–RGB fusion. Its MIT code exists, but it expects detailed 3D hand poses; our 24-joint body skeleton is not that input. Reuse ideas rather than claiming a drop-in replication. Its Assembly101-derived features retain their dataset lineage. [Official code](https://github.com/s-shamil/HandFormer).

**MViTv2-S** has an official Torchvision checkpoint, about 34.5M parameters, and 16-frame/224-pixel inference settings. It is a feasible *candidate* for local frozen inference, not a guarantee that fine-tuning fits the 6-GB GPU or improves retail performance. Use its own transforms. [Official model documentation](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.video.mvit_v2_s.html).

**HOI-DETR** is a newly relevant pretrained source of hand/object boxes and interaction links. The official repository links a checkpoint and uses an older MMDetection/MMCV stack with a ViT-L configuration. Code is MIT; checkpoint/training-data terms and our VRAM/runtime fit still need checking. It is an optional isolated proposal/label-assistance experiment, not the first dependency for our small-GPU pipeline or a ready-made take/put/touch detector. Do not install its old stack into the model environment. [Repository and checkpoint instructions](https://github.com/AhmadDarKhalil/HOI-DETR), [paper](https://arxiv.org/abs/2606.17384).

There is **no demonstrated instant accuracy switch**. Confidence thresholds, abstention, duplicate suppression and two-view consistency can improve alert precision/volume quickly, while reducing coverage or recall. Measure that tradeoff separately; it is not proof of better underlying classification. Quantization and TensorRT optimize runtime, not missing visual evidence.

## Additional datasets: ranked by what they can actually teach

| Priority / dataset | Useful for | Access and rights checked; recommendation |
|---|---|---|
| 1 — More usable RetailAction supervision | Same-domain take/put/touch and both views | Already local. Source training has only 64 touch-positive groups and we selected rare groups already. Recover ambiguity/visibility and better input handling before downloading more take clips. Custom Standard.AI terms remain applicable. [Source](https://huggingface.co/datasets/standard-cognition/RetailAction). |
| 1 — Authorized target-camera recordings | Continuous normal activity, rare actions, real false alarms | Highest expected deployment value. Begin with a small footage/label tranche, then expand based on measured errors. Preserve camera/session/day splits and natural background prevalence. This is a proposed collection, not data already available. |
| 2 — MERL Shopping | Overhead reach, retract, hand-in-shelf and inspection; continuous action timing | 106 roughly two-minute videos; official research download. Small auxiliary temporal test; its labels are not take/put/touch. Verify download/terms before use. [Publisher](https://www.merl.com/research/highlights/merl-shopping-dataset). |
| 3 — PoseLift | Separate pose-based anomaly branch | Pose sequences and frame labels, not raw RGB contact detail. Repository has Apache-2.0; verify the downloaded package's scope. Do not merge anomaly labels with take/put/touch. [Publisher repository](https://github.com/TeCSAR-UNCC/PoseLift). |
| 3 — RetailS | Normal-behavior drift and staged/authentic anomaly diagnostics | Pose-only release and external download; complete dataset-use rights not verified. Its repository does not contain the implementation described by its README. Not an RGB touch solution. [Publisher repository](https://github.com/TeCSAR-UNCC/RetailS). |
| 4 — Assembly101 / AssemblyHands | Multi-view hand-object motion, action segmentation and hand-pose methodology | Assembly101 now has an official gated Hugging Face distribution; full archive is ~3.89 TB. CC BY-NC 4.0. Use a small auxiliary research subset or released features only if appropriate, not a bulk download or default commercial model lineage. [Official distribution](https://huggingface.co/datasets/cvml-nus/assembly101), [AssemblyHands](https://github.com/facebookresearch/assemblyhands-toolkit). |
| 4 — EPIC-KITCHENS-100 | Diverse hand/object actions and optional contact annotations | Egocentric kitchen domain; CC BY-NC 4.0 and separate commercial licensing path. Auxiliary representation research, not direct overhead-retail ground truth. [Publisher](https://epic-kitchens.github.io/site). |
| 4 — Something-Something | Direction-sensitive manipulation / motion reasoning | Research-license restrictions. Interesting pretraining comparison, not unrestricted new take/put/touch data. [Publisher license](https://www.qualcomm.com/content/dam/qcomm-martech/dm-assets/documents/jester_something_something_exercise_research_license_final_qti_28jul2022.pdf). |
| Separate task — MNNIT staged shoplifting | Concealment versus normal RGB diagnostics | Publisher lists CC BY 4.0 and lab-recorded clips. Do not map its two classes to touch or payment status; check actual archive availability before allocation. [Version 1](https://data.mendeley.com/datasets/r3yjf35hzr/1). |
| Separate task — MVTec D2S | Product instance segmentation | CC BY-NC-SA 4.0, static product images. Does not teach temporal actions. [Publisher](https://www.mvtec.com/research-teaching/datasets/mvtec-d2s). |
| Partner exploration — Standard AI Tracks | Coordinate-trajectory replay / analytics | Header says CC BY 4.0, but access text describes an evaluation agreement and commercial-by-request data. Treat rights/access as unresolved; do not assume a free 60,000-hour download. Coordinate-only data cannot validate a raw-video detector. [Publisher card](https://huggingface.co/datasets/standard-cognition/Tracks). |
| Partner benchmark — MMPTRACK | Calibrated multi-camera person tracking, including a simulated retail setting | Research-only access requires a signed agreement/request. Useful after the replay/association adapter exists; it is not shelf-action training data. [Publisher and access terms](https://iccv2021-mmp.github.io/subpage/dataset.html). |

The Shopformer README describes training commands, but a fresh repository-root check still found only documentation/assets/environment/license, not its advertised training/model directories; no ready-to-run release was verified. Keep it as a reference. [Public repository](https://github.com/TeCSAR-UNCC/Shopformer).

## V6 experiment and evaluation discipline

1. Preserve existing reports/checkpoints. Fix preprocessing and inspect training examples before another long run.
2. Freeze one dataset/split/protocol per ablation. First compare old crop versus separate hand/context inputs; then timing/short-time features; then one alternative encoder. Same task, examples and metrics each time. Keep caches keyed by media, crop, timestamps, weights and manifest hashes.
3. Retain a matched diagnostic benchmark against V2/V5, clearly labeled reused. All official rare test cases were inspected in V3; remaining unused test clips are not a new rare-action test. No new public-data generalization claim can be manufactured by renaming that split.
4. Group-based internal cross-validation can diagnose learnability, but source shopper IDs are withheld. It is not guaranteed shopper-independent. A held source-training fold must not inherit V2's supervised features/weights fitted on that fold; use untouched pretrained encoders or refit within each fold. The same applies to V5's neighbor bank.
5. Reserve a future target-camera/session/day test pack before fitting local data. Report per-class precision/recall and uncertainty, plus event recall/false alerts per camera-hour on continuous footage. Measure abstention coverage and identity errors separately.
6. Suggested first collection tranche, adjustable after inspection: review 50 distinct touch and 50 return examples, with natural browsing/occlusions from the same camera setup and separate sessions. Expand toward hundreds of independent examples if those cases remain the bottleneck. Counts are planning targets, not guarantees of accuracy or statistical adequacy.

**Next allocation:** we own the model/data ablations above. The partner owns the replay, camera/tracking adapters, analytics and review exporter in [PARTNER_START_HERE.md](PARTNER_START_HERE.md). The review exporter and preserved timestamps directly improve what the model team can learn; this split creates shared progress rather than two competing training experiments.
