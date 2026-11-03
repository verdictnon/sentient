"""Learnability gate and matched context/body/detail development pilot."""
import argparse
import json
import time
import numpy as np
import torch
from features_v6 import ROOT,OUT,SOURCE,plan,encoder,prepare
from temporal_v3 import Residual,normalize,frame_metrics,event_metrics,calibrate

HEADS=['context','body','detail'];SEEDS=[43,44]


def dataset(require_complete=True):
    info=plan();status=np.load(OUT/'status.npy',mmap_mode='r')
    if require_complete and np.any(status==0):raise RuntimeError('Finish feature extraction')
    ids=np.flatnonzero(status==1);slots=np.array([r['source_slot'] for r in info['views']])
    context=np.load(SOURCE/'features.npy',mmap_mode='r');labels=np.load(SOURCE/'labels.npy',mmap_mode='r')
    context=context[slots[ids]][:,list(range(192))+list(range(384,506))].astype(np.float32)
    detail=np.load(OUT/'detail_features.npy',mmap_mode='r')[ids].astype(np.float32)
    values=np.concatenate([context,detail],axis=1);lengths=np.load(OUT/'lengths.npy',mmap_mode='r')[ids].copy()
    return info,ids,values,labels[slots[ids]].copy(),lengths


def indices(name):return list(range(314)) if name=='context' else list(range(698)) if name=='body' else list(range(1468))


def head_for(name):
    return torch.nn.Sequential(torch.nn.Conv1d(len(indices(name)),96,1),torch.nn.GELU(),
        *[Residual(96,d) for d in [1,2,4]],torch.nn.Dropout(.2),torch.nn.Conv1d(96,3,1))


def predict(model,x):
    model.eval()
    with torch.inference_mode():return np.concatenate([model(part).sigmoid().cpu().numpy() for part in x.split(64)])


def binary_f1(truth,prediction,lengths):
    mask=np.arange(32)[None,:]<lengths[:,None];values=[]
    for c in range(3):
        y=truth[:,c][mask]>0;p=prediction[:,c][mask]>=.5;tp=int((y&p).sum());fp=int((~y&p).sum());fn=int((y&~p).sum())
        values.append(2*tp/max(2*tp+fp+fn,1))
    return values


def learnability():
    info,ids,values,y,n=dataset(False);lookup={int(slot):i for i,slot in enumerate(ids)}
    wanted=[r['slot'] for r in info['tiny_examples']]
    if any(i not in lookup for i in wanted):raise RuntimeError('Cache the 16 diagnostic views first')
    pick=np.array([lookup[i] for i in wanted]);values=values[pick];y=y[pick];n=n[pick]
    x,mean,std,mask=normalize(torch.from_numpy(values),n);x=x.cuda();mask=mask.cuda();target=torch.from_numpy(y).cuda()
    torch.manual_seed(600);model=head_for('detail').cuda()
    for module in model.modules():
        if isinstance(module,torch.nn.Dropout):module.p=0
    optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=0)
    pos=(target*mask).sum((0,2));weight=((mask.sum()-pos)/pos.clamp_min(1)).sqrt().clamp(1,10)
    loss_fn=torch.nn.BCEWithLogitsLoss(pos_weight=weight[None,:,None],reduction='none');started=time.monotonic();passed=False
    for step in range(1,301):
        model.train();optimizer.zero_grad(set_to_none=True);logits=model(x)
        loss=(loss_fn(logits,target)*mask).sum()/(mask.sum()*3);loss.backward();optimizer.step()
        if step%25==0:
            p=predict(model,x);f1=binary_f1(y,p,n)
            if min(f1)>=.95:passed=True;break
    result={'passed':passed,'views':len(pick),'unique_training_clip_groups':len(pick),'steps':step,
        'per_class_frame_f1_at_0_5':dict(zip(['take','put','touch'],f1)),'seconds':time.monotonic()-started,
        'scope':'Training memorization diagnostic only. Automatic source labels; four sample categories visually inspected. Not validation accuracy.'}
    (OUT/'learnability.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)
    if not passed:raise RuntimeError('Learnability gate failed; inspect before expanding')


def train():
    if (OUT/'pilot_result.json').exists():raise RuntimeError('Completed pilot locked; use a new experiment')
    if not json.loads((OUT/'learnability.json').read_text())['passed']:raise RuntimeError('Learnability check required')
    info,ids,values,y,n=dataset();roles={r:np.array([i for i,slot in enumerate(ids) if info['views'][int(slot)]['pilot_role']==r]) for r in ['train','selection','check']}
    tr=roles['train'];sel=roles['selection'];check=roles['check'];x,mean,std,mask=normalize(torch.from_numpy(values[tr]),n[tr])
    vx,_,_,_=normalize(torch.from_numpy(values[sel]),n[sel],mean,std);cx,_,_,_=normalize(torch.from_numpy(values[check]),n[check],mean,std)
    x=x.cuda();vx=vx.cuda();cx=cx.cuda();mask=mask.cuda();target=torch.from_numpy(y[tr]).cuda()
    pos=(target*mask).sum((0,2));weight=((mask.sum()-pos)/pos.clamp_min(1)).sqrt().clamp(1,10)
    loss_fn=torch.nn.BCEWithLogitsLoss(pos_weight=weight[None,:,None],reduction='none')
    for name in HEADS:
        wanted=indices(name);tx=x[:,wanted];sv=vx[:,wanted]
        for seed in SEEDS:
            path=OUT/f'{name}_{seed}.pt'
            if path.exists():continue
            torch.manual_seed(seed);model=head_for(name).cuda();optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.03)
            best=-1;bad=0;history=[];start=time.monotonic()
            for epoch in range(1,31):
                torch.manual_seed(seed*100+epoch);model.train()
                for batch in torch.randperm(len(tr),device='cuda').split(32):
                    optimizer.zero_grad(set_to_none=True);logits=model(tx[batch]);loss=(loss_fn(logits,target[batch])*mask[batch]).sum()/(mask[batch].sum()*3)
                    loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
                p=predict(model,sv);metric=frame_metrics(y[sel],p,n[sel]);history.append({'epoch':epoch,'selection':metric})
                if metric['macro_ap']>best:
                    best=metric['macro_ap'];bad=0;bestepoch=epoch;state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
                else:bad+=1
                if bad>=6:break
            artifact={'head_name':name,'seed':seed,'head_state':state,'epoch':bestepoch,'selection_frame_macro_ap':best,
                'mean':mean,'std':std,'channels':wanted,'signature':info['signature'],'history':history,'seconds':time.monotonic()-start}
            torch.save(artifact,path);print(f'{name} seed {seed}: selection macro AP {best:.3f}, best epoch {bestepoch}',flush=True)


def evaluate():
    if (OUT/'pilot_result.json').exists():print('Pilot result already recorded');return
    info,ids,values,y,n=dataset();roles={r:np.array([i for i,slot in enumerate(ids) if info['views'][int(slot)]['pilot_role']==r]) for r in ['train','selection','check']}
    sel=roles['selection'];check=roles['check'];results={};all_p={};mean_scores={};class_scores={}
    # Record selection before computing any new development-check predictions.
    preselection={}
    for name in HEADS:
        preselection[name]=[]
        for seed in SEEDS:
            artifact=torch.load(OUT/f'{name}_{seed}.pt',weights_only=True,map_location='cpu');model=head_for(name).cuda();model.load_state_dict(artifact['head_state'])
            sx,_,_,_=normalize(torch.from_numpy(values[sel]),n[sel],artifact['mean'],artifact['std'])
            sp=predict(model,sx[:,artifact['channels']].cuda())
            preselection[name].append(frame_metrics(y[sel],sp,n[sel])['macro_ap'])
    choice=max(HEADS,key=lambda name:np.mean(preselection[name]))
    (OUT/'selection_before_check.json').write_text(json.dumps({'selected':choice,'selection_scores':preselection,'uses_check_for_ranking':False},indent=2)+'\n')
    for name in HEADS:
        metrics=[];class_ap=[];predictions=[];thresholds=[]
        for seed in SEEDS:
            artifact=torch.load(OUT/f'{name}_{seed}.pt',weights_only=True,map_location='cpu');model=head_for(name).cuda();model.load_state_dict(artifact['head_state'])
            def inputs(selected):
                x,_,_,_=normalize(torch.from_numpy(values[selected]),n[selected],artifact['mean'],artifact['std'])
                return x[:,artifact['channels']].cuda()
            sp=predict(model,inputs(sel));cp=predict(model,inputs(check));cal=calibrate(y[sel],sp,n[sel]);thresholds.append(cal)
            s=frame_metrics(y[sel],sp,n[sel]);c=frame_metrics(y[check],cp,n[check]);predictions.append(cp)
            class_ap.append(s['per_class_ap']);metrics.append({'seed':seed,'best_epoch':artifact['epoch'],'selection_frame':s,'check_frame':c,
                'check_events':event_metrics(y[check],cp,n[check],cal),'thresholds_from_selection':cal})
        results[name]=metrics;mean_scores[name]=float(np.mean([m['selection_frame']['macro_ap'] for m in metrics]))
        class_scores[name]={c:float(np.mean([m[c] for m in class_ap])) for c in ['take','put','touch']}
        all_p[name]=np.mean(predictions,axis=0)
    selected=max(HEADS,key=lambda name:mean_scores[name])
    if selected!=choice:raise ValueError('Selection changed unexpectedly')
    detail_delta=mean_scores['detail']-mean_scores['body'];rare_delta=np.mean([class_scores['detail'][c]-class_scores['body'][c] for c in ['put','touch']])
    check_delta=float(np.mean([m['check_frame']['macro_ap'] for m in results['detail']])-np.mean([m['check_frame']['macro_ap'] for m in results['body']]))
    expand=detail_delta>=.02 and rare_delta>=0 and check_delta>0
    support={role:{'clip_groups':len({(info['views'][int(ids[i])]['split'],info['views'][int(ids[i])]['sample_id']) for i in slots}),
        'views':len(slots),'positive_clip_groups':{c:len({(info['views'][int(ids[i])]['split'],info['views'][int(ids[i])]['sample_id']) for i in slots if y[i,j].any()}) for j,c in enumerate(['take','put','touch'])}} for role,slots in roles.items()}
    report={'selected_by_mean_selection_ap':selected,'selection_macro_ap_mean':mean_scores,'selection_class_ap_mean':class_scores,
        'detail_minus_body_selection_ap':detail_delta,'detail_minus_body_rare_ap':float(rare_delta),'detail_minus_body_check_ap':check_delta,
        'expand_detail_experiment':bool(expand),'support':support,'results':results,'signature':info['signature'],
        'scope':'Small rare-enriched development pilot; no official-test predictions; development check was previously part of V3 tuning.'}
    (OUT/'pilot_result.json').write_text(json.dumps(report,indent=2)+'\n')
    np.savez_compressed(OUT/'check_predictions.npz',slots=ids[check],truth=y[check],lengths=n[check],**all_p)
    print(json.dumps({k:v for k,v in report.items() if k not in ['results','signature']},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['learnability','train','evaluate'],required=True);args=p.parse_args()
    torch.set_num_threads(2);torch.cuda.set_per_process_memory_fraction(.7)
    if args.phase=='learnability':learnability()
    elif args.phase=='train':train()
    else:evaluate()
