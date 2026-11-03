"""V4: improve the existing V2 classifier without changing its input/task."""
import argparse
import copy
import hashlib
import json
import time
import numpy as np
import torch
from torch.utils.data import DataLoader
from train_pilot import ROOT,CLASSES,metrics
from train_expanded import OUT as SOURCE,load_data,classifier,tail_model,Activations,logits_for,fused,selection_score

OUT=ROOT/'runs/x3d_s_v4'
HEADS=['linear_refine','residual','residual_mixup']


def score(y,p):
    cm=np.bincount(np.asarray(y)*4+np.asarray(p),minlength=16).reshape(4,4)
    denominator=cm.sum(0)+cm.sum(1)
    f=np.divide(2*np.diag(cm),denominator,out=np.zeros(4),where=denominator>0)
    return float(f[[0,1,3]].mean())


def fit_bias(y,logits):
    # Touch has one tuning source group; leave its bias fixed rather than fit that singleton.
    bias=np.zeros(4,dtype=np.float32);best=score(y,logits.argmax(1))
    for _ in range(2):
        for c in [1,3]:
            original=bias.copy()
            for value in [-1.5,-1.,-.5,0.,.5,1.,1.5]:
                trial=original.copy();trial[c]=value;result=score(y,(logits+trial).argmax(1))
                if result>best+1e-10:best=result;bias=trial
    return bias


def cross_calibrate(y,logits,folds):
    prediction=np.zeros(len(y),dtype=np.int64)
    for fold in [0,1]:
        held=folds==fold;bias=fit_bias(y[~held],logits[~held])
        prediction[held]=(logits[held]+bias).argmax(1)
    return score(y,prediction)


class Head(torch.nn.Module):
    def __init__(self,kind):
        super().__init__();self.kind=kind;self.base=classifier()
        if kind!='linear_refine':
            self.base.requires_grad_(False)
            self.residual=torch.nn.Sequential(torch.nn.LayerNorm(2048,elementwise_affine=False),
                torch.nn.Linear(2048,128),torch.nn.GELU(),torch.nn.Dropout(.35),torch.nn.Linear(128,4))
            torch.nn.init.zeros_(self.residual[-1].weight);torch.nn.init.zeros_(self.residual[-1].bias)
    def forward(self,x):
        if self.kind=='linear_refine':return self.base(x)
        self.base.eval();return self.base(x)+self.residual(x)


def setup():
    OUT.mkdir(parents=True,exist_ok=True);info,labels,roles=load_data('evaluate')
    protocol={'source':'V2 selected fine-tuned checkpoint; original V2 window task and split retained',
        'signature':info['signature'],'selection':'Mean take/put/background F1 on tuning only; touch diagnostic due one tuning group',
        'calibration':'Two-fold out-of-fold bias scoring, grouped by paired source clip; final bias fit on all tuning',
        'candidates':['unchanged_v2','calibrated_v2']+HEADS+['continued_focal'],
        'evaluation':'Previously inspected V2 holdout, paired diagnostic comparison; NOT a new unseen test or independent confirmation',
        'minimum_selection_gain':.005}
    path=OUT/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=protocol:raise ValueError('V4 protocol changed')
    path.write_text(json.dumps(protocol,indent=2)+'\n')
    return info,labels,roles


def original():return torch.load(SOURCE/'selected.pt',weights_only=True,map_location='cpu')


def cached(info,labels,roles,include_holdout=False):
    n=len(labels);path=OUT/'v2_features.npy';sp=OUT/'feature_status.npy'
    array=np.lib.format.open_memmap(path,mode='r+' if path.exists() else 'w+',dtype=np.float32,shape=(n,2048))
    status=np.lib.format.open_memmap(sp,mode='r+' if sp.exists() else 'w+',dtype=np.int8,shape=(n,))
    ids=np.concatenate([roles[r] for r in (['train','tune','holdout'] if include_holdout else ['train','tune'])]);ids=ids[status[ids]==0]
    if len(ids):
        tail=tail_model().cuda().eval();tail.load_state_dict(original()['tail_state']);start=time.monotonic();offset=0
        loader=DataLoader(Activations(ids,labels),batch_size=64,num_workers=0,pin_memory=True)
        with torch.inference_mode():
            for batch,_ in loader:
                with torch.autocast('cuda',dtype=torch.float16):value=tail(batch.cuda(non_blocking=True)).float().cpu().numpy()
                slots=ids[offset:offset+len(value)];array[slots]=value;status[slots]=1;offset+=len(value)
        array.flush();status.flush();print(f'Cached {len(ids)} fine-tuned V2 feature vectors in {time.monotonic()-start:.1f}s',flush=True)
    return array


def class_weights(y,power=.5):
    w=torch.bincount(torch.as_tensor(y),minlength=4).float().clamp_min(1).pow(-power);return (w/w.mean()).clamp(.2,5)


def head_predict(head,x):
    head.eval()
    with torch.inference_mode():return torch.cat([head(part).cpu() for part in x.split(256)]).numpy()


def train_heads(info,labels,roles):
    if (OUT/'evaluation.json').exists():raise RuntimeError('V4 evaluated run is locked')
    features=cached(info,labels,roles);ids=roles['train'];tune=roles['tune']
    x=torch.from_numpy(features[ids].copy()).cuda();vx=torch.from_numpy(features[tune].copy()).cuda()
    target=torch.as_tensor(labels[ids]).cuda();truth=labels[tune];base=original()
    for kind in HEADS:
        if (OUT/(kind+'.pt')).exists():continue
        torch.manual_seed(200);head=Head(kind).cuda();head.base.load_state_dict(base['head_state'])
        optimizer=torch.optim.AdamW([p for p in head.parameters() if p.requires_grad],lr=5e-5 if kind=='linear_refine' else 1e-4,weight_decay=.04)
        loss_fn=torch.nn.CrossEntropyLoss(weight=class_weights(labels[ids]).cuda(),label_smoothing=.03)
        best=-1;bad=0;history=[];rng=np.random.default_rng(201);start=time.monotonic()
        for epoch in range(1,25):
            torch.manual_seed(200+epoch);head.train()
            for batch in torch.randperm(len(ids),device='cuda').split(128):
                optimizer.zero_grad(set_to_none=True);value=x[batch];y=target[batch]
                if kind=='residual_mixup':
                    lam=float(rng.beta(.2,.2));shuffle=torch.randperm(len(batch),device='cuda')
                    logits=head(lam*value+(1-lam)*value[shuffle]);loss=lam*loss_fn(logits,y)+(1-lam)*loss_fn(logits,y[shuffle])
                else:loss=loss_fn(head(value),y)
                loss.backward();torch.nn.utils.clip_grad_norm_(head.parameters(),1);optimizer.step()
            logits=head_predict(head,vx);metric=score(truth,logits.argmax(1));history.append({'epoch':epoch,'tune_selection_f1':metric})
            if metric>best:
                best=metric;bad=0;state=copy.deepcopy({k:v.detach().cpu() for k,v in head.state_dict().items()});bestepoch=epoch
            else:bad+=1
            if bad>=6:break
        artifact={'kind':kind,'head_state':state,'epoch':bestepoch,'raw_tune_selection_f1':best,'signature':info['signature'],
                  'history':history,'seconds':time.monotonic()-start}
        torch.save(artifact,OUT/(kind+'.pt'));print(f'{kind}: best tune F1 {best:.3f}, epoch {bestepoch}',flush=True)


def continued(info,labels,roles,epochs):
    if (OUT/'evaluation.json').exists():raise RuntimeError('V4 evaluated run is locked')
    base=original();tail=tail_model().cuda();tail.load_state_dict(base['tail_state'])
    head=classifier().cuda();head.load_state_dict(base['head_state'])
    for layer in tail.modules():
        if isinstance(layer,torch.nn.modules.batchnorm._BatchNorm):layer.requires_grad_(False)
    optimizer=torch.optim.AdamW([{'params':[p for p in tail.parameters() if p.requires_grad],'lr':1e-5},
        {'params':head.parameters(),'lr':5e-5}],weight_decay=.03);scaler=torch.amp.GradScaler('cuda')
    weight=class_weights(labels[roles['train']],.6).cuda();path=OUT/'continued_state.pt'
    state=torch.load(path,weights_only=True,map_location='cpu') if path.exists() else None
    epoch=0;best=base['selection_score'];bad=0;history=[]
    if state:
        tail.load_state_dict(state['tail_state']);head.load_state_dict(state['head_state']);optimizer.load_state_dict(state['optimizer'])
        scaler.load_state_dict(state['scaler']);epoch=state['epoch'];best=state['best'];bad=state['bad'];history=state['history']
    for _ in range(epochs):
        if epoch>=6 or bad>=3:break
        epoch+=1;torch.manual_seed(600+epoch);tail.eval();head.train();start=time.monotonic()
        loader=DataLoader(Activations(roles['train'],labels),batch_size=16,shuffle=True,
            generator=torch.Generator().manual_seed(600+epoch),num_workers=0,pin_memory=True)
        for batch,y in loader:
            optimizer.zero_grad(set_to_none=True);y=y.cuda(non_blocking=True)
            with torch.autocast('cuda',dtype=torch.float16):features=tail(batch.cuda(non_blocking=True))
            logits=head(features.float());ce=torch.nn.functional.cross_entropy(logits,y,weight=weight,label_smoothing=.03,reduction='none')
            pt=logits.softmax(1).gather(1,y[:,None]).squeeze(1).detach()
            loss=(ce*(.75+.25*(1-pt).pow(1.5))).mean()
            scaler.scale(loss).backward();scaler.unscale_(optimizer);torch.nn.utils.clip_grad_norm_(list(tail.parameters())+list(head.parameters()),1)
            scaler.step(optimizer);scaler.update()
        logits=logits_for(tail,head,roles['tune'],labels).numpy();metric=score(labels[roles['tune']],logits.argmax(1))
        history.append({'epoch':epoch,'tune_selection_f1':metric,'seconds':time.monotonic()-start})
        if metric>best:
            best=metric;bad=0
            torch.save({'kind':'continued_focal','tail_state':{k:v.detach().cpu() for k,v in tail.state_dict().items()},
                'head_state':{k:v.detach().cpu() for k,v in head.state_dict().items()},'epoch':epoch,
                'raw_tune_selection_f1':best,'signature':info['signature']},OUT/'continued_focal.pt')
        else:bad+=1
        state={'tail_state':tail.state_dict(),'head_state':head.state_dict(),'optimizer':optimizer.state_dict(),'scaler':scaler.state_dict(),
               'epoch':epoch,'best':best,'bad':bad,'history':history,'signature':info['signature']}
        torch.save(state,OUT/'continued_state.pt');(OUT/'continued_history.json').write_text(json.dumps(history,indent=2)+'\n')
        print(f'Continued epoch {epoch}: tune F1 {metric:.3f}, best {best:.3f}; {time.monotonic()-start:.1f}s',flush=True)


def candidate_logits(name,features,ids,labels):
    if name=='v2':
        head=classifier().cuda();head.load_state_dict(original()['head_state'])
    elif name in HEADS:
        head=Head(name).cuda();head.load_state_dict(torch.load(OUT/(name+'.pt'),weights_only=True,map_location='cpu')['head_state'])
    else:
        artifact=torch.load(OUT/(name+'.pt'),weights_only=True,map_location='cpu');tail=tail_model().cuda();tail.load_state_dict(artifact['tail_state'])
        head=classifier().cuda();head.load_state_dict(artifact['head_state']);return logits_for(tail,head,ids,labels).numpy()
    return head_predict(head,torch.from_numpy(features[ids].copy()).cuda())


def select(info,labels,roles):
    if (OUT/'selection_before_evaluation.json').exists():return json.loads((OUT/'selection_before_evaluation.json').read_text())
    features=cached(info,labels,roles);ids=roles['tune'];truth=labels[ids]
    folds=np.array([int(hashlib.sha256((info['rows'][int(i)]['split']+'/'+info['rows'][int(i)]['sample']).encode()).hexdigest(),16)%2 for i in ids])
    candidates=['v2']+HEADS+(['continued_focal'] if (OUT/'continued_focal.pt').exists() else [])
    results={};predictions={}
    for name in candidates:
        value=candidate_logits(name,features,ids,labels);predictions[name]=value
        results[name]={'uncalibrated':score(truth,value.argmax(1)),'calibrated_oof':cross_calibrate(truth,value,folds)}
    ranked=[(r[mode],name,mode) for name,r in results.items() for mode in ['uncalibrated','calibrated_oof']]
    best,name,mode=max(ranked);baseline=results['v2']['uncalibrated']
    if best<baseline+.005:name='v2';mode='uncalibrated'
    bias=fit_bias(truth,predictions[name]) if mode=='calibrated_oof' else np.zeros(4,np.float32)
    decision={'selected':name,'calibration':mode,'bias':bias.tolist(),'tuning_results':results,
        'signature':info['signature'],'selection_rule':'Best tuning F1, calibrated versions scored out of fold by paired clip group; minimum +0.005 over V2',
        'evaluation_status':'Prior V2 holdout is reused; this is a development comparison, not fresh confirmation.'}
    (OUT/'selection_before_evaluation.json').write_text(json.dumps(decision,indent=2)+'\n');print(json.dumps(decision,indent=2),flush=True)
    return decision


def evaluate(info,labels,roles):
    if (OUT/'evaluation.json').exists():print('V4 evaluation already recorded');return
    decision=select(info,labels,roles);features=cached(info,labels,roles,True);ids=roles['holdout'];truth=torch.as_tensor(labels[ids])
    baseline=candidate_logits('v2',features,ids,labels);chosen=candidate_logits(decision['selected'],features,ids,labels)+np.array(decision['bias'],np.float32)
    results={**decision,'baseline_v2':metrics(truth,torch.from_numpy(baseline).argmax(1)),
        'selected_v4':metrics(truth,torch.from_numpy(chosen).argmax(1)),
        'baseline_fused':fused(info['rows'],ids,truth,torch.from_numpy(baseline)),
        'selected_fused':fused(info['rows'],ids,truth,torch.from_numpy(chosen)),
        'holdout_windows':len(ids),'holdout_clip_groups':len({info['rows'][int(i)]['sample'] for i in ids})}
    (OUT/'evaluation.json').write_text(json.dumps(results,indent=2)+'\n')
    np.savez_compressed(OUT/'comparison_predictions.npz',indices=ids,truth=truth.numpy(),baseline=baseline,selected=chosen)
    print(json.dumps({k:results[k] for k in ['selected','baseline_v2','selected_v4']},indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--phase',required=True,choices=['cache','heads','continue','select','evaluate']);parser.add_argument('--epochs',type=int,default=1);args=parser.parse_args()
    torch.set_num_threads(2);torch.cuda.set_per_process_memory_fraction(.7);info,labels,roles=setup()
    if args.phase=='cache':cached(info,labels,roles)
    elif args.phase=='heads':train_heads(info,labels,roles)
    elif args.phase=='continue':continued(info,labels,roles,args.epochs)
    elif args.phase=='select':select(info,labels,roles)
    else:evaluate(info,labels,roles)
