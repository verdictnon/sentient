"""Export the improved V2 model and quantify the paired development comparison."""
import json
import hashlib
import numpy as np
import torch
from improve_v2 import OUT,SOURCE,ROOT,HEADS,Head,original
from train_expanded import classifier
from cache_expanded import prepare
from pytorchvideo.models.hub.x3d import x3d_s


def load_full(path,device='cuda'):
    artifact=torch.load(path,weights_only=True,map_location='cpu')
    model=x3d_s(pretrained=False);model.blocks[-1].proj=torch.nn.Identity();model.blocks[-1].activation=torch.nn.Identity()
    model.load_state_dict(artifact['backbone_state'])
    if artifact['head_kind']=='corrected':
        from improve_v5 import CorrectedHead
        head=CorrectedHead(artifact['head_spec'],artifact['bank_size'])
    else:head=Head(artifact['head_kind']) if artifact['head_kind'] in HEADS else classifier()
    head.load_state_dict(artifact['head_state'])
    return model.eval().to(device),head.eval().to(device),torch.tensor(artifact['bias'],device=device)[None,:]


def main(prebuilt=False):
    torch.set_num_threads(2)
    report=json.loads((OUT/'evaluation.json').read_text());info=json.loads((SOURCE/'index.json').read_text())
    if prebuilt:full=torch.load(OUT/'selected_full.pt',weights_only=True,map_location='cpu')
    else:
        full=torch.load(SOURCE/'selected_full.pt',weights_only=True,map_location='cpu');name=report['selected']
        full['head_kind']=name if name in HEADS else 'classifier'
        if name!='v2':
            learned=torch.load(OUT/(name+'.pt'),weights_only=True,map_location='cpu');full['head_state']=learned['head_state']
            if name=='continued_focal':
                model=x3d_s(pretrained=False);model.blocks[-1].proj=torch.nn.Identity();model.blocks[-1].activation=torch.nn.Identity()
                model.load_state_dict(full['backbone_state']);torch.nn.Sequential(*list(model.blocks)[4:]).load_state_dict(learned['tail_state'])
                full['backbone_state']=model.state_dict()
    full['bias']=report['bias'];full['selection']=report['selected'];full['calibration']=report['calibration']
    full['evaluation_status']=report['evaluation_status'];full['version']=5 if prebuilt else 4
    torch.save(full,OUT/'selected_full.pt')
    data=np.load(OUT/'comparison_predictions.npz');ids=data['indices'];truth=data['truth']
    groups=sorted({info['rows'][int(i)]['sample'] for i in ids});lookup={g:i for i,g in enumerate(groups)};matrices={}
    for name in ['baseline','selected']:
        cm=np.zeros((len(groups),4,4),np.int64)
        for i,t,p in zip(ids,truth,data[name].argmax(1)):cm[lookup[info['rows'][int(i)]['sample']],t,p]+=1
        matrices[name]=cm
    rng=np.random.default_rng(404);draws=rng.multinomial(len(groups),np.full(len(groups),1/len(groups)),size=500)
    def scores(values):
        cm=(draws@values.reshape(len(groups),16)).reshape(500,4,4);denom=cm.sum(1)+cm.sum(2)
        f=np.divide(2*np.diagonal(cm,axis1=1,axis2=2),denom,out=np.zeros_like(denom,dtype=float),where=denom>0)
        accuracy=np.trace(cm,axis1=1,axis2=2)/cm.sum((1,2))
        return f.mean(1),f[:,[0,1,3]].mean(1),accuracy
    b,bc,ba=scores(matrices['baseline']);s,sc,sa=scores(matrices['selected'])
    uncertainty={'resampling_unit':'Whole paired source clip group, all views and windows together','draws':500,'seed':404,
        'selected_macro_f1_95pct':np.quantile(s,[.025,.975]).tolist(),
        'selected_minus_v2_macro_f1_95pct':np.quantile(s-b,[.025,.975]).tolist(),
        'selected_minus_v2_take_put_background_f1_95pct':np.quantile(sc-bc,[.025,.975]).tolist(),
        'selected_minus_v2_accuracy_95pct':np.quantile(sa-ba,[.025,.975]).tolist(),
        'limitation':'Reused, previously inspected holdout; interval excludes adaptive experiment-selection and store/session uncertainty. Touch has one source clip group.'}
    (OUT/'uncertainty.json').write_text(json.dumps(uncertainty,indent=2)+'\n')
    model,head,bias=load_full(OUT/'selected_full.pt');errors=[];seen=set();agreements=[]
    for position,index in enumerate(ids):
        row=info['rows'][int(index)]
        if row['sample'] in seen:continue
        seen.add(row['sample']);view=next(v for v in info['views'] if int(index) in v['indices'])
        _,tensor,error=prepare({**view,'indices':[int(index)]},info['rows'])[0]
        if error:raise ValueError(error)
        tensor=tensor[None].cuda()
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):features=model(tensor)
        with torch.inference_mode():value=(head(features.float())+bias).cpu().numpy()
        expected=data['selected'][position:position+1];errors.append(float(np.abs(value-expected).max()))
        agreements.append(bool(value.argmax(1)[0]==expected.argmax(1)[0]))
        if max(errors)>.05 or not all(agreements):raise ValueError('Complete model export disagrees with cached comparison')
        if len(errors)>=3:break
    def forward():
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):features=model(tensor)
        with torch.inference_mode():return head(features.float())+bias
    for _ in range(5):forward()
    timings=[]
    for _ in range(20):
        a=torch.cuda.Event(enable_timing=True);b=torch.cuda.Event(enable_timing=True);a.record();forward();b.record();b.synchronize();timings.append(a.elapsed_time(b))
    checks={'verified_real_clips':len(errors),'max_logit_difference':max(errors),'prediction_agreement':all(agreements),
        'checkpoint_bytes':(OUT/'selected_full.pt').stat().st_size,'sha256':hashlib.sha256((OUT/'selected_full.pt').read_bytes()).hexdigest(),
        'median_gpu_forward_ms':float(np.median(timings)),'scope':'Warm 13-frame batch-one full-backbone, head and bias. Decode, pose extraction, crop and tracking excluded.'}
    (OUT/'export_checks.json').write_text(json.dumps(checks,indent=2)+'\n');write_report(report,uncertainty,checks)
    print(json.dumps({'uncertainty':uncertainty,'export':checks},indent=2))


def write_report(report,uncertainty,checks):
    version=5 if 'selected_v5' in report else 4
    b=report['baseline_v2'];s=report['selected_v5'] if version==5 else report['selected_v4'];bf=report['baseline_fused'];sf=report['selected_fused']
    lines=['| Model | Per-view accuracy | Four-class macro F1 | Known-pair fused accuracy | Fused macro F1 |','|---|---:|---:|---:|---:|',
        f'| Original V2 | {b["accuracy"]:.1%} | {b["macro_f1_supported"]:.3f} | {bf["accuracy"]:.1%} | {bf["macro_f1_supported"]:.3f} |',
        f'| Selected V{version} | {s["accuracy"]:.1%} | {s["macro_f1_supported"]:.3f} | {sf["accuracy"]:.1%} | {sf["macro_f1_supported"]:.3f} |']
    classlines=[f'| Class | V2 precision / recall / F1 | V{version} precision / recall / F1 |','|---|---:|---:|']
    for name in ['take','put','touch','background']:
        old=b['per_class'][name];new=s['per_class'][name]
        classlines.append(f'| {name} | {old["precision"]:.1%} / {old["recall"]:.1%} / {old["f1"]:.3f} | {new["precision"]:.1%} / {new["recall"]:.1%} / {new["f1"]:.3f} |')
    experiments=['| Candidate | Raw tuning F1 (take/put/background) | Grouped OOF calibrated tuning F1 |','|---|---:|---:|']
    for name,r in report['tuning_results'].items():experiments.append(f'| {name} | {r["uncalibrated"]:.3f} | {r["calibrated_oof"]:.3f} |')
    ci=uncertainty['selected_minus_v2_macro_f1_95pct']
    description=('Tested weight averaging between the original Kinetics tail/frozen classifier and V2, shrunken residual-head ensembles, class-prototype correction, and nearest-neighbor probability correction. Nearest neighbors use only training embeddings and labels. The selected correction blends 85% V2 probabilities with 15% votes from 31 training neighbors in the learned feature space; it does not estimate customer identity. The memory bank is included in the complete export.' if version==5 else 'Reused the trained V2 backbone rather than restarting from Kinetics. Cached its learned 2048-dimensional features once. Tested linear-head refinement, a residual MLP, feature mixup and continued low-rate fine-tuning with frozen batch normalization, class weighting, label smoothing and mild focal emphasis. None cleared the minimum tuning gain, so original V2 was retained.')
    fusion_note=' V5 improves per-view point estimates and fused accuracy, but fused macro F1 falls from 0.536 to 0.525; it does not improve every objective.' if version==5 else ''
    text=f'''# V{version}: improving the V2 classifier

Completed locally on 29 October 2026. Selected **{report['selected']}**, calibration **{report['calibration']}**, using tuning data only. The 13-frame V2 input, supplied subject-pose crop and four-class classification task are preserved. V3's different frame/event task is not used for this comparison.

## Paired development comparison

{chr(10).join(lines)}

{chr(10).join(classlines)}

These are **reused V2 holdout results**, not a new unseen test. They describe a paired development comparison on {report['holdout_windows']} windows from {report['holdout_clip_groups']} source clip groups. Both views/windows are clustered together. Only eight holdout groups contain put-back and one contains touch, so rare-class generalization is unproven. Provided poses and annotation-centered windows remain required; this is not continuous action discovery or theft detection.

The 500 paired-group bootstrap 95% interval for the selected-minus-V2 four-class macro-F1 difference is [{ci[0]:.3f}, {ci[1]:.3f}]. It includes zero and does not establish a macro-F1 gain. This interval does not account for choosing experiments after seeing earlier results or for undisclosed store/session dependence. The official test is not consulted in this V{version} run.

Known-pair fusion averages logits for already matched windows; it does not measure live cross-camera identity consistency.{fusion_note}

## Experiments and selection

{chr(10).join(experiments)}

{description}

Checkpoint and candidate selection use tuning mean F1 over take/put/background; touch has only one usable tuning source group. Put/background logit calibration is evaluated out of fold across two paired-group folds, then fitted on all tuning only for the selected calibrated candidate. Touch bias is fixed at zero to avoid fitting the singleton. A candidate must improve the tuning selection score by at least 0.005 over V2. Selection is saved before evaluation; the evaluated experiment is locked against further fitting.

Selected logit bias in class order take/put/touch/background: `{report['bias']}`. This is an additive decision correction, not calibrated payment/theft probability.

## Artifact and verification

Complete model: `runs/x3d_s_v{version}/selected_full.pt` ({checks['checkpoint_bytes']:,} bytes). It includes the backbone, selected head, logit bias, classes and input specification. `audit_v4.load_full(path, device)` loads it without the separate pretrained checkpoint.

Reloaded on {checks['verified_real_clips']} real clips: maximum logit difference {checks['max_logit_difference']:.4f}, identical class predictions. Warm full-model batch-one GPU forward median: {checks['median_gpu_forward_ms']:.2f} ms, excluding decode, pose extraction, cropping, tracking and stream handling.

Measurements are in `runs/x3d_s_v{version}/`: `protocol.json`, `selection_before_evaluation.json`, `evaluation.json`, `uncertainty.json`, `export_checks.json` and `comparison_predictions.npz`. V4 training histories remain in `runs/x3d_s_v4/`. Original V2/V3 artifacts remain available. RetailAction's Standard.AI Dataset License and attribution requirements still apply.
'''
    (ROOT/f'TRAINING_RESULT_V{version}.md').write_text(text)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--version',type=int,choices=[4,5],default=4);args=parser.parse_args()
    if args.version==5:OUT=ROOT/'runs/x3d_s_v5'
    main(prebuilt=args.version==5)
