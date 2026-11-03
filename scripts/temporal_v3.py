"""Multi-label frame and temporal heads with fresh official-test evaluation."""
import argparse
import hashlib
import json
import time
import numpy as np
import torch
from train_pilot import ROOT
from features_v3 import OUT,DATA,CLASSES

MODELS=['mlp_dual','tcn_body','tcn_dual']


def ap(y,p):
    y=np.asarray(y,dtype=np.int64);p=np.asarray(p)
    if not y.sum():return None
    order=np.argsort(-p,kind='stable');truth=y[order];scores=p[order]
    ends=np.r_[np.flatnonzero(np.diff(scores)),len(scores)-1]
    tp=np.cumsum(truth)[ends];precision=tp/(ends+1)
    return float((np.diff(np.r_[0,tp])*precision).sum()/truth.sum())


def frame_metrics(y,p,lengths):
    mask=np.arange(32)[None,:]<lengths[:,None]
    values={name:ap(y[:,i,:][mask],p[:,i,:][mask]) for i,name in enumerate(CLASSES)}
    supported=[v for v in values.values() if v is not None]
    return {'per_class_ap':values,'macro_ap':sum(supported)/len(supported)}


def spans(binary):
    edges=np.diff(np.r_[False,np.asarray(binary,dtype=bool),False].astype(np.int8))
    return list(zip(np.flatnonzero(edges==1).tolist(),np.flatnonzero(edges==-1).tolist()))


def match_events(actual,predicted,iou=.3):
    candidates=[]
    for i,(a,b) in enumerate(actual):
        for j,(c,d) in enumerate(predicted):
            intersection=max(0,min(b,d)-max(a,c));union=max(b,d)-min(a,c)
            overlap=intersection/union
            if overlap>=iou:candidates.append((overlap,i,j))
    used_actual=set();used_pred=set()
    for _,i,j in sorted(candidates,reverse=True):
        if i not in used_actual and j not in used_pred:used_actual.add(i);used_pred.add(j)
    return len(used_actual),len(predicted)-len(used_pred),len(actual)-len(used_actual)


def event_metrics(y,p,lengths,thresholds):
    result={}
    for c,name in enumerate(CLASSES):
        counts=np.zeros(3,dtype=np.int64)
        for i,n in enumerate(lengths):counts+=match_events(spans(y[i,c,:n]),spans(p[i,c,:n]>=thresholds[c]))
        tp,fp,fn=map(int,counts);precision=tp/(tp+fp) if tp+fp else 0.;recall=tp/(tp+fn) if tp+fn else None
        f1=2*tp/(2*tp+fp+fn) if tp+fn else None
        result[name]={'tp':tp,'fp':fp,'fn':fn,'precision':precision,'recall':recall,'f1':f1}
    supported=[v['f1'] for v in result.values() if v['f1'] is not None]
    return {'per_class':result,'macro_f1':sum(supported)/len(supported),'temporal_iou':.3,
            'scope':'Frame-indexed contiguous action spans, not capture-time/spatial localization or false alarms per camera-hour.'}


def calibrate(y,p,lengths):
    thresholds=[]
    for c in range(3):
        best=-1;threshold=.5
        for candidate in np.linspace(.1,.9,17):
            counts=np.zeros(3,dtype=np.int64)
            for i,n in enumerate(lengths):counts+=match_events(spans(y[i,c,:n]),spans(p[i,c,:n]>=candidate))
            tp,fp,fn=counts;score=2*tp/max(2*tp+fp+fn,1)
            if score>best:best=float(score);threshold=float(candidate)
        thresholds.append(threshold)
    return thresholds


class Residual(torch.nn.Module):
    def __init__(self,width,dilation):
        super().__init__();self.norm=torch.nn.LayerNorm(width)
        self.conv=torch.nn.Conv1d(width,width,3,padding=dilation,dilation=dilation)
        self.out=torch.nn.Conv1d(width,width,1);self.dropout=torch.nn.Dropout(.2)
    def forward(self,x):
        z=self.norm(x.transpose(1,2)).transpose(1,2)
        return x+self.out(self.dropout(torch.nn.functional.gelu(self.conv(z))))


def channels(name):return list(range(192))+list(range(384,506)) if name=='tcn_body' else list(range(506))


def model_for(name):
    count=len(channels(name));layers=[torch.nn.Conv1d(count,128,1),torch.nn.GELU()]
    if name.startswith('tcn'):layers += [Residual(128,d) for d in [1,2,4]]
    else:layers += [torch.nn.Dropout(.2),torch.nn.Conv1d(128,128,1),torch.nn.GELU()]
    layers += [torch.nn.Dropout(.2),torch.nn.Conv1d(128,3,1)]
    return torch.nn.Sequential(*layers)


def data(required):
    info=json.loads((OUT/'index.json').read_text());status=np.load(OUT/'status.npy',mmap_mode='r')
    digest=hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest()
    if info['signature']['manifest_sha256']!=digest:raise ValueError('Manifest changed')
    views=info['views']
    if any(status[i]==0 for i,r in enumerate(views) if r['role'] in required):raise RuntimeError('Required features are incomplete')
    roles={role:np.array([i for i,r in enumerate(views) if r['role']==role and status[i]==1]) for role in ['train','tune','test']}
    features=np.load(OUT/'features.npy',mmap_mode='r');labels=np.load(OUT/'labels.npy',mmap_mode='r');lengths=np.load(OUT/'lengths.npy',mmap_mode='r')
    return info,roles,features,labels,lengths


def normalize(x,lengths,mean=None,std=None):
    mask=(torch.arange(32)[None,:]<torch.from_numpy(np.array(lengths,copy=True))[:,None])[:,None,:]
    if mean is None:
        mean=(x*mask).sum((0,2))/mask.sum()
        variance=((x-mean[None,:,None]).square()*mask).sum((0,2))/mask.sum()
        std=variance.sqrt().clamp_min(.05)
    x=((x-mean[None,:,None])/std[None,:,None]).clamp(-10,10)*mask
    return x,mean,std,mask


def infer(model,x):
    model.eval();values=[]
    with torch.inference_mode():
        for part in x.split(128):values.append(model(part).sigmoid().cpu().numpy())
    return np.concatenate(values)


def train(name,epochs_per_call):
    if (OUT/'evaluation.json').exists():raise RuntimeError('Evaluated V3 run is locked; use a fresh experiment')
    info,roles,features,labels,lengths=data(['train','tune'])
    torch.set_num_threads(2);torch.manual_seed(43);torch.cuda.set_per_process_memory_fraction(.7)
    ids=roles['train'];tune=roles['tune'];wanted=channels(name)
    x=torch.from_numpy(features[ids].astype(np.float32));x,mean,std,mask=normalize(x,lengths[ids])
    vx=torch.from_numpy(features[tune].astype(np.float32));vx,_,_,_=normalize(vx,lengths[tune],mean,std)
    x=x[:,wanted].cuda();vx=vx[:,wanted].cuda();mask=mask.cuda();y=torch.from_numpy(labels[ids].copy()).cuda()
    vy=labels[tune].copy();vn=lengths[tune].copy()
    positives=(y*mask).sum((0,2));negatives=mask.sum()-positives
    positive_weights=(negatives/positives.clamp_min(1)).sqrt().clamp(1,15)
    loss_fn=torch.nn.BCEWithLogitsLoss(pos_weight=positive_weights[None,:,None],reduction='none')
    visit_counts=[]
    raw_y=labels[ids].copy()
    for c in range(3):visit_counts.append(len({info['views'][int(i)]['sample_id'] for i,v in zip(ids,raw_y[:,c].any(1)) if v}))
    sample_weights=np.ones(len(ids),dtype=np.float32)
    for c,count in enumerate(visit_counts):sample_weights=np.maximum(sample_weights,np.where(raw_y[:,c].any(1),min(6,(max(visit_counts)/max(count,1))**.5),1))
    sample_weights=torch.from_numpy(sample_weights).cuda()
    model=model_for(name).cuda();optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.02)
    statepath=OUT/(name+'_state.pt');state=torch.load(statepath,weights_only=True,map_location='cpu') if statepath.exists() else None
    epoch=0;bad=0;best=-1;history=[]
    if state:
        if state['signature']!=info['signature']:raise ValueError('Training signature changed')
        model.load_state_dict(state['model_state']);optimizer.load_state_dict(state['optimizer'])
        epoch=state['epoch'];bad=state['bad'];best=state['best'];history=state['history']
    if epoch>=40 or bad>=8:print(f'{name}: complete',flush=True);return
    for _ in range(epochs_per_call):
        if epoch>=40 or bad>=8:break
        epoch+=1;torch.manual_seed(4300+epoch);model.train();started=time.monotonic()
        order=torch.multinomial(sample_weights,len(ids),replacement=True);total=0.
        for batch in order.split(64):
            optimizer.zero_grad(set_to_none=True);logits=model(x[batch])
            loss=(loss_fn(logits,y[batch])*mask[batch]).sum()/(mask[batch].sum()*3)
            loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();total+=float(loss.detach())
        prediction=infer(model,vx);score=frame_metrics(vy,prediction,vn)
        history.append({'epoch':epoch,'loss':total/len(order.split(64)),'tune':score,'seconds':time.monotonic()-started})
        if score['macro_ap']>best:
            best=score['macro_ap'];bad=0
            artifact={'model_name':name,'model_state':{k:v.detach().cpu().clone() for k,v in model.state_dict().items()},
                'mean':mean,'std':std,'channels':wanted,'classes':CLASSES,'signature':info['signature'],
                'epoch':epoch,'tune':score,'sampling':'Visit-aware rare-action oversampling, capped at 6x',
                'positive_weights':positive_weights.cpu(),'protocol':info['protocol']}
            torch.save(artifact,OUT/(name+'.pt'))
        else:bad+=1
        state={'model_state':model.state_dict(),'optimizer':optimizer.state_dict(),'epoch':epoch,'bad':bad,
               'best':best,'history':history,'signature':info['signature']}
        torch.save(state,OUT/(name+'_state.tmp.pt'));(OUT/(name+'_state.tmp.pt')).replace(statepath)
        (OUT/(name+'_history.json')).write_text(json.dumps(history,indent=2)+'\n')
        if epoch%5==0:print(f'{name} epoch {epoch}: tune macro AP {score["macro_ap"]:.3f}, best {best:.3f}',flush=True)
    print(f'{name}: saved epoch {epoch}, best tune AP {best:.3f}',flush=True)


def evaluate():
    if (OUT/'evaluation.json').exists():print('V3 official-test evaluation already recorded');return
    info,roles,features,labels,lengths=data(['train','tune','test']);torch.set_num_threads(2)
    for name in MODELS:
        state=torch.load(OUT/(name+'_state.pt'),weights_only=True,map_location='cpu')
        if state['epoch']<40 and state['bad']<8:raise RuntimeError('Finish all predefined candidates before testing')
    artifacts={name:torch.load(OUT/(name+'.pt'),weights_only=True,map_location='cpu') for name in MODELS}
    selected=max(MODELS,key=lambda name:artifacts[name]['tune']['macro_ap'])
    decision={'selected_by_tuning_only':selected,'tune_macro_ap':{n:a['tune']['macro_ap'] for n,a in artifacts.items()},
              'test_selection':'500 preregistered random official-test visits plus every official-test put/touch visit; natural cohort reported separately.'}
    (OUT/'selection_before_test.json').write_text(json.dumps(decision,indent=2)+'\n')
    ids=roles['test'];actual=labels[ids].copy();n=lengths[ids].copy();results={};predictions={}
    natural=np.array([info['views'][int(i)]['natural_test'] for i in ids]);tune=roles['tune']
    for name,artifact in artifacts.items():
        model=model_for(name).cuda();model.load_state_dict(artifact['model_state'])
        def inputs(indices):
            value=torch.from_numpy(features[indices].astype(np.float32))
            value,_,_,_=normalize(value,lengths[indices],artifact['mean'],artifact['std'])
            return value[:,artifact['channels']].cuda()
        vp=infer(model,inputs(tune));thresholds=calibrate(labels[tune].copy(),vp,lengths[tune].copy())
        prediction=infer(model,inputs(ids));predictions[name]=prediction
        results[name]={'thresholds':thresholds,'all_test_frame':frame_metrics(actual,prediction,n),
                       'all_test_events':event_metrics(actual,prediction,n,thresholds),
                       'natural_test_frame':frame_metrics(actual[natural],prediction[natural],n[natural]),
                       'natural_test_events':event_metrics(actual[natural],prediction[natural],n[natural],thresholds)}
        artifact['thresholds']=thresholds
        if name==selected:torch.save(artifact,OUT/'selected_head.pt')
        del model;torch.cuda.empty_cache()
    support={}
    for role,indices in roles.items():
        support[role]={'visits':len({(info['views'][int(i)]['split'],info['views'][int(i)]['sample_id']) for i in indices}),
                      'views':len(indices),'positive_visits':{label:len({(info['views'][int(i)]['split'],info['views'][int(i)]['sample_id']) for i in indices if labels[i,c].any()}) for c,label in enumerate(CLASSES)}}
    report={**decision,'support':support,'natural_test_visits_usable':len({info['views'][int(i)]['sample_id'] for i in ids[natural]}),
            'results':results,'limitations':['Provided subject poses and public sparse clips; not raw-camera person discovery.',
                'Events use frame-index IoU, not real-time seconds or spatial action localization.',
                'Rare official-test support remains small; no operational false-alert-rate claim.',
                'V2 validation is now tuning data; official test is fresh for this experiment.']}
    (OUT/'evaluation.json').write_text(json.dumps(report,indent=2)+'\n')
    np.savez_compressed(OUT/'test_predictions.npz',indices=ids,truth=actual,lengths=n,natural=natural,**predictions)
    print(json.dumps({'selected':selected,'support':support,'scores':{name:{'frame_ap':r['all_test_frame']['macro_ap'],'event_f1':r['all_test_events']['macro_f1']} for name,r in results.items()}},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',choices=MODELS);p.add_argument('--epochs-per-call',type=int,default=10);p.add_argument('--evaluate',action='store_true');args=p.parse_args()
    if args.evaluate:evaluate()
    elif args.model:train(args.model,args.epochs_per_call)
    else:p.error('Choose --model or --evaluate')
