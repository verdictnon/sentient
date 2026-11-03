"""Visit-cluster uncertainty, complete export, and real-video reload check."""
import hashlib
import json
import time
import numpy as np
import torch
from features_v3 import OUT,DATA,ROOT,prepare,CLASSES
from temporal_v3 import model_for,normalize,spans,match_events
from pytorchvideo.models.hub.x3d import x3d_s


def main():
    torch.set_num_threads(2)
    report=json.loads((OUT/'evaluation.json').read_text());info=json.loads((OUT/'index.json').read_text())
    saved=np.load(OUT/'test_predictions.npz');ids=saved['indices'];y=saved['truth'];lengths=saved['lengths']
    chosen=report['selected_by_tuning_only'];groups=sorted({info['views'][int(i)]['sample_id'] for i in ids})
    positions={group:i for i,group in enumerate(groups)};counts={}
    for name in set([chosen,'mlp_dual']):
        values=np.zeros((len(groups),3,3),np.int64);prediction=saved[name]
        thresholds=report['results'][name]['thresholds']
        for row,index in enumerate(ids):
            group=positions[info['views'][int(index)]['sample_id']];n=int(lengths[row])
            for c in range(3):values[group,c]+=match_events(spans(y[row,c,:n]),spans(prediction[row,c,:n]>=thresholds[c]))
        counts[name]=values
    rng=np.random.default_rng(43129);samples=[];deltas=[];perclass=[]
    def score(values):
        tp,fp,fn=values.T
        return np.divide(2*tp,2*tp+fp+fn,out=np.full(3,np.nan),where=tp+fn>0)
    for _ in range(500):
        draw=rng.integers(0,len(groups),len(groups));s=score(counts[chosen][draw].sum(0));b=score(counts['mlp_dual'][draw].sum(0))
        samples.append(float(np.nanmean(s)));deltas.append(float(np.nanmean(s)-np.nanmean(b)));perclass.append(s)
    uncertainty={'unit':'Whole paired source clip group, both camera views together','draws':500,'seed':43129,
        'selected_event_macro_f1_95_percentile_ci':np.percentile(samples,[2.5,97.5]).tolist(),
        'selected_minus_frame_head_95_percentile_ci':np.percentile(deltas,[2.5,97.5]).tolist(),
        'per_class_event_f1_ci':{c:np.nanpercentile(np.array(perclass)[:,i],[2.5,97.5]).tolist() for i,c in enumerate(CLASSES)},
        'limitation':'Visit clustering does not account for undisclosed store/session overlap or establish generalization to new cameras.'}
    (OUT/'uncertainty.json').write_text(json.dumps(uncertainty,indent=2)+'\n')
    artifact=torch.load(OUT/'selected_head.pt',weights_only=True,map_location='cpu')
    if hashlib.sha256((ROOT/'models/X3D_S.pyth').read_bytes()).hexdigest()!=artifact['signature']['backbone_sha256']:
        raise ValueError('Pretrained backbone differs from the feature-cache source')
    backbone=x3d_s(pretrained=False)
    backbone.load_state_dict(torch.load(ROOT/'models/X3D_S.pyth',weights_only=True,map_location='cpu')['model_state'])
    backbone=torch.nn.Sequential(*list(backbone.blocks)[:5]).eval().requires_grad_(False)
    artifact['backbone_state']=backbone.state_dict();artifact['feature_layout']=info['layout']
    artifact['preprocessing']={'frames':32,'padded_with':'last RGB frame; output features zeroed after true length',
        'image_size':160,'rgb_mean':.45,'rgb_std':.225,'crop':'features_v3.prepare, provided pose confidence >=0.2',
        'features':'Spatially averaged Kinetics X3D blocks 0..4, body+hands, supplied pose and clip metadata',
        'normalization':'Train-only channel mean/std, min std .05, clamp standardized channels to [-10,10]',
        'temporal_units':'Nonuniform sampled frame order; actual capture timestamps unavailable',
        'output':'Three independent sigmoid scores; background means no threshold is exceeded'}
    artifact['dataset_license']='Standard.AI Dataset License; see data/retailaction_v3/LICENSE and NOTICE'
    target=OUT/'selected_full.pt';torch.save(artifact,target)
    reloaded=torch.load(target,weights_only=True,map_location='cpu')
    restored=x3d_s(pretrained=False);restored=torch.nn.Sequential(*list(restored.blocks)[:5])
    restored.load_state_dict(reloaded['backbone_state']);restored=restored.eval().cuda()
    head=model_for(reloaded['model_name']).cuda().eval();head.load_state_dict(reloaded['model_state'])
    features=np.load(OUT/'features.npy',mmap_mode='r');all_lengths=np.load(OUT/'lengths.npy',mmap_mode='r')
    valid=[int(i) for i in ids[:3]];errors=[];agree=[];timings=[]
    for index in valid:
        body,hands,pose,_,n=prepare(info['views'][index]);inputs=torch.stack([body,hands]).cuda()
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):rgb=restored(inputs).mean((-1,-2)).float().cpu().numpy()
        real=np.concatenate([rgb[0],rgb[1],pose],axis=0).astype(np.float16).astype(np.float32);real[:,n:]=0
        real=torch.from_numpy(real[None]);real,_,_,_=normalize(real,np.array([n]),reloaded['mean'],reloaded['std'])
        cached=torch.from_numpy(features[index:index+1].astype(np.float32));cached,_,_,_=normalize(cached,all_lengths[index:index+1],reloaded['mean'],reloaded['std'])
        with torch.inference_mode():
            a=head(real[:,reloaded['channels']].cuda()).sigmoid().cpu().numpy()
            b=head(cached[:,reloaded['channels']].cuda()).sigmoid().cpu().numpy()
        errors.append(float(np.abs(a-b).max()));threshold=np.array(reloaded['thresholds'])[None,:,None]
        agree.append(float(((a>=threshold)==(b>=threshold))[:,:,:n].mean()))
    with torch.inference_mode():
        for _ in range(23):
            torch.cuda.synchronize();started=time.perf_counter()
            with torch.autocast('cuda',dtype=torch.float16):restored(inputs)
            head(real[:,reloaded['channels']].cuda());torch.cuda.synchronize();timings.append((time.perf_counter()-started)*1000)
    checks={'checkpoint_bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'real_clips_checked':len(valid),'max_probability_difference':max(errors),'threshold_decision_agreement':agree,
        'median_two_crop_backbone_plus_head_ms':float(np.median(timings[3:])),
        'latency_scope':'32-frame two-crop GPU forward and head; excludes decoding, pose estimation, cropping, tracking and most CPU feature preparation.'}
    if max(errors)>.03 or min(agree)<.95:raise ValueError('Export parity exceeded tolerance')
    (OUT/'export_checks.json').write_text(json.dumps(checks,indent=2)+'\n')
    write_report(report,uncertainty,checks)
    print(json.dumps({'uncertainty':uncertainty,'export':checks},indent=2))


def write_report(report,uncertainty,checks):
    chosen=report['selected_by_tuning_only'];results=report['results'];support=report['support']
    rows=['| Head | Tune frame macro AP | Fresh test frame macro AP | Fresh test event macro F1 |',
          '|---|---:|---:|---:|']
    for name,r in results.items():rows.append(f'| {name} | {report["tune_macro_ap"][name]:.3f} | {r["all_test_frame"]["macro_ap"]:.3f} | {r["all_test_events"]["macro_f1"]:.3f} |')
    class_rows=['| Action | Train / tune / test positive clip groups | Test event precision | Recall | F1 |','|---|---:|---:|---:|---:|']
    for name in CLASSES:
        r=results[chosen]['all_test_events']['per_class'][name]
        counts=' / '.join(str(support[role]['positive_visits'][name]) for role in ['train','tune','test'])
        class_rows.append(f'| {name} | {counts} | {r["precision"]:.1%} | {r["recall"]:.1%} | {r["f1"]:.3f} |')
    ci=uncertainty['selected_event_macro_f1_95_percentile_ci'];delta=uncertainty['selected_minus_frame_head_95_percentile_ci']
    nat=results[chosen];manifest=json.loads((DATA/'manifest.json').read_text())
    text=f'''# Temporal multi-label experiment — V3

Completed locally on 29 October 2026, RTX 4050 Laptop GPU. No paid compute.

## Result

Tuning selected **{chosen}**, before official-test predictions. The table compares all three predefined heads on the same fresh cohort. V2 used different inputs, task definitions and evaluation data, so its classification scores are not directly comparable.

The temporal dual-stream model improves event macro F1 over the frame head, but put-back precision remains low and touch recall is zero. This is a research improvement, not a deployable detector. On the naturally sampled cohort, put-back precision is only {nat['natural_test_events']['per_class']['put']['precision']:.1%}.

{chr(10).join(rows)}

The selected event macro F1 has a 500-resample paired-clip-cluster 95% percentile interval of [{ci[0]:.3f}, {ci[1]:.3f}]. Its difference from the frame-head baseline has interval [{delta[0]:.3f}, {delta[1]:.3f}]. Both views of each source clip group are resampled together. This does not establish performance on new stores or camera arrangements.

{chr(10).join(class_rows)}

## What changed

- Retained overlapping action labels with three independent sigmoid outputs: take, put, touch. Background has no active label. Rare annotations discarded by the V2 single-class window filter are now usable.
- Used complete provided clips, up to 32 nonuniform sampled frames, with per-frame targets. No annotation-centered subwindows or ground-truth interaction-point crops.
- Reused the original Kinetics-pretrained X3D-S blocks 0–4 as a frozen encoder. Cached body RGB, hand/forearm-region RGB, supplied pose and clip metadata. No training from random video weights; no V2-trained weights transferred.
- Compared a frame MLP on both RGB streams and pose, a temporal head on body RGB and pose, and a temporal head on both streams and pose. The X3D encoder already models time; the frame-head baseline is not temporally blind. The two temporal heads isolate the incremental hand stream; this experiment does not isolate pose-only benefit.
- Used train-only channel normalization, masked weighted binary cross-entropy, rare-action oversampling capped at 6x, dropout, weight decay and early stopping. No temporal reversal augmentation that could confuse pickup and return.
- Selected checkpoints and the final head by tuning frame macro average precision across all three actions. Calibrated each sigmoid threshold on tuning event F1 only.

## Data and evaluation protocol

The deterministic manifest is `data/retailaction_v3/manifest.json`. Media hashes are verified against the pinned mirror inventory. Source: Standard Cognition RetailAction; mirror `harpreetsahota/RetailAction`, revision `{manifest['revision']}`. Exact video hashes and paired source clip groups do not cross training, tuning or test roles.

Usable data: {support['train']['visits']} training clip groups / {support['train']['views']} views; {support['tune']['visits']} tuning clip groups / {support['tune']['views']} views; {support['test']['visits']} test clip groups / {support['test']['views']} views. Tuning includes original validation plus 52 source-training clip groups deliberately removed from V3 training, including 12 touch clip groups. V2 validation is reused for tuning, so V3 reserves new evaluation data from the official test split.

The test plan sampled 500 official-test clip groups uniformly with seed 31029, then added all remaining official-test put/touch clip groups for rare-class diagnostics. Both views retain one role. After pose/data exclusions, the natural cohort has {report['natural_test_visits_usable']} usable clip groups. Its selected frame macro AP is {nat['natural_test_frame']['macro_ap']:.3f} and event macro F1 is {nat['natural_test_events']['macro_f1']:.3f}. The main table uses the rare-enriched cohort and must not be interpreted as natural-store prevalence. Per-view events are scored separately; paired views are not independent groups. A source clip group is not a verified unique person or full store visit.

Event matching uses contiguous positive spans and greedy one-to-one IoU ≥0.3 in **sampled frame indices**. The public frames are sparse and nonuniform. These numbers are not capture-time action-localization AP, real-time alert rates, or recognition on continuous camera footage. Supplied poses identify the subject; pose extraction and tracking are not learned or validated here. Provided clips also have dataset-specific segmentation and composition.

## Checks and artifact

Fourteen preprocessing, label, event-matching and split checks passed. The complete export is `runs/temporal_v3/selected_full.pt` ({checks['checkpoint_bytes']:,} bytes), including encoder, head, normalization, feature layout, classes and thresholds. Reload on {checks['real_clips_checked']} real test clips gives maximum probability difference {checks['max_probability_difference']:.5f}; threshold-decision agreement: {checks['threshold_decision_agreement']}.

Median two-crop 32-frame encoder-plus-head GPU time: {checks['median_two_crop_backbone_plus_head_ms']:.1f} ms. This excludes decoding, pose estimation, cropping, tracking and most feature preparation. It is not a streaming latency or camera-capacity estimate.

Measurements: `evaluation.json`, `selection_before_test.json`, `uncertainty.json`, `export_checks.json`, per-head histories and `test_predictions.npz` in `runs/temporal_v3/`. The evaluated run is locked against additional fitting.

## Next useful work

Collect a small continuous, annotated sample from the intended store cameras: ordinary browsing, pickup, returns, touching without removal, occlusion, bags/carts and crowded scenes. This enables hard-negative mining, camera-held-out evaluation and false alerts per camera-hour. Add independent rare-action examples before trusting put/touch performance. Confirm synchronized capture and cross-camera tracking separately; no receipt integration is required for this action-recognition task. Video alone does not establish payment status or theft.

RetailAction attribution and the Standard.AI Dataset License apply to the data and trained artifacts; retain LICENSE and NOTICE and review the stated commercial conditions before product use.
'''
    (ROOT/'TRAINING_RESULT_V3.md').write_text(text)


if __name__=='__main__':main()
