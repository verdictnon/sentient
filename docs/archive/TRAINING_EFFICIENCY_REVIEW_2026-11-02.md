# Sentient: training bottlenecks, public data and compute decision

Reviewed 2 November 2026. Constraint confirmed by the owner: **public datasets only; no authorized target-camera recordings available yet**.

**Decision: keep the RTX 4050 for the next experiments and run a controlled backbone comparison. X3D-S has not been established as the best model.** The implementation is economical for frozen-feature experiments, but most recent experiments cannot adapt the visual encoders. More epochs of their heads cannot recover visual information discarded upstream. A larger GPU becomes useful for a specific larger encoder or a measured throughput problem; moving the unchanged experiment to Colab does not supply missing action examples.

This review adds a direct hardware/input benchmark and code-level findings to the earlier accuracy report. It does not replace the completed experiment results or claim improved accuracy.

## Project scope and what exists

I reviewed `plan.txt`, the root planning/handoff documents, V1–V6 results, training/preparation/evaluation code, tests, contracts, manifests, cached-result metadata and hardware. I inspected the existing training example sheet. The binary checkpoints were inspected for metadata where needed, rather than retraining the experiments or rerunning official-test evaluation. Third-party vendor trees were inspected at the relevant model implementations, not exhaustively reviewed as independent libraries.

| Directory / files | Current function | Consequence for the plan |
|---|---|---|
| `plan.txt`, `RESEARCH_AND_PLAN.md` | Counts, heatmaps/routes, queues, shelf interactions and security review goals | These require several components; a shelf-action model is one component. |
| `scripts/fetch_pilot.py`, `prepare_expanded.py`, `prepare_v3.py` | Pinned data selection, media verification, pose extraction and role assignment | Useful foundations. Preserve source labels and grouping. |
| `scripts/train_pilot.py`, `cache_expanded.py`, `train_expanded.py` | Annotation-centered four-class window classification | Useful classification reference; action boundaries and the observed subject are supplied. |
| `scripts/features_v3.py`, `temporal_v3.py` | Sparse-clip multi-label frame/event research | Closer to action localization, but elapsed time and continuous discovery remain incomplete. |
| `scripts/improve_v2.py`, `improve_v5.py` | Head, calibration, weight averaging and neighbor corrections | These mostly reuse fixed representations. Additional sweeps have diminishing information value. |
| `scripts/features_v6.py`, `train_v6.py`, `audit_v6.py` | Frozen X3D + frozen DINOv2 appearance + temporal heads | Tests extra appearance information; it is not retail fine-tuning of DINOv2 or X3D. |
| `models/`, `vendor/`, `.venv/` | Pretrained weights, source dependencies and a functioning local CUDA environment | The local training setup already works. Rebuilding it elsewhere has a setup cost. |
| `runs/`, `research/` | Checkpoints, feature caches, predictions, audits | Preserve old results; use new output directories for changed protocols. |
| `contracts/v1/`, partner briefs | Proposed analytics/worker boundary and synthetic replay fixture | Good integration preparation. There is no implemented `app/` or `web/` in this directory. |
| `tests/` | Data, split, calibration and crop checks | All **21 existing tests passed** during this review. They do not certify labels or continuous-video accuracy. |

The directory still has no root Git repository. Establish versioned source/configuration before cloud migration; avoid transferring `.venv`, downloaded vendor histories and duplicate media trees as a cloud training bundle. Existing media reuse includes hard links, which some upload/archive tools can expand into extra copies.

Counts, queues and zone analytics can progress through pretrained tracking and deterministic application logic while action research continues. The current checkpoints do not establish payment status, theft or pickpocketing recognition. This follows from their task/input definitions, not from a missing percentage point of classifier accuracy.

## What the plateau actually means

| Experiment | Evidence | Interpretation |
|---|---|---|
| V2 | Per-view accuracy 83.7%, macro-F1 0.477; touch recall zero | Majority accuracy conceals weak rare classes. |
| V4 | Extra candidates did not beat V2's tuning gate | More fitting of the same representation was insufficient. |
| V5 | Accuracy 84.6%, macro-F1 0.484; touch recall zero; paired-fusion macro-F1 declined | Small mixed development gain, not a broad breakthrough. |
| V3 | Different sparse-frame task; selected event macro-F1 0.239, touch recall zero | Do not compare this number with window accuracy. |
| V6, 512 groups | Detail check macro-AP 0.250 vs body 0.239; paired gain interval includes zero | Additional detail is promising but not established as a reliable gain. |

The source inventory has **15,248 take-positive, 349 put-positive and 64 touch-positive training clip groups**. These are groups of paired views, not verified independent people. The publisher says official splits separate shopper identities, but shopper IDs are not provided to establish person independence for our internal resplits. [RetailAction publisher card](https://huggingface.co/datasets/standard-cognition/RetailAction).

One especially revealing comparison comes directly from the two V6 `pilot_result.json` files:

| V6 training set | Total groups | Take-positive | Put-positive | Touch-positive |
|---|---:|---:|---:|---:|
| Pilot | 256 | 111 | 128 | **46** |
| Expanded | 512 | 222 | 248 | **46** |

Class counts can overlap. Doubling this training set did **not add a single touch-positive group**. Reweighting, oversampling and more epochs reuse the same scenes. They can help optimization, but cannot create new independent observations.

V2's holdout has just one usable touch group; its selection criterion deliberately excludes touch because tuning also has one usable group. V6 has nine touch-positive groups per development half. There is too little support to interpret tiny metric changes as a stable rare-action solution. The existing tiny-set memorization check passed, which supports basic learnability of the pipeline but does not establish generalization.

## Direct measurements on this RTX 4050

Reproduce with:

```sh
.venv/bin/python scripts/audit_efficiency.py
```

Output: `research/efficiency_audit_2026-11-02.json`. This benchmark does not save trained weights, change historical results or use test clips.

The machine has an RTX 4050 Laptop with 6,141 MiB reported by `nvidia-smi`; this PyTorch process reports about 5,765 MiB CUDA-visible memory. The probes kept the existing 70% allocator cap.

I ran AMP forward/backward/AdamW through pretrained **X3D blocks 0–4 plus adaptive average pooling and a small three-output head**, with all non-BN backbone parameters trainable. This is approximately 1.98M trainable parameters. It replaces the original X3D classification head, so it is not a benchmark of the unchanged historical full classifier or the combined V6 model.

| Input | Batch | Median optimizer step | Peak allocated | Peak reserved |
|---|---:|---:|---:|---:|
| 13 frames, 160×160 | 1 | 37 ms | 218 MiB | 232 MiB |
| 13 frames, 160×160 | 2 | 47 ms | 395 MiB | 452 MiB |
| 16 frames, 224×224 | 1 | 49 ms | 464 MiB | 544 MiB |
| 32 frames, 224×224 | 1 | 79 ms | 888 MiB | 1,072 MiB |

Seven synthetic steps per configuration; medians exclude the first two. Memory excludes other apps and CUDA context. These are short feasibility measurements with frozen BN, not long-run throughput guarantees, usable batch-size maxima or accuracy results. Nevertheless, they demonstrate substantial room for a small trainable video backbone at 224 pixels on the existing card.

I also tested **MViTv2-S** locally at 16 frames, 224×224, batch one. Frozen forward took approximately **42 ms / 302 MiB peak allocated**, and a full-gradient AMP/AdamW step approximately **155 ms / 1,400 MiB peak allocated** (1,696 MiB peak reserved). This probe used random initialization to avoid a new checkpoint download, replaced the classifier with three outputs, and timed five steps with the first two excluded. It establishes architecture feasibility only. Pretrained representation quality and downstream accuracy have not been tested. A controlled pretrained MViTv2-S comparison does **not** require cloud hardware on current evidence.

Historical artifacts support the same compute decision:

- V2: eight cached-activation fine-tuning epochs took **145.2 seconds**, maximum recorded training allocation **348 MiB**.
- V3 dual TCN: 14 epochs recorded approximately **4.8 seconds** total.
- V6 expanded: the six completed seeded head fits record approximately **7.0 seconds** total, excluding extraction and setup.

Those very short times are possible because most visual computation is cached/frozen. They are not evidence that end-to-end retail representation training was already exhausted.

### Input preparation is a meaningful cost

On eight deterministic, distinct **training** groups, sequential warm-file medians were:

| Stage | Median |
|---|---:|
| Existing context decode + crop + preparation | 131 ms |
| Existing DINO body/hand decode + crop + preparation | 192 ms |
| Three-region DINO transfer + encode + copy back | 104 ms |

Seven views had 32 frames and one had nine. This small sample is not representative camera throughput. Individual stage medians must not be treated as a measured summed end-to-end median. Existing extraction workers can overlap CPU and GPU work.

Still, CPU preparation is substantial, and **the V6 raw-inference helper decodes each clip twice**: `audit_v6.prepared_inputs()` calls `features_v3.prepare()` and `features_v6.prepare()`, each opening/decoding the same MP4. The context path also constructs an old hand stream that the V6 helper discards. The existing 141 ms V6 forward benchmark excludes this work and pose/tracking.

## Concrete pipeline findings and fixes

| Priority | Finding and evidence | Next implementation |
|---|---|---|
| High: accuracy | V6 encoders are frozen; `train_v6.py` fits only a small temporal head | Keep this cheap baseline, then compare controlled later-block visual fine-tuning after crop/label QA. |
| High: accuracy | `features_v6.fixed_boxes()` aggregates the hand trajectory over the **entire clip**; median hand crop remains 258 source pixels | Test a crop stabilized over a short rolling window. Retain wrist/item/shelf context and a validity flag. Never center using ground-truth contact points. |
| High: accuracy | `features_v6.cache()` calls DINO's default forward, which returns the global CLS embedding; patch outputs are discarded | After the crop test, consider a small 2×2 pooling of patch tokens or wrist-local patch pooling. This is a hypothesis, not proof that CLS caused the errors. |
| High: timing | V3/V6 encode frame order, normalized frame position and total segment duration, without original per-frame elapsed times | Add real elapsed time, inter-frame gaps and missing-time masks where source metadata exists. Do not equate sparse-export FPS with capture cadence. |
| High: measurement | V5/V6 reuse development sets; official rare test cases have already been evaluated in V3 | Treat future internal comparisons as development. Do not call another resplit an untouched public test. |
| Medium: throughput | V6 helper decodes twice and prepares unused branches | Decode once, share RGB/pose arrays and construct only required crops. Check prediction parity before substituting it. |
| Medium: throughput | Each V6 view encodes up to 96 DINO images; cross-view batching is limited | Retain bounded prefetch, benchmark pinned transfers and larger frame batches only if GPU is waiting. Skip invalid hand encoding while preserving its zero-feature/mask semantics. |
| Medium: validity | Whole-clip crop statistics, symmetric TCN convolutions and X3D temporal context use future frames | Define bounded delayed scoring or implement causal inputs. An offline result cannot silently be advertised as immediate streaming output. |
| Medium: reproducibility | Cache signatures cover several hashes but not all preprocessing implementation changes; older caches have only a coarse version | New runs should fingerprint crop/time code/config, media/pose inputs, weights and feature layout. Preserve old caches. |
| Medium: cloud readiness | V6 writes a seed checkpoint after its complete fit, rather than resumable epoch optimizer state; paths/sample counts are experiment-specific | Add a run configuration, explicit data/output roots and resumable model/optimizer/scaler/RNG state before costly runs. |
| Low: local memory | `train_v6.train()` normalizes and uploads `cx` for check data but never uses it during fitting | Load check inputs only in evaluation; stream selected channels if scaling beyond laptop RAM. This is waste, not evidence of label leakage. |

The cached-feature strategy, mixed precision, train-only normalization, frozen BN in V2, bounded workers, hard-linked media reuse, grouped views and selected-before-evaluation records are all useful choices. Preserve them. The main gap is the information and supervision supplied to the model, alongside the transition to continuous inputs.

For new raw-video training, start with AMP and microbatch 1–2, accumulate toward an effective batch around eight, freeze BN statistics and benchmark two to four CPU workers. Accumulation does not fix BN small-batch statistics. Test a small learning rate for pretrained blocks and a larger one for new heads. These are starting settings, not established optimums. Keep BCE for overlapping actions; avoid temporal reversal that can swap take/put semantics. Checkpointing and compilation are optional measured optimizations, not prerequisites for these small runs. [PyTorch performance guidance](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide).

## Public datasets: what to do with the current constraint

**Use more useful supervision, rather than a larger count of mostly take clips.** RetailAction is already the closest verified source in this project. Its selected, motion-aware clips contain at most 32 frames; reconstructing timestamps cannot recover frames omitted from the public release. [RetailAction publisher card](https://huggingface.co/datasets/standard-cognition/RetailAction).

1. **Audit the training-only rare material already available.** Review the 46-group touch queue, both views, subject association, visible item state and ambiguous boundaries. Record visible/occluded/unknown separately and retain original labels. Do not silently relabel an uncertain touch as background. Review rare source-training exclusions to identify recoverable cases; expand only from training-role material under an explicit new protocol.
2. **Mine reviewed hard negatives from source-training clips.** Include shelf browsing, hand contact without removal, bag handling, occlusions and aborted reaches. Unannotated frames are candidate negatives, not guaranteed negatives. Use diversity sampling across scenes/crops so a confidence queue does not repeatedly select the same visual situation.
3. **Use auxiliary datasets with separate supervision heads first.** A different dataset's label named “other” is not automatically our touch/background class. Pretrain or use multi-task losses, then evaluate the retail task separately.

| Dataset | What it could add | Decision now |
|---|---|---|
| RetailAction | Existing exact-domain action labels and paired overhead views | Primary. Review rare supervision and improve representation first. |
| **UR-PRD** | Publisher describes 2,103 pickup/return/other instances, skeletons and hand images; occlusion/small-item/failed-action scenarios | Newly relevant lead. Repository links Baidu HDF5 downloads, but lists no clear license in its root. Download accessibility, rights, schema and label quality are unverified; do not count it as ready training data. [Publisher repository](https://github.com/chelsea9067/UR-PRD). |
| **MERL Shopping** | 106 roughly two-minute overhead videos; reach/retract/hand-in-shelf/product-inspection/shelf-inspection timing | Good auxiliary continuous-action research task. Different classes; no direct take/put/touch mapping. Publisher offers research download; actual transfer was not tested here. [Publisher](https://merl.com/research/highlights/merl-shopping-dataset). |
| PoseLift / RetailS | Pose/anomaly research already assessed in the previous report | Separate branch; they do not add RGB evidence of whether an item left a shelf. |
| Assembly101 / EPIC-KITCHENS | Manipulation pretraining, different views and domains | Optional later research; large/domain-shifted and existing noncommercial terms need to remain visible in model lineage. No bulk download justified now. |
| Staged shoplifting / static shelf-product images | Concealment or product detection as separate tasks | Do not combine their labels with touch/put to inflate action training counts. |

Public-data-only work can establish better research models and a working replay demo. It cannot yet establish performance on the eventual store cameras. That deployment claim remains an explicit future evaluation dependency.

## Which model should come next?

**No best backbone has been identified.** X3D-S was selected for its low cost; V2–V6 are not a controlled pretrained-backbone tournament. Frozen Kinetics features may fail to preserve item state even when they recognize reaching. That hypothesis deserves a model comparison alongside input/supervision work, rather than treating data scarcity as the only possible explanation.

There is relevant task-specific evidence: the RetailAction publisher reports MViT-b at 41.7 localization mAP, SlowFast-R101 at 40.2 and MoViNet-A2 at 33.5. Those scores use a different multi-view localization system; MViT-b is not Torchvision MViTv2-S, and 41.7 mAP is not comparable to our accuracy. This supports testing the model family, not declaring a winner for our implementation. [Publisher benchmark](https://huggingface.co/datasets/standard-cognition/RetailAction/blob/main/README.md).

**First comparison:** existing X3D features versus pretrained MViTv2-S on a matched, fixed development protocol. Hold source groups, input views/crops, temporal coverage, labels, tuning budget and scoring fixed; document each encoder's own normalization and temporal feature stride. For frame localization, expose temporal tokens and map their lower-resolution times back to the same scored frame grid; do not substitute one global clip vector and call it equivalent localization. Run the existing V6 system as a separate reference, because its extra DINO streams change more than the backbone. Include a pose-only baseline and a DINO+pose baseline without X3D so each expensive branch has to demonstrate value.

**Lowest-friction larger alternative:** Torchvision MViTv2-S, official Kinetics weights, about 34.5M parameters with 16-frame/224-pixel settings. Try frozen extraction first with a matched task/metric. Its 80.8% Kinetics accuracy is not a retail prediction. Local capacity passed the synthetic test above; useful retail accuracy remains unmeasured. [Official documentation](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.video.mvit_v2_s.html).

**Optional cloud representation comparison:** VideoPrism-B. The official release provides a 114M-parameter video encoder, spatial-temporal token outputs and a frozen-backbone classification Colab example. Its code uses JAX/Flax, with PyTorch support still a TODO; software is Apache-2.0 and other released materials CC BY 4.0. It is a plausible stronger frozen representation, but a separate-stack experiment with unmeasured retail gain and memory use. Use a separate environment and compare one bounded feature cohort. [Official release](https://github.com/google-deepmind/videoprism).

Do not install several large backbones or old hand-detector stacks at once. A model replacement becomes useful evidence only when input, supervision, split and evaluation are held fixed.

## RTX 4050, Colab or a rented GPU?

| Option | Suitable work | Recommendation |
|---|---|---|
| Existing RTX 4050, 6 GB | Current experiments, crop/time ablations, X3D fine-tuning and a bounded MViTv2-S comparison | **Use now.** Both architecture capacity tests passed. |
| Colab free | Exploratory notebook if a useful GPU is allocated | Optional convenience; GPU and runtime availability vary. |
| Colab paid | Notebook-based trials needing more memory or a separate stack | Useful if already available; it is not a guaranteed accuracy or specific-hardware upgrade. |
| Rented 24 GB GPU | A planned larger-encoder/longer-input comparison after local setup works | Reasonable next compute purchase if profiling or memory requires it. |
| A100/H100/multi-GPU | Much larger controlled workloads | No demonstrated need for the current experiments. |

Google documents variable GPU availability, finite/interruptible runtimes and Drive I/O limitations. If using Colab, upload a compact source/config bundle and data archives, extract to the VM's local disk and copy resumable checkpoints/results to persistent storage. Avoid training across thousands of small files directly on mounted Drive. Check the actual allocated GPU; a higher-memory card is not automatically faster for every small workload. [Colab FAQ](https://research.google.com/colaboratory/intl/en-GB/faq.html).

As checked on 2 November 2026, Runpod's public Pods page displayed RTX 3090 24 GB at $0.50/hour, RTX 4090 24 GB at $0.89/hour and A40 48 GB at $0.59/hour. Those displayed rates imply $5.00, $8.90 and $5.90 for ten running hours, respectively, before storage/other charges. These are examples, not a verified available-instance quote, cloud-tier guarantee or measured performance comparison. [Provider pricing](https://www.runpod.io/pricing).

A sensible first paid allocation is one bounded, approximately $10–20 compute experiment **after** its runnable config, local smoke test and evaluation rule exist. This is a suggested spending cap, not a claim that a specific training run takes ten hours. Include setup, extraction, retries and persistence in cost comparisons. Current heads finishing in seconds provide little reason to migrate them.

## Proposed next experiment sequence

Keep the historical checkpoints intact. Use a new run family and preserve source group roles for the first comparisons.

| Step | Controlled question | Decision/output |
|---|---|---|
| 0 | Are the existing touch examples and designated subjects observable? | Separate review/adjudication records for the 46 training groups; quantify usable/ambiguous/occluded support. Human-reviewed labels require an actual reviewer. |
| 1 | Is X3D the right starting representation? | Matched pretrained X3D vs MViTv2-S development comparison locally; shared supervision/scoring with documented temporal-stride handling. No claim of a winner until this is run. |
| 2 | Do short stabilized hand regions help on the same sparse-frame task? | Compare old vs new crops with the same encoder, head family, examples, seeds and macro-AP. Proposals use poses/time, not true action boundaries. |
| 3 | Does measured elapsed-time information help? | Add gap/time channels and validity masks while holding crop choice fixed. Report timestamp coverage and the missing-time cohort. |
| 4 | Does retail adaptation of visual blocks help? | Fine-tune later blocks on those inputs with low learning rate; freeze BN where applicable. Compare with frozen features. |
| 5 | Is either encoder unnecessary? | Pose-only, DINO+pose and full-fusion ablation; report quality alongside complete input/forward costs. |
| 6 | Is stronger pretraining worth paid compute? | One VideoPrism-B frozen-feature comparison, selected in advance. Rent only if local fit/time warrants it. |

For every step, report per-class AP and thresholded precision/recall, paired-group uncertainty, training time, preparation time and peak memory. Choose checkpoints by selection data only. The existing V6 check has already influenced expansion decisions, so future use remains adaptive development evidence. If touch support becomes too thin, use preregistered grouped source-training cross-validation as a diagnostic with untouched pretrained encoders/refitting within each fold. This is not a new shopper-independent test or a way to reuse supervised features across held folds.

Avoid optimizing overall accuracy as the main gate. Require a meaningful minority improvement without a material majority regression, and inspect whether the gain is repeated across seeds/groups. Report uncertain changes as uncertain; the existing V6 bootstrap interval already crosses zero.

For the broader product, implement the replay/count/dwell/review slice from the partner brief independently. End-to-end action acceptance later needs continuous recordings, event recall and false alerts per camera-hour, plus pose/tracking and timing errors. Those measurements cannot be derived from the present annotation-centered accuracy.

## What was changed and verified in this review

- Added `scripts/audit_efficiency.py`, a bounded synthetic-capacity and training-input timing audit.
- Generated `research/efficiency_audit_2026-11-02.json` with the actual measurements above.
- Added this report; original trainers, manifests, checkpoints and benchmark reports were preserved.
- Executed the audit successfully and reran the existing suite: **21 tests passed**.
- No paid service was provisioned, dataset bulk-downloaded or new official-test model prediction generated.
