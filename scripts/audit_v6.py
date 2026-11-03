"""V6 development uncertainty and a complete, raw-clip-verified export."""
import json
import hashlib
import numpy as np
import torch
from PIL import Image
from features_v6 import ROOT,OUT,SOURCE,WEIGHTS,plan,encoder,prepare,raw,fixed_boxes
from features_v3 import prepare as context_prepare,clean_pose,crop_box
from train_v6 import head_for,dataset,predict
from temporal_v3 import normalize,frame_metrics,calibrate,event_metrics
from pytorchvideo.models.hub.x3d import x3d_s


def context_encoder(pretrained=True):
    model=x3d_s(pretrained=False)
    if pretrained:model.load_state_dict(torch.load(ROOT/'models/X3D_S.pyth',weights_only=True,map_location='cpu')['model_state'])
    return torch.nn.Sequential(*list(model.blocks)[:5]).eval().requires_grad_(False)


def load_full(path):
    artifact=torch.load(path,weights_only=True,map_location='cpu');context=context_encoder(pretrained=False)
    context.load_state_dict(artifact['context_encoder_state']);context=context.cuda().eval()
    image=None
    if artifact['head_name']!='context':
        from dinov2.hub.backbones import dinov2_vits14
        image=dinov2_vits14(pretrained=False);image.load_state_dict(artifact['image_encoder_state']);image=image.cuda().eval().requires_grad_(False)
    heads=[]
    for state in artifact['head_states']:
        head=head_for(artifact['head_name']).cuda().eval();head.load_state_dict(state);heads.append(head)
    return artifact,context,image,heads


def prepared_inputs(sample):
    body,_,pose,_,n=context_prepare(sample);regions,valid,length,boxes=prepare(sample)
    if n!=length:raise ValueError('Context/detail frame counts differ')
    return body[None].cuda(),torch.from_numpy(pose.astype(np.float16)).float().cuda(),torch.from_numpy(regions.reshape(-1,224,224,3).transpose(0,3,1,2)).cuda(),valid,n


def forward(inputs,artifact,context,image,heads):
    body,pose,images,valid,n=inputs
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):rgb=context(body).mean((-1,-2))[0].float()
    base=torch.cat([rgb,pose],dim=0).half().float();base[:,n:]=0
    name=artifact['head_name']
    if name!='context':
        if name=='body':images=images[:n]
        mean=torch.tensor([.485,.456,.406],device='cuda')[None,:,None,None];std=torch.tensor([.229,.224,.225],device='cuda')[None,:,None,None]
        parts=[]
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):
            for batch in images.split(32):parts.append(image((batch.float()/255-mean)/std).half().float())
        encoded=torch.cat(parts);detail=torch.zeros((1154,32),device='cuda')
        if name=='body':detail[:384,:n]=encoded.T
        else:
            detail[:1152,:n]=encoded.reshape(3,n,384).permute(0,2,1).reshape(1152,n)
            detail[384:768]*=valid[0];detail[768:1152]*=valid[1];detail[1152:,:n]=torch.tensor(valid,device='cuda')[:,None]
        base=torch.cat([base,detail],dim=0)
    wanted=artifact['channels'];x=base[wanted][None]
    mean=artifact['mean_selected'].to(x.device)[None,:,None];std=artifact['std_selected'].to(x.device)[None,:,None]
    x=((x-mean)/std).clamp(-10,10);x[:,:,n:]=0
    with torch.inference_mode():return torch.stack([h(x).sigmoid() for h in heads]).mean(0)


def geometry(info):
    from features_v6 import DATA
    ratios=[];point_covered=[];sides=[]
    for row in info['views']:
        if row['pilot_role']!='train':continue
        folder=DATA/row['split']/row['sample_id'];poses=json.loads((folder/(row['group']['name']+'_poses.json')).read_text())['frames']
        n=row['metadata']['total_frame_count'];width=row['metadata']['frame_width'];height=row['metadata']['frame_height']
        cleaned=[clean_pose(poses.get(str(i+1),{})) for i in range(n)];boxes,valid=fixed_boxes(cleaned,width,height)
        for hand,okay in zip(boxes[1:],valid):
            if okay:sides.append(hand[2]-hand[0])
        for p,c in cleaned:
            old=crop_box(p,c,width,height,True)
            if old:
                for hand,okay in zip(boxes[1:],valid):
                    if okay:ratios.append((hand[2]-hand[0])/(old[2]-old[0]))
        for event in (row.get('ground_truth') or {}).get('detections',[]):
            x=event['x']*width;y=event['y']*height
            point_covered.append(any(okay and box[0]<=x<=box[2] and box[1]<=y<=box[3] for box,okay in zip(boxes[1:],valid)))
    return {'median_new_hand_side_px':float(np.median(sides)),'median_new_to_old_hand_side_ratio':float(np.median(ratios)),
        'training_contact_point_covered_by_either_hand_crop':float(np.mean(point_covered)),
        'scope':'Training-only geometry; point inclusion is not measured item visibility or bounding-box recall.'}


def main():
    torch.set_num_threads(2);report=json.loads((OUT/'pilot_result.json').read_text());info,ids,values,y,n=dataset()
    name=report['selected_by_mean_selection_ap'];artifacts=[torch.load(OUT/f'{name}_{seed}.pt',weights_only=True,map_location='cpu') for seed in [43,44]]
    selection=np.array([i for i,s in enumerate(ids) if info['views'][int(s)]['pilot_role']=='selection']);predictions=[]
    for artifact in artifacts:
        model=head_for(name).cuda().eval();model.load_state_dict(artifact['head_state'])
        x,_,_,_=normalize(torch.from_numpy(values[selection]),n[selection],artifact['mean'],artifact['std'])
        predictions.append(predict(model,x[:,artifact['channels']].cuda()))
    thresholds=calibrate(y[selection],np.mean(predictions,axis=0),n[selection])
    context=context_encoder();a=artifacts[0]
    full={'version':6,'head_name':name,'head_states':[v['head_state'] for v in artifacts],
        'context_encoder_state':context.state_dict(),'channels':a['channels'],'mean_selected':a['mean'][a['channels']],
        'std_selected':a['std'][a['channels']],'classes':['take','put','touch'],'thresholds':thresholds,'signature':info['signature'],
        'pilot_index_sha256':hashlib.sha256((OUT/'index.json').read_bytes()).hexdigest(),
        'preprocessing':{'maximum_sparse_frames':32,'context':'features_v3.prepare: 160px dynamic body crop, Kinetics normalization',
            'detail':'features_v6.prepare: fixed body and separate hand crops at 224px, ImageNet normalization',
            'missing_hand':'zero image feature stream plus validity flag','feature_precision':'fp16 cache parity',
            'output':'Mean of two seeded heads; independent sigmoid scores per sampled frame, thresholds fitted on selection only',
            'required':'Provided subject poses and sparse clips; not continuous person/action discovery'},
        'evaluation_status':'Development-only, rare-enriched cohort previously used for V3 tuning. No independent test claim.',
        'dataset_license':'Retain Standard.AI Dataset License and attribution; see data/retailaction_v3/LICENSE and NOTICE.'}
    if name!='context':full['image_encoder_state']=encoder().state_dict()
    torch.save(full,OUT/'selected_full.pt');artifact,context,image,heads=load_full(OUT/'selected_full.pt')
    saved=np.load(OUT/'check_predictions.npz');slots=saved['slots'];errors=[];agreements=[];seen=set()
    for position,slot in enumerate(slots):
        row=info['views'][int(slot)];key=(row['split'],row['sample_id'])
        if key in seen:continue
        seen.add(key);inputs=prepared_inputs(row);value=forward(inputs,artifact,context,image,heads).cpu().numpy();expected=saved[name][position:position+1]
        errors.append(float(np.abs(value-expected).max()));t=np.array(thresholds)[None,:,None];length=inputs[-1]
        agreements.append(float(((value>=t)==(expected>=t))[:,:,:length].mean()))
        if len(errors)==3:break
    if max(errors)>.03 or min(agreements)<.95:raise ValueError('Raw clip export parity failed')
    times=[]
    for i in range(10):
        start=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True);start.record();forward(inputs,artifact,context,image,heads);end.record();end.synchronize()
        if i>=2:times.append(start.elapsed_time(end))
    export={'verified_raw_clips':len(errors),'max_probability_error':max(errors),'threshold_decision_agreement':agreements,
        'bytes':(OUT/'selected_full.pt').stat().st_size,'sha256':hashlib.sha256((OUT/'selected_full.pt').read_bytes()).hexdigest(),
        'median_prepared_input_gpu_forward_ms':float(np.median(times)),
        'scope':'Both encoders and two averaged heads, 32-frame input; excludes decode/crop/pose inference and tracking.'}
    (OUT/'export_checks.json').write_text(json.dumps(export,indent=2)+'\n')
    check_views=[info['views'][int(i)] for i in slots];keys=[(r['split'],r['sample_id']) for r in check_views];groups=sorted(set(keys));lookup={g:np.flatnonzero(np.array([k==g for k in keys])) for g in groups}
    rng=np.random.default_rng(606);deltas=[]
    for _ in range(300):
        draw=rng.integers(0,len(groups),len(groups));indices=np.concatenate([lookup[groups[int(i)]] for i in draw]);truth=saved['truth'][indices];lengths=saved['lengths'][indices]
        d=frame_metrics(truth,saved['detail'][indices],lengths)['macro_ap'];b=frame_metrics(truth,saved['body'][indices],lengths)['macro_ap'];deltas.append(d-b)
    uncertainty={'unit':'Paired source clip group, all sampled frames/views together','resamples':300,'seed':606,
        'detail_minus_body_ensemble_macro_ap_95pct':np.percentile(deltas,[2.5,97.5]).tolist(),
        'scope':'Conditional development uncertainty only; not independent shopper/store/day confirmation.'}
    (OUT/'uncertainty.json').write_text(json.dumps(uncertainty,indent=2)+'\n');g=geometry(info);(OUT/'crop_audit.json').write_text(json.dumps(g,indent=2)+'\n')
    write_report(report,export,uncertainty,g);print(json.dumps({'export':export,'uncertainty':uncertainty,'geometry':g},indent=2),flush=True)


def write_report(report,export,uncertainty,geometry):
    rows=['| Inputs | Mean selection macro AP | Mean development-check macro AP |','|---|---:|---:|']
    for name in ['context','body','detail']:
        check=np.mean([r['check_frame']['macro_ap'] for r in report['results'][name]])
        rows.append(f'| {name} | {report["selection_macro_ap_mean"][name]:.3f} | {check:.3f} |')
    cls=['| Action | Body selection AP | Detail selection AP | Body check AP | Detail check AP |','|---|---:|---:|---:|---:|']
    for name in ['take','put','touch']:
        b=report['selection_class_ap_mean']['body'][name];d=report['selection_class_ap_mean']['detail'][name]
        bc=np.mean([r['check_frame']['per_class_ap'][name] for r in report['results']['body']]);dc=np.mean([r['check_frame']['per_class_ap'][name] for r in report['results']['detail']])
        cls.append(f'| {name} | {b:.3f} | {d:.3f} | {bc:.3f} | {dc:.3f} |')
    text=f'''# V6: hand-detail development experiment

Completed 2 November 2026 on the local RTX 4050 Laptop. No paid compute. Selected architecture: **{report['selected_by_mean_selection_ap']}**, using mean selection AP across seeds 43/44. Full export averages the two selected heads; table values are the mean of per-seed metrics, not ensemble metrics.

## Controlled result

{chr(10).join(rows)}

{chr(10).join(cls)}

Same training and development examples, labels, loss and temporal-head family are used for each feature ablation. `context` uses original Kinetics X3D body/pose features; `body` adds per-frame DINOv2 fixed-body embeddings; `detail` additionally adds separate left/right hand-region embeddings and validity masks. This tests feature augmentation, with different input-layer parameter counts, not identical capacity.

Training: {report['support']['train']['clip_groups']} source clip groups / {report['support']['train']['views']} views. Selection and development-check: {report['support']['selection']['clip_groups']} and {report['support']['check']['clip_groups']} groups. Each development half has {report['support']['selection']['positive_clip_groups']['touch']} touch-positive groups. The 256-group pilot preceded this bounded 512-group expansion. Existing features were reused; see `parent_cache_reused.json`.

Detail-minus-body mean selection macro AP: {report['detail_minus_body_selection_ap']:+.3f}; check: {report['detail_minus_body_check_ap']:+.3f}. The predefined detail-expansion gate is {report['expand_detail_experiment']}. Touch may move differently from the aggregate; inspect its row rather than claiming all classes improved.

For the averaged-head predictions, a 300-resample paired-clip bootstrap estimates the check macro-AP difference (detail minus body) interval as {uncertainty['detail_minus_body_ensemble_macro_ap_95pct']}. This describes conditional development uncertainty and excludes adaptive model/data selection effects.

## Scope and safeguards

These are rare-enriched **development** results. Selection/check groups were already V3 tuning; neither is a new independent test. No official-test model predictions were computed in this run. Scores are not comparable with V5's 84.6% annotation-centered window accuracy, natural-prevalence alert precision or the RetailAction localization benchmark.

Paired source clip groups stay in one role. Encoders are untouched Kinetics X3D-S and original DINOv2 ViT-S/14, not V2/V5 supervised features. No ground-truth interaction point is used for an input crop. The model retains nonuniform sampled frame order; original capture-time alignment is not fixed in this crop experiment.

The tiny-set learnability check passed before expansion; it is training memorization, not validation accuracy. Four training-category examples were visually inspected. Source labels have not been certified by a human annotation campaign.

DINOv2 code revision: `{report['signature']['dinov2_git_revision']}`. Original ViT-S/14 checkpoint SHA-256: `{report['signature']['dinov2_sha256']}`. The downloaded checkpoint is `models/dinov2_vits14_pretrain.pth`, from `https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/dinov2_vits14_pretrain.pth`. The full export includes these weights; no xFormers installation was needed for the measured run.

## Crop and export checks

Median separate-hand crop side is {geometry['median_new_hand_side_px']:.1f} source pixels; median side ratio to the old broad hand crop is {geometry['median_new_to_old_hand_side_ratio']:.3f}. Training contact-point inclusion by either hand region is {geometry['training_contact_point_covered_by_either_hand_crop']:.1%}. These are geometric diagnostics, not measured object visibility/recall.

Complete experimental export: `{OUT.relative_to(ROOT)}/selected_full.pt` ({export['bytes']:,} bytes), including required encoder weights, both temporal heads, normalizers, provenance and selection-fitted thresholds. `audit_v6.load_full(path)` loads it. Raw-clip reload on {export['verified_raw_clips']} cases gives max probability error {export['max_probability_error']:.5f}; threshold agreement {export['threshold_decision_agreement']}.

Prepared-input GPU forward median: {export['median_prepared_input_gpu_forward_ms']:.1f} ms, excluding decoding, cropping, pose estimation and tracking. This is not camera-capacity or continuous-stream latency.

Existing checks cover hand coordinate conventions, missing-hand flags, fixed crops and group isolation. Model/data terms remain applicable: DINOv2's original Apache-2.0 assets and RetailAction's Standard.AI Dataset License. Retain notices and source provenance.

## Next work

Keep V2/V5 available for their window-classification task. V6 shows small, uncertain development gains and is not a deployment replacement. The full-clip hand crops are only modestly tighter; a shorter-time stabilized crop is a candidate for the next controlled input experiment. Avoid another broad sweep. Prioritize human review of touch/return ambiguity and per-view visibility, plus original timestamp handling, before further scaling. A training-only touch review queue is available as `touch_review_queue.jsonl` in the run directory. The partner should complete P0 replay/persistence and P1 review export from `PARTNER_START_HERE.md` concurrently.
'''
    (ROOT/'TRAINING_RESULT_V6.md').write_text(text)


def review_pack():
    from features_v6 import DATA
    info=plan();groups={}
    for row in info['views']:
        events=(row.get('ground_truth') or {}).get('detections',[])
        if row['pilot_role']!='train' or not any(e['label']=='touch' for e in events):continue
        key=(row['split'],row['sample_id']);entry=groups.setdefault(key,{'clip_group':'/'.join(key),'role':'train','review_status':'unreviewed','review_reasons':[],'views':[]})
        folder=DATA/row['split']/row['sample_id'];name=row['group']['name'];poses=json.loads((folder/(name+'_poses.json')).read_text())['frames']
        cleaned=[clean_pose(poses.get(str(i+1),{})) for i in range(row['metadata']['total_frame_count'])]
        width=row['metadata']['frame_width'];height=row['metadata']['frame_height'];boxes,valid=fixed_boxes(cleaned,width,height)
        annotations=[{'action':e['label'],'start_frame':e['support'][0]-1,'end_frame_exclusive':e['support'][1],
            'contact_xy_normalized':[e['x'],e['y']]} for e in events]
        contact_covered=all(any(ok and b[0]<=e['x']*width<=b[2] and b[1]<=e['y']*height<=b[3] for b,ok in zip(boxes[1:],valid)) for e in events if e['label']=='touch')
        if len({e['label'] for e in events})>1:entry['review_reasons'].append('Multiple source action classes')
        if not all(valid):entry['review_reasons'].append('Missing hand pose')
        if not contact_covered:entry['review_reasons'].append('Touch point outside separate hand crops')
        entry['views'].append({'camera_view':name,'video_ref':str((folder/(name+'_video.mp4')).relative_to(ROOT)),
            'sha256':row['remote_sha256'],'pose_ref':str((folder/(name+'_poses.json')).relative_to(ROOT)),
            'source_annotations_not_human_reverified':annotations,'frame_index_base':0,'source_kind':'sparse_clip',
            'capture_time_utc':None,'hand_validity':valid,'touch_contact_crop_covered':contact_covered})
    queue=[]
    for entry in groups.values():
        if len(entry['views'])<2:entry['review_reasons'].append('Only one usable camera view')
        entry['review_reasons']=sorted(set(entry['review_reasons']));entry['review_questions']=['Is the designated subject correct?',
            'Is the action visible in each view?', 'Does the item stay, leave, or return?', 'Are source class and frame boundaries correct?', 'Is the result ambiguous or unobservable?']
        queue.append(entry)
    queue.sort(key=lambda e:(-len(e['review_reasons']),e['clip_group']))
    (OUT/'touch_review_queue.jsonl').write_text(''.join(json.dumps(e,separators=(',',':'))+'\n' for e in queue))
    print(f'Training-only review queue: {len(queue)} clip groups; source annotations preserved, no new human labels asserted.',flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--review-only',action='store_true');args=parser.parse_args()
    if args.review_only:review_pack()
    else:main();review_pack()
