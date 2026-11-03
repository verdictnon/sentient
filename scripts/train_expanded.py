"""Same-data frozen baseline vs last-stage fine-tuning; holdout only after selection."""
import argparse
from collections import Counter,defaultdict
import copy
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import torch
from torch.utils.data import Dataset,DataLoader
from train_pilot import ROOT,CLASSES,metrics
from pytorchvideo.models.hub.x3d import x3d_s

OUT=ROOT/'runs/x3d_s_v2'


def load_data(phase):
    info=json.loads((OUT/'index.json').read_text());status=np.load(OUT/'status.npy',mmap_mode='r')
    actual=hashlib.sha256((ROOT/'data/retailaction_v2/manifest.json').read_bytes()).hexdigest()
    if actual!=info['signature']['manifest_sha256']:raise ValueError('Dataset manifest changed after caching')
    rows=info['rows'];valid=np.flatnonzero(status==1)
    labels=np.array([CLASSES.index(r['label']) for r in rows],dtype=np.int64)
    roles={role:np.array([i for i in valid if rows[i]['role']==role]) for role in ['train','tune','holdout']}
    required=['train','tune','holdout'] if phase=='evaluate' else ['train','tune']
    if any(status[i]==0 for i,row in enumerate(rows) if row['role'] in required):raise RuntimeError('Required activation cache is incomplete')
    return info,labels,roles


def classifier():
    return torch.nn.Sequential(torch.nn.LayerNorm(2048,elementwise_affine=False),
                               torch.nn.Dropout(.2),torch.nn.Linear(2048,4))


def weights(labels):
    count=torch.bincount(torch.as_tensor(labels),minlength=4).float()
    value=count.clamp_min(1).rsqrt();value/=value.mean()
    return value.clamp(.2,5)


def selection_score(result):
    # Tuning has only one touch visit; do not select an epoch on that singleton.
    return sum(result['per_class'][name]['f1'] for name in ['take','put','background'])/3


def baseline(info,labels,roles):
    torch.manual_seed(42);torch.set_num_threads(4)
    features=torch.from_numpy(np.load(OUT/'pooled.npy').copy())
    ids=roles['train'];tune=roles['tune'];target=torch.from_numpy(labels)
    head=classifier();optimizer=torch.optim.AdamW(head.parameters(),lr=3e-4,weight_decay=.03)
    loss_fn=torch.nn.CrossEntropyLoss(weight=weights(labels[ids]),label_smoothing=.05)
    best=-1;best_state=None;history=[];started=time.monotonic()
    for epoch in range(1,41):
        head.train();total=0
        for batch in torch.randperm(len(ids)).split(128):
            indices=ids[batch.numpy()];optimizer.zero_grad();loss=loss_fn(head(features[indices]),target[indices])
            loss.backward();optimizer.step();total+=float(loss.detach())*len(batch)
        head.eval()
        with torch.no_grad():score=metrics(target[tune],head(features[tune]).argmax(1))
        history.append({'epoch':epoch,'loss':total/len(ids),'tune_macro_f1':score['macro_f1_supported']})
        if selection_score(score)>best:
            best=selection_score(score);best_state=copy.deepcopy(head.state_dict());best_epoch=epoch;best_metrics=score
    artifact={'kind':'frozen_baseline','head_state':best_state,'classes':CLASSES,'signature':info['signature'],
              'tune':best_metrics,'selection_metric':'macro_f1_take_put_background','selection_score':best,
              'best_epoch':best_epoch,'seconds':time.monotonic()-started}
    torch.save(artifact,OUT/'baseline.pt');(OUT/'baseline_history.json').write_text(json.dumps(history,indent=2))
    print(json.dumps({k:v for k,v in artifact.items() if k!='head_state'},indent=2),flush=True)


class Activations(Dataset):
    def __init__(self,ids,labels):
        self.ids=ids;self.labels=labels;self.array=np.load(OUT/'early.npy',mmap_mode='r')
    def __len__(self):return len(self.ids)
    def __getitem__(self,index):
        slot=int(self.ids[index]);return torch.from_numpy(self.array[slot].copy()),int(self.labels[slot])


def tail_model():
    model=x3d_s(pretrained=False)
    model.load_state_dict(torch.load(ROOT/'models/X3D_S.pyth',weights_only=True,map_location='cpu')['model_state'])
    model.blocks[-1].proj=torch.nn.Identity();model.blocks[-1].activation=torch.nn.Identity()
    return torch.nn.Sequential(*list(model.blocks)[4:])


def logits_for(tail,head,ids,labels):
    tail.eval();head.eval();values=[]
    loader=DataLoader(Activations(ids,labels),batch_size=32,num_workers=0,pin_memory=True)
    with torch.inference_mode():
        for batch,_ in loader:
            with torch.autocast('cuda',dtype=torch.float16):features=tail(batch.cuda(non_blocking=True))
            values.append(head(features.float()).cpu())
    return torch.cat(values)


def fine_tune(info,labels,roles,epochs_per_call):
    torch.set_num_threads(2);torch.cuda.set_per_process_memory_fraction(.7);torch.manual_seed(42)
    baseline_artifact=torch.load(OUT/'baseline.pt',weights_only=True)
    tail=tail_model().cuda();head=classifier().cuda();head.load_state_dict(baseline_artifact['head_state'])
    # Frozen BN running statistics and affine parameters are safer with small batches.
    for layer in tail.modules():
        if isinstance(layer,torch.nn.modules.batchnorm._BatchNorm):layer.requires_grad_(False)
    optimizer=torch.optim.AdamW([{'params':[p for p in tail.parameters() if p.requires_grad],'lr':5e-5},
                                {'params':head.parameters(),'lr':2e-4}],weight_decay=.02)
    scaler=torch.amp.GradScaler('cuda')
    loss_fn=torch.nn.CrossEntropyLoss(weight=weights(labels[roles['train']]).cuda(),label_smoothing=.05)
    statepath=OUT/'finetune_state.pt';state=torch.load(statepath,weights_only=True,map_location='cpu') if statepath.exists() else None
    epoch=0;best=-1;bad=0;history=[]
    if state:
        if state['signature']!=info['signature']:raise ValueError('Training data signature changed')
        tail.load_state_dict(state['tail_state']);head.load_state_dict(state['head_state'])
        optimizer.load_state_dict(state['optimizer']);scaler.load_state_dict(state['scaler'])
        epoch=state['epoch'];best=state['best'];bad=state['bad'];history=state['history']
    if epoch>=8 or bad>=3:print('Fine-tuning already complete',flush=True);return
    for _ in range(epochs_per_call):
        if epoch>=8 or bad>=3:break
        epoch+=1;torch.manual_seed(42+epoch);tail.eval();head.train()
        generator=torch.Generator().manual_seed(42+epoch)
        loader=DataLoader(Activations(roles['train'],labels),batch_size=16,shuffle=True,generator=generator,num_workers=0,pin_memory=True)
        torch.cuda.reset_peak_memory_stats()
        started=time.monotonic();total=0;count=0
        for step,(batch,target) in enumerate(loader):
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast('cuda',dtype=torch.float16):features=tail(batch.cuda(non_blocking=True))
            loss=loss_fn(head(features.float()),target.cuda(non_blocking=True))
            scaler.scale(loss).backward();scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(list(tail.parameters())+list(head.parameters()),1)
            scaler.step(optimizer);scaler.update();total+=float(loss.detach())*len(batch);count+=len(batch)
            if step%100==0:print(f'epoch {epoch} batch {step}/{len(loader)} loss {float(loss.detach()):.3f}',flush=True)
        prediction=logits_for(tail,head,roles['tune'],labels).argmax(1)
        result=metrics(torch.as_tensor(labels[roles['tune']]),prediction)
        history.append({'epoch':epoch,'loss':total/count,'tune':result,'seconds':time.monotonic()-started,
                        'peak_cuda_allocated_mib':torch.cuda.max_memory_allocated()/1024**2})
        score=selection_score(result)
        if score>best:
            best=score;bad=0
            artifact={'kind':'last_stage_finetuned','tail_state':{k:v.detach().cpu() for k,v in tail.state_dict().items()},
                      'head_state':{k:v.detach().cpu() for k,v in head.state_dict().items()},'signature':info['signature'],
                      'classes':CLASSES,'epoch':epoch,'tune':result,'selection_metric':'macro_f1_take_put_background','selection_score':score}
            torch.save(artifact,OUT/'finetuned.pt')
        else:bad+=1
        state={'tail_state':tail.state_dict(),'head_state':head.state_dict(),'optimizer':optimizer.state_dict(),
               'scaler':scaler.state_dict(),'epoch':epoch,'best':best,'bad':bad,'history':history,'signature':info['signature']}
        temporary=OUT/'finetune_state.tmp.pt';torch.save(state,temporary);temporary.replace(statepath)
        (OUT/'finetune_history.json').write_text(json.dumps(history,indent=2)+'\n')
        print(f'epoch {epoch} complete in {time.monotonic()-started:.1f}s; tune selection F1 {score:.3f}; best {best:.3f}',flush=True)


def fused(rows,ids,truth,logits):
    groups={}
    for slot,target,value in zip(ids,truth.tolist(),logits):
        row=rows[int(slot)];key=(row['split'],row['sample'],row['event'])
        item=groups.setdefault(key,{'target':target,'values':[]})
        if item['target']!=target:raise ValueError('Inconsistent paired labels')
        item['values'].append(value)
    actual=torch.tensor([g['target'] for g in groups.values()])
    predictions=torch.stack([torch.stack(g['values']).mean(0) for g in groups.values()]).argmax(1)
    return metrics(actual,predictions)


def evaluate(info,labels,roles):
    if (OUT/'evaluation.json').exists():print('Holdout evaluation already recorded; refusing to retune on it');return
    a=torch.load(OUT/'baseline.pt',weights_only=True);b=torch.load(OUT/'finetuned.pt',weights_only=True)
    choice='finetuned' if selection_score(b['tune'])>=selection_score(a['tune'])+.005 else 'baseline'
    decision={'chosen_by_tuning_only':choice,'margin_required':.005,'baseline_tune_macro_f1':a['tune']['macro_f1_supported'],
              'selection_metric':'macro_f1_take_put_background; touch diagnostic only because tuning has one touch visit',
              'baseline_selection_score':selection_score(a['tune']),'finetuned_selection_score':selection_score(b['tune']),
              'finetuned_tune_macro_f1':b['tune']['macro_f1_supported'],'signature':info['signature']}
    (OUT/'selection_before_holdout.json').write_text(json.dumps(decision,indent=2)+'\n')
    ids=roles['holdout'];actual=torch.as_tensor(labels[ids]);head=classifier().eval();head.load_state_dict(a['head_state'])
    features=torch.from_numpy(np.load(OUT/'pooled.npy')[ids].copy())
    with torch.no_grad():base_logits=head(features)
    tail=tail_model().cuda();tail.load_state_dict(b['tail_state']);head=classifier().cuda();head.load_state_dict(b['head_state'])
    fine_logits=logits_for(tail,head,ids,labels)
    planned=json.loads((ROOT/'data/retailaction_v2/manifest.json').read_text())['role_groups']['holdout']
    results={**decision,'holdout_groups_planned':planned,
             'holdout_groups_usable':len({info['rows'][int(i)]['sample'] for i in ids}),'holdout_windows_usable':len(ids),
             'training_windows':len(roles['train']),'tuning_windows':len(roles['tune']),
             'class_counts':{role:dict(Counter(CLASSES[int(labels[i])] for i in indices)) for role,indices in roles.items()},
             'class_visit_counts':{role:{name:len({info['rows'][int(i)]['sample'] for i in indices if CLASSES[int(labels[i])]==name})
                                  for name in CLASSES} for role,indices in roles.items()},
             'baseline':metrics(actual,base_logits.argmax(1)),'finetuned':metrics(actual,fine_logits.argmax(1)),
             'baseline_fused':fused(info['rows'],ids,actual,base_logits),
             'finetuned_fused':fused(info['rows'],ids,actual,fine_logits),
             'limitations':['Supplied poses and annotation-centered clips, not event discovery.',
                            'Frame-indexed mirror labels; not original capture-time localization.',
                            'Holdout has very few independent touch visits; views are correlated.',
                            'Same dataset/store domain; local-camera generalization is untested.']}
    (OUT/'evaluation.json').write_text(json.dumps(results,indent=2)+'\n')
    torch.save(b if choice=='finetuned' else a,OUT/'selected.pt')
    np.savez(OUT/'holdout_predictions.npz',indices=ids,truth=actual.numpy(),baseline_logits=base_logits.numpy(),finetuned_logits=fine_logits.numpy())
    print(json.dumps(results,indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['baseline','finetune','evaluate'],required=True)
    p.add_argument('--epochs-per-call',type=int,default=1);args=p.parse_args()
    if args.phase!='evaluate' and (OUT/'evaluation.json').exists():
        raise RuntimeError('This evaluated experiment is locked. Use a new experiment directory and fresh evaluation plan for further fitting.')
    info,labels,roles=load_data(args.phase)
    if args.phase=='baseline':baseline(info,labels,roles)
    elif args.phase=='finetune':fine_tune(info,labels,roles,args.epochs_per_call)
    else:evaluate(info,labels,roles)
