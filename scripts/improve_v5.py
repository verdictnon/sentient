"""Bounded V2 weight soups, residual-head shrinkage and neighbour correction."""
import json
import hashlib
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
import improve_v2 as v4
from train_expanded import tail_model,Activations,logits_for,classifier,selection_score
from train_pilot import ROOT,CLASSES,metrics

OUT=ROOT/'runs/x3d_s_v5'


class CorrectedHead(torch.nn.Module):
    def __init__(self,spec,bank_size=0):
        super().__init__();self.spec=spec;self.base=classifier()
        kind=spec['kind']
        if kind=='shrink':self.extra=v4.Head(spec['head'])
        elif kind=='knn':
            self.register_buffer('bank',torch.empty(bank_size,2048));self.register_buffer('targets',torch.empty(bank_size,dtype=torch.long))
            self.register_buffer('class_weight',torch.ones(4))
        elif kind=='prototypes':self.register_buffer('centres',torch.zeros(4,2048))
    def forward(self,x):
        original=self.base(x);kind=self.spec['kind'];amount=self.spec['amount']
        if kind=='shrink':return (1-amount)*original+amount*self.extra(x)
        value=F.normalize(F.layer_norm(x,(2048,)),dim=1)
        if kind=='knn':
            similarity=value@self.bank.T;top,indices=similarity.topk(self.spec['k'],dim=1)
            weights=((top-top[:,0:1])/.07).exp();votes=torch.full((len(x),4),.01,device=x.device)
            votes.scatter_add_(1,self.targets[indices],weights*self.class_weight[self.targets[indices]])
            other=votes/votes.sum(1,keepdim=True)
        else:other=(value@self.centres.T/.07).softmax(1)
        return ((1-amount)*original.softmax(1)+amount*other).clamp_min(1e-7).log()


def spec_for(name):
    if name.startswith('shrink_'):
        _,head,amount=name.split(':');return {'kind':'shrink','head':head,'amount':float(amount)}
    if name.startswith('knn:'):
        _,k,amount=name.split(':');return {'kind':'knn','k':int(k),'amount':float(amount)}
    return {'kind':'prototypes','amount':float(name.split(':')[1])}


def corrected(name,features,labels,train):
    spec=spec_for(name);head=CorrectedHead(spec,len(train));head.base.load_state_dict(v4.original()['head_state'])
    if spec['kind']=='shrink':head.extra.load_state_dict(torch.load(v4.OUT/(spec['head']+'.pt'),weights_only=True,map_location='cpu')['head_state'])
    else:
        bank=F.normalize(F.layer_norm(torch.from_numpy(features[train].copy()),(2048,)),dim=1)
        targets=torch.from_numpy(labels[train].copy())
        if spec['kind']=='knn':
            head.bank.copy_(bank);head.targets.copy_(targets)
            count=torch.bincount(targets,minlength=4).float();weight=count.clamp_min(1).rsqrt();weight/=weight.mean();head.class_weight.copy_(weight)
        else:
            centres=torch.stack([bank[targets==c].mean(0) for c in range(4)]);head.centres.copy_(F.normalize(centres,dim=1))
    return head.cuda().eval()


def soup(alpha):
    tail=tail_model();old=tail.state_dict();fine=v4.original()
    tail.load_state_dict({k:(alpha*fine['tail_state'][k]+(1-alpha)*v) if v.is_floating_point() else v for k,v in old.items()})
    baseline=torch.load(v4.SOURCE/'baseline.pt',weights_only=True,map_location='cpu');head=classifier()
    head.load_state_dict({k:alpha*fine['head_state'][k]+(1-alpha)*v for k,v in baseline['head_state'].items()})
    return tail.cuda().eval(),head.cuda().eval()


def run():
    torch.set_num_threads(2);torch.cuda.set_per_process_memory_fraction(.7);OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'evaluation.json').exists():print('V5 evaluated run is locked');return
    info,labels,roles=v4.setup();features=v4.cached(info,labels,roles);ids=roles['tune'];truth=labels[ids]
    folds=np.array([int(hashlib.sha256((info['rows'][int(i)]['split']+'/'+info['rows'][int(i)]['sample']).encode()).hexdigest(),16)%2 for i in ids])
    candidates=['v2']+[f'soup:{a}' for a in [.5,.75]]+[f'shrink_:{h}:{a}' for h in v4.HEADS for a in [.25,.5]]
    candidates +=[f'knn:{k}:{a}' for k in [5,15,31] for a in [.15,.3]]+[f'prototypes:{a}' for a in [.15,.3]]
    protocol={'source':'Existing V2 classifier and cached learned features','candidates':candidates,
        'selection':'Tuning take/put/background F1; grouped two-fold out-of-fold calibration; minimum +0.005 versus V2',
        'evaluation':'Same previously inspected V2 holdout, development comparison only','signature':info['signature']}
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    vx=torch.from_numpy(features[ids].copy()).cuda();results={};values={}
    for name in candidates:
        if name=='v2':value=v4.candidate_logits(name,features,ids,labels)
        elif name.startswith('soup:'):
            tail,head=soup(float(name.split(':')[1]));value=logits_for(tail,head,ids,labels).numpy();del tail,head
        else:
            head=corrected(name,features,labels,roles['train']);value=v4.head_predict(head,vx);del head
        values[name]=value;results[name]={'uncalibrated':v4.score(truth,value.argmax(1)),'calibrated_oof':v4.cross_calibrate(truth,value,folds)}
        print(f'{name}: tuning raw {results[name]["uncalibrated"]:.3f}, OOF calibrated {results[name]["calibrated_oof"]:.3f}',flush=True)
    score,name,mode=max((r[mode],name,mode) for name,r in results.items() for mode in ['uncalibrated','calibrated_oof'])
    if score<results['v2']['uncalibrated']+.005:name='v2';mode='uncalibrated'
    bias=v4.fit_bias(truth,values[name]) if mode=='calibrated_oof' else np.zeros(4,np.float32)
    decision={'selected':name,'calibration':mode,'bias':bias.tolist(),'tuning_results':results,'signature':info['signature'],
        'evaluation_status':protocol['evaluation'],'selection_rule':protocol['selection']}
    (OUT/'selection_before_evaluation.json').write_text(json.dumps(decision,indent=2)+'\n');print('Selected '+name+' '+mode,flush=True)
    features=v4.cached(info,labels,roles,True);ids=roles['holdout'];truth=torch.as_tensor(labels[ids])
    baseline=v4.candidate_logits('v2',features,ids,labels)
    if name=='v2':chosen=baseline
    elif name.startswith('soup:'):
        tail,head=soup(float(name.split(':')[1]));chosen=logits_for(tail,head,ids,labels).numpy()
    else:
        head=corrected(name,features,labels,roles['train']);chosen=v4.head_predict(head,torch.from_numpy(features[ids].copy()).cuda())
    chosen=chosen+bias
    report={**decision,'baseline_v2':metrics(truth,torch.from_numpy(baseline).argmax(1)),
        'selected_v5':metrics(truth,torch.from_numpy(chosen).argmax(1)),
        'baseline_fused':v4.fused(info['rows'],ids,truth,torch.from_numpy(baseline)),
        'selected_fused':v4.fused(info['rows'],ids,truth,torch.from_numpy(chosen)),
        'holdout_windows':len(ids),'holdout_clip_groups':len({info['rows'][int(i)]['sample'] for i in ids})}
    (OUT/'evaluation.json').write_text(json.dumps(report,indent=2)+'\n')
    np.savez_compressed(OUT/'comparison_predictions.npz',indices=ids,truth=truth.numpy(),baseline=baseline,selected=chosen)
    export(name,bias,features,labels,roles)
    print(json.dumps({'selected':name,'v2':report['baseline_v2'],'new':report['selected_v5']},indent=2),flush=True)


def export(name,bias,features,labels,roles):
    from pytorchvideo.models.hub.x3d import x3d_s
    full=torch.load(v4.SOURCE/'selected_full.pt',weights_only=True,map_location='cpu');full['version']=5
    full['bias']=bias.tolist();full['selection']=name;full['head_kind']='classifier'
    if name.startswith('soup:'):
        tail,head=soup(float(name.split(':')[1]));model=x3d_s(pretrained=False)
        model.blocks[-1].proj=torch.nn.Identity();model.blocks[-1].activation=torch.nn.Identity();model.load_state_dict(full['backbone_state'])
        torch.nn.Sequential(*list(model.blocks)[4:]).load_state_dict(tail.cpu().state_dict());full['backbone_state']=model.state_dict();full['head_state']=head.cpu().state_dict()
    elif name!='v2':
        head=corrected(name,features,labels,roles['train']).cpu();full['head_state']=head.state_dict();full['head_kind']='corrected'
        full['head_spec']=head.spec;full['bank_size']=len(roles['train']) if head.spec['kind']=='knn' else 0
    torch.save(full,OUT/'selected_full.pt')


if __name__=='__main__':run()
