"""One bounded pickup-specific head and precision-oriented demo threshold.

Reuses existing grouped train/tune/development roles. The development holdout has
been inspected previously; its numbers are not continuous CCTV accuracy.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
import torch
from torch import nn

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from train_expanded import load_data
from improve_v5 import corrected

OUT=ROOT/'runs/take_demo_v1'


def save(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def metrics(y,p,t):
    selected=p>=t;tp=int((selected&y).sum());fp=int((selected&~y).sum());fn=int((~selected&y).sum())
    return {'threshold':float(t),'tp':tp,'fp':fp,'fn':fn,
        'precision':tp/(tp+fp) if tp+fp else 0.,'recall':tp/(tp+fn) if tp+fn else 0.}


def choose(y,p):
    values=[metrics(y,p,t) for t in np.arange(.1,.996,.005)]
    valid=[r for r in values if r['tp']+r['fp']>=30 and r['precision']>=.95]
    return max(valid,key=lambda r:(r['recall'],r['threshold'])) if valid else {'recall':0.,'threshold':.995,'precision':0.}


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'evaluation.json').exists():print((OUT/'evaluation.json').read_text());return
    torch.manual_seed(43);torch.set_num_threads(2)
    info,labels,roles=load_data('evaluate')
    features=np.load(ROOT/'runs/x3d_s_v4/v2_features.npy',mmap_mode='r')
    save('protocol.json',{'task':'take vs all other source window labels','epochs_max':20,'seed':43,
        'precision_target_on_tuning':.95,'minimum_tuning_predictions':30,'selection':'Max tuning recall at >=95% empirical tuning precision; keep V5 unless recall improves by >=0.02',
        'source_features':str(ROOT/'runs/x3d_s_v4/v2_features.npy'),'signature':info['signature'],
        'evaluation':'Reused grouped development holdout; not an independent continuous-video accuracy estimate'})
    base=corrected('knn:31:0.15',features,labels,roles['train'])
    tune=roles['tune'];truth=labels[tune]==0
    with torch.inference_mode():
        base_p=np.concatenate([base(torch.from_numpy(features[p].copy()).cuda()).softmax(1)[:,0].cpu().numpy() for p in np.array_split(tune,10)])
    base_metric=choose(truth,base_p)
    model=nn.Sequential(nn.LayerNorm(2048,elementwise_affine=False),nn.Linear(2048,128),nn.GELU(),nn.Dropout(.25),nn.Linear(128,1)).cuda()
    train=roles['train'];x=torch.from_numpy(features[train].copy()).cuda();y=torch.from_numpy((labels[train]==0).astype(np.float32)).cuda()
    tx=torch.from_numpy(features[tune].copy()).cuda()
    optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.03)
    history=[];best=None;best_state=None;start=time.monotonic()
    for epoch in range(1,21):
        model.train()
        for batch in torch.randperm(len(x),device='cuda').split(256):
            optimizer.zero_grad(set_to_none=True);logit=model(x[batch]).flatten()
            loss=nn.functional.binary_cross_entropy_with_logits(logit,y[batch]);loss.backward();optimizer.step()
        model.eval()
        with torch.inference_mode():p=model(tx).flatten().sigmoid().cpu().numpy()
        result=choose(truth,p);history.append({'epoch':epoch,**result})
        if best is None or result['recall']>best['recall']:
            best=result;best_state=copy.deepcopy({k:v.detach().cpu() for k,v in model.state_dict().items()});best_epoch=epoch
        print('take epoch',epoch,'tuning',result,flush=True)
    selected='take_binary_head' if best['recall']>=base_metric['recall']+.02 else 'v5_precision_threshold'
    chosen=best if selected=='take_binary_head' else base_metric
    policy={'format':'sentient.pickup-demo/1','threshold':chosen['threshold'],'merge_gap_ms':500,
        'decision':'model_prediction','target_tuning_precision':.95,'source':'RetailAction grouped development windows',
        'warning':'Precision target is empirical tuning performance; continuous detector-crop transfer remains unverified.'}
    decision={'selected':selected,'epoch':best_epoch,'policy':policy,'baseline_tuning':base_metric,'binary_tuning':best,
        'selection_before_development_evaluation':True,'training_seconds':time.monotonic()-start}
    save('selection-before-evaluation.json',decision);save('history.json',history)
    artifact=torch.load(ROOT/'runs/x3d_s_v5/selected_full.pt',weights_only=True,map_location='cpu')
    artifact['pickup_policy']=policy
    if selected=='take_binary_head':artifact['take_classifier_state']=best_state
    torch.save(artifact,OUT/'selected_full.pt')
    ids=roles['holdout'];hy=labels[ids]==0
    with torch.inference_mode():
        base_test=np.concatenate([base(torch.from_numpy(features[p].copy()).cuda()).softmax(1)[:,0].cpu().numpy() for p in np.array_split(ids,8)])
        if selected=='take_binary_head':
            model.load_state_dict(best_state);model.eval();hp=model(torch.from_numpy(features[ids].copy()).cuda()).flatten().sigmoid().cpu().numpy()
        else:hp=base_test
    report={**decision,'development_baseline':metrics(hy,base_test,base_metric['threshold']),
        'development_selected':metrics(hy,hp,policy['threshold']),'development_windows':len(ids),
        'development_clip_groups':len({info['rows'][int(i)]['sample'] for i in ids}),
        'limitation':'Reused curated pose-cropped windows. Event and continuous camera accuracy are not established.'}
    save('evaluation.json',report)
    np.savez_compressed(OUT/'predictions.npz',indices=ids,truth=hy,baseline=base_test,selected=hp)
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':run()
