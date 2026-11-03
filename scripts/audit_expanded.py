"""Cluster-aware uncertainty and a complete, reload-verified model checkpoint."""
import json
import time
import numpy as np
import torch
from train_pilot import ROOT,CLASSES
from train_expanded import OUT,classifier,tail_model
from cache_expanded import prepare
from pytorchvideo.models.hub.x3d import x3d_s

torch.set_num_threads(2)
result=json.loads((OUT/'evaluation.json').read_text());info=json.loads((OUT/'index.json').read_text())
data=np.load(OUT/'holdout_predictions.npz');ids=data['indices'];truth=data['truth']
keys=[info['rows'][int(i)]['sample'] for i in ids];visits=sorted(set(keys));lookup={key:i for i,key in enumerate(visits)}
matrices={}
for name in ['baseline','finetuned']:
    predictions=data[name+'_logits'].argmax(1);confusions=np.zeros((len(visits),4,4),dtype=np.int64)
    for key,target,prediction in zip(keys,truth,predictions):confusions[lookup[key],target,prediction]+=1
    matrices[name]=confusions
rng=np.random.default_rng(42);draws=rng.multinomial(len(visits),np.full(len(visits),1/len(visits)),size=500)
def scores(name):
    cm=(draws@matrices[name].reshape(len(visits),16)).reshape(500,4,4)
    denominator=cm.sum(1)+cm.sum(2)
    f1=np.divide(2*np.diagonal(cm,axis1=1,axis2=2),denominator,out=np.zeros_like(denominator,dtype=float),where=denominator>0)
    return f1.mean(1),f1[:,[0,1,3]].mean(1)
b_all,b_common=scores('baseline');f_all,f_common=scores('finetuned')
selected=result['chosen_by_tuning_only'];selected_scores=f_all if selected=='finetuned' else b_all
uncertainty={'method':'500 paired nonparametric bootstrap resamples of whole validation visits; paired views/windows kept together.',
             'independent_visits':len(visits),'selected_four_class_macro_f1_95pct':np.quantile(selected_scores,[.025,.975]).tolist(),
             'finetuned_minus_baseline_macro_f1_95pct':np.quantile(f_all-b_all,[.025,.975]).tolist(),
             'finetuned_minus_baseline_selection_classes_f1_95pct':np.quantile(f_common-b_common,[.025,.975]).tolist(),
             'caution':'Fixed four-class macro averaging uses zero for undefined bootstrap class F1; rare touch support remains inadequate.'}
(OUT/'uncertainty.json').write_text(json.dumps(uncertainty,indent=2)+'\n')

artifact=torch.load(OUT/'selected.pt',weights_only=True,map_location='cpu')
model=x3d_s(pretrained=False)
model.load_state_dict(torch.load(ROOT/'models/X3D_S.pyth',weights_only=True,map_location='cpu')['model_state'])
model.blocks[-1].proj=torch.nn.Identity();model.blocks[-1].activation=torch.nn.Identity()
if artifact['kind']=='last_stage_finetuned':torch.nn.Sequential(*list(model.blocks)[4:]).load_state_dict(artifact['tail_state'])
head=classifier();head.load_state_dict(artifact['head_state'])
full={'architecture':'x3d_s_feature_backbone_plus_layernorm_head','backbone_state':model.state_dict(),
      'head_state':head.state_dict(),'classes':CLASSES,'source_signature':info['signature'],
      'selection':result['chosen_by_tuning_only'],'preprocessing':info['protocol'],
      'input_shape':[3,13,160,160],'normalization_mean':[.45]*3,'normalization_std':[.225]*3}
torch.save(full,OUT/'selected_full.pt')
# Reload the exported weights and verify against cached holdout logits on real clips.
loaded=torch.load(OUT/'selected_full.pt',weights_only=True,map_location='cpu')
model.load_state_dict(loaded['backbone_state']);head.load_state_dict(loaded['head_state'])
model=model.eval().requires_grad_(False).cuda();head=head.eval().requires_grad_(False).cuda()
errors=[];seen=set();last=None
for position,slot in enumerate(ids):
    row=info['rows'][int(slot)]
    if row['sample'] in seen:continue
    seen.add(row['sample']);view=next(v for v in info['views'] if int(slot) in v['indices'])
    _,tensor,error=prepare({**view,'indices':[int(slot)]},info['rows'])[0]
    if error:raise ValueError(error)
    tensor=tensor[None].cuda()
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):features=model(tensor)
    with torch.inference_mode():logits=head(features.float()).cpu()
    expected=torch.from_numpy(data[selected+'_logits'][position:position+1].copy())
    difference=float((logits-expected).abs().max());errors.append(difference)
    if not torch.allclose(logits,expected,atol=.05,rtol=.005):raise ValueError(f'Export parity failure: {difference}')
    if logits.argmax(1).item()!=expected.argmax(1).item():raise ValueError('Export prediction changed')
    last=tensor
    if len(errors)==3:break
def forward():
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):features=model(last)
    with torch.inference_mode():return head(features.float())
for _ in range(5):forward()
torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats();times=[]
for _ in range(20):
    a=torch.cuda.Event(enable_timing=True);b=torch.cuda.Event(enable_timing=True)
    a.record();forward();b.record();b.synchronize();times.append(a.elapsed_time(b))
profile={'export_verified_clips':len(errors),'max_abs_logit_difference':max(errors),
         'median_forward_ms':float(np.median(times)),'p95_forward_ms':float(np.quantile(times,.95)),
         'peak_cuda_allocated_mib':torch.cuda.max_memory_allocated()/1024**2,
         'scope':'Warm batch-one full-backbone plus classifier GPU forward; excludes decoding, cropping and streaming.'}
(OUT/'export_profile.json').write_text(json.dumps(profile,indent=2)+'\n')
print(json.dumps({'uncertainty':uncertainty,'export_profile':profile},indent=2))
