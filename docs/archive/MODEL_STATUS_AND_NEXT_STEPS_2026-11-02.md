# Sentient: current recognition quality and next steps

Updated 2 November 2026 after two actual model/input experiments. Public datasets only; no authorized target-camera recordings are available yet.

**We have a useful pickup-window baseline, poor return recognition, and no measured end-to-end store-system accuracy. Two new experiments did not establish a reliable improvement. Keep the existing checkpoints and change the supervision/data strategy before scaling compute.**

## What the percentages mean

V5, on its reused development holdout of supplied subject crops and annotation-centered windows:

| Action | Precision: how many predicted examples were correct | Recall: how many labeled examples were found |
|---|---:|---:|
| Take | 84.1% | 92.0% |
| Put / return | 19.2% | 33.3% |
| Touch | 0.0% | 0.0% |
| Background | 88.5% | 75.4% |

Overall window accuracy is **84.6%**, but four-class macro-F1 is **0.484**. Neither is theft-detection accuracy. Put support is only eight holdout groups and touch support one group, so rare-class percentages are unstable. See `TRAINING_RESULT_V5.md`.

V6 addresses a different sparse-frame task. Its mean development-check frame macro-AP is **0.2499**; that means an average-precision score of 0.2499, not “24.99% of people/actions correct.” V6 does produce some touch detections; V5's zero touch recall must not be generalized to every newer experiment. None establishes performance on continuous real store footage.

The “21 tests passed” statement in the earlier review concerned software correctness: split grouping, coordinates, label handling and calibration behavior. It was not an accuracy result. Short GPU feasibility tests likewise established memory fit only.

## Why progress has been small

Confirmed constraints:

- V4/V5 mostly refine a representation already learned by V2. V6 freezes both visual encoders and learns temporal heads. These experiments do not exhaust visual fine-tuning.
- The public source training inventory has 15,248 take-positive groups, 349 put-positive groups and 64 touch-positive groups. V6 doubled total groups from 256 to 512 while keeping touch-positive training groups at 46.
- Selection and development data are small and repeatedly reused. A model can win on selection and reverse on the check set, as happened in V7 below.
- Current experiments rely on supplied subject poses and curated sparse clips. They have not validated continuous person discovery, event proposals, tracking, shelf/item association or checkout linking.

Remaining hypotheses include insufficient visible item state, per-view occlusion/label ambiguity, weak transfer from generic pretrained encoders, spatial pooling and missing elapsed-time information. The new trials show that replacing one frozen backbone or tightening crops alone is insufficient on this setup. They do not prove that all larger models or all better crops fail.

## Actual experiment V7: X3D versus MViTv2-S

Command: `.venv/bin/python scripts/compare_backbones_v7.py`.

Both pretrained encoders receive the same fixed-body crop at 224 pixels and the same 16 sampled frames. Temporal features are mapped back to the original sparse-frame index grid, concatenated with the same pose features and fitted with the same TCN family and training budget. Roles remain 512 training, 64 selection and 64 development-check paired clip groups; two seeds. Input projection sizes differ, so this is not an equal-parameter comparison. It tests frozen encoder transfer, not the best attainable fine-tuned performance of either architecture.

| Representation | Mean selection frame macro-AP | Mean check frame macro-AP |
|---|---:|---:|
| Pose only | 0.2654 | 0.1981 |
| X3D + pose | 0.2774 | 0.2318 |
| MViTv2-S + pose | 0.2794 | 0.2189 |

Selection chose MViTv2-S before check predictions. It scored lower on check AP, although some thresholded event scores moved differently. The 500-resample paired-group interval for the ensemble MViT-minus-X3D check AP difference is **[-0.0554, 0.0585]**. There is no reliable winner.

The original V6 full fusion remains a separate reference: it has additional DINO streams and different visual inputs, so it cannot be used to isolate the backbone effect. No official-test predictions were used. The MViTv2-S weights came from the official Torchvision URL; full SHA-256 is recorded in `runs/backbone_v7/protocol.json`.

Decision: **do not replace the current reference merely because MViTv2-S is larger**. A comparison using a different pretraining task or visual fine-tuning remains open.

## Actual experiment V8: short-window hand detail

Command: `.venv/bin/python scripts/short_hand_v8.py`.

Reused V6 body/context features, labels, examples, grouping and temporal-head family. Replaced full-clip hand crops with pose-based crops stabilized over ±2 sparse frames, plus per-frame hand-validity flags. No true action boundaries/contact points determine inputs. Both heads use seeds 43/44. The changed input passed a training memorization diagnostic; this is not generalization evidence.

| Input | Mean selection macro-AP | Mean check macro-AP |
|---|---:|---:|
| Original V6 hand inputs | 0.3087 | 0.2499 |
| Short-window hand inputs | 0.3056 | 0.2503 |

Median training hand-crop side fell from **258 to 112 source pixels**. The check change is only **+0.00044 AP**. The paired-group ensemble difference interval is **[-0.0416, 0.0571]**. Original V6 won selection and is retained. Better geometric magnification alone did not establish an accuracy gain. Item visibility and source labels have not been certified by human review.

These V7/V8 sets are adaptive development data already used in V6; the intervals do not correct for that history. No new production-quality claim is warranted.

## Zooming out: use the right task and data for each product component

| Product need from `plan.txt` | What must be learned/measured | Current coverage |
|---|---|---|
| Counts, routes, zones and queues | Person detection/tracking, source timing, calibrated geometry and event logic | Contracts/briefs exist; no implemented application in this directory. These need not wait for the action classifier. |
| Item pickup and return | Who interacted, item state before/after, hand/shelf evidence and temporal boundaries | RetailAction is relevant; current model is a limited research baseline. |
| Concealment/security review | Visible concealment actions, hard normal negatives, journey continuity and human review | Current take/put/touch supervision does not directly train this task. |
| Shelf/SKU attribution | Shelf mapping and sufficiently detailed product views | Separate from the action classification results. |

The first action milestone should be **reliable pickup/return events for a known subject, with visible evidence**. Touch remains useful for distinguishing normal shelf contact, but maximizing an unstable touch score should not become the entire product roadmap. A checkout-zone visit remains an observation, not proof that an item was paid for.

## More public data: yes, targeted and audited

The existing database is relevant for one component and incomplete for the product. More source-training take clips can improve diversity, but do not increase rare touch support. More unrelated videos or frames from the same few scenes do not supply the missing supervision.

New and rechecked leads are saved in `research/public_data_expansion_2026-11-02.json`:

| Source | Appropriate use | Availability / limitation checked |
|---|---|---|
| MERL Shopping | Continuous overhead reach/retract/inspection timing and hard browsing cases | Publisher lists 106 roughly two-minute research videos. Classes differ from take/put/touch; transfer not tested here. [Publisher](https://merl.com/research/highlights/merl-shopping-dataset). |
| UR-PRD | Additional pickup/return hand-image and skeleton supervision | Repository claims 2,103 examples and links Baidu files. No explicit dataset license found; download/schema not verified. It is a lead, not acquired usable training data. [Repository](https://github.com/chelsea9067/UR-PRD). |
| GroceryVision TAL/STAL | Cart/item state-change localization as a separate auxiliary task | “Take” means putting into the basket; “return” means taking out of it. Do not merge names with shelf actions. Publisher uses an affiliation-email request and CC BY-NC 4.0. [Publisher](https://grocery-vision.github.io/past_challenge/iccv2025.html). |
| Zenodo event dataset | Separate concealment/normal diagnostic | API confirms open access/CC BY 4.0; two RAR archives total about 943 MB. Bounded HTTP probes returned valid RAR signatures. Publisher says 790 videos but class counts sum to 820: actual counts, subjects, duplicates and visual labels need audit before use. [Record](https://zenodo.org/records/10149996). |
| MNNIT shoplifting | Staged concealment/normal auxiliary research | V1 lists a 725 MB ZIP; V2 is metadata only. CC BY 4.0 is listed. No video transfer/quality audit here. [V1](https://data.mendeley.com/datasets/r3yjf35hzr/1), [V2](https://data.mendeley.com/datasets/r3yjf35hzr/2). |

One useful pretraining direction is manipulation-focused learning instead of only generic Kinetics transfer. Something-Something V2 contains 220,847 everyday-object action clips; official VideoMAE and MMAction2 releases offer models evaluated/fitted on that task. This is a candidate for a bounded representation comparison, not a guarantee of retail transfer; verify each checkpoint's terms and retain dataset lineage. [Dataset publisher](https://www.qualcomm.com/developer/software/something-something-v-2-dataset), [VideoMAE model zoo](https://github.com/MCG-NJU/VideoMAE/blob/main/MODEL_ZOO.md), [MMAction2 models](https://github.com/open-mmlab/mmaction2/blob/main/configs/recognition/mvit/README.md).

## Next allocation

1. **Audit supervision before scaling.** Use the existing training-only 46-group queue for subject identity, per-view visibility, item stays/leaves/returns and annotation boundaries. Preserve labels and record adjudications separately. Unknown visibility must not silently become background. No human-reviewed ground truth is claimed here.
2. **Add distinct relevant supervision.** Inspect a small accessible public-data tranche before bulk integration. Keep source/session/subject groups intact, separate task label namespaces and collect reviewed hard negatives. Expand source-training retail diversity in a measured run; do not absorb tuning/test labels into training under the old evaluation claim.
3. **Adapt the visual encoder.** Run a bounded later-block fine-tuning comparison on item/hand/context evidence after QA, then consider one manipulation-pretrained encoder. The 4050 has demonstrated capacity for local small-batch trials. Training only new heads is not a complete test of representation learning.
4. **Make the evaluation match the product.** Report pickup/return event precision and recall, and eventually false alerts per video-hour on continuous footage. Reserve an independently collected target-camera evaluation pack when that becomes possible. Public-data development cannot establish deployment accuracy yet.

Do not advertise 90–95% as an expected outcome or switch to cloud as a substitute for these decisions. The new experiments are useful negative evidence; they are not a breakthrough.

Artifacts: `runs/backbone_v7/` and `runs/short_hand_v8/` contain locked protocols/checkpoints/results/predictions. Historical experiments were preserved. Additional regression checks verify temporal interpolation and bounded crop context; they measure code behavior, not recognition accuracy.
