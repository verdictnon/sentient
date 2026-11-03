"""Cache frozen early-stage activations once, enabling cheap last-stage fine-tuning."""
import argparse
from collections import Counter,deque
import concurrent.futures
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import numpy as np
import torch
from PIL import Image
from train_pilot import ROOT,CLASSES
sys.path.insert(0,str(ROOT/'vendor/pytorchvideo'))
from pytorchvideo.models.hub.x3d import x3d_s
import av

DATA=ROOT/'data/retailaction_v2';OUT=ROOT/'runs/x3d_s_v2'


def frame_windows(sample):
    n=int(sample['metadata']['total_frame_count'])
    if n<4:return []
    events=(sample.get('ground_truth') or {}).get('detections',[])
    events=[e for e in events if e['label'] in CLASSES[:3]]
    def interval(center):
        left=max(0,min(int(round(center))-6,n-13))
        return left,min(n-1,left+12)
    output=[]
    for number,event in enumerate(events):
        first,last=[int(v)-1 for v in event['support']]
        left,right=interval((first+last)/2)
        if any(e['label']!=event['label'] and int(e['support'][0])-1<=right and int(e['support'][1])-1>=left for e in events):continue
        output.append({'label':event['label'],'left':left,'right':right,'event':f'event{number}'})
    centers=[(n-1)/2] if not events else [6,n-7]
    for center in centers:
        left,right=interval(center)
        if all(right<int(e['support'][0])-2 or left>int(e['support'][1]) for e in events):
            output.append({'label':'background','left':left,'right':right,'event':'background'});break
    return output


def index():
    OUT.mkdir(parents=True,exist_ok=True)
    source=DATA/'manifest.json';manifest=json.loads(source.read_text())
    signature={'version':2,'manifest_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
               'backbone_sha256':hashlib.sha256((ROOT/'models/X3D_S.pyth').read_bytes()).hexdigest()}
    path=OUT/'index.json'
    if path.exists():
        saved=json.loads(path.read_text())
        if saved['signature']!=signature:raise ValueError('Cache signature mismatch')
        return saved
    rows=[];views=[]
    for sample in sorted(manifest['views'],key=lambda r:(r['split'],r['sample_id'],r['group']['name'])):
        view=sample['group']['name'];items=[]
        for window in frame_windows(sample):
            items.append(len(rows));rows.append({'split':sample['split'],'sample':sample['sample_id'],
                'role':sample['role'],'view':view,**window})
        if items:views.append({'split':sample['split'],'sample':sample['sample_id'],'view':view,
                              'nframes':sample['metadata']['total_frame_count'],'indices':items})
    saved={'signature':signature,'protocol':'Frame-indexed converted annotations, 13-frame subject-pose crops. Not capture-time event localization.',
           'rows':rows,'views':views,'prefix_shape':[96,13,10,10]}
    path.write_text(json.dumps(saved,indent=2)+'\n')
    print('Planned windows '+json.dumps(dict(Counter((r['role']+'/'+r['label']) for r in rows))),flush=True)
    return saved


def prepare(view,rows):
    folder=DATA/view['split']/view['sample']
    pose=json.loads((folder/f"{view['view']}_poses.json").read_text())['frames']
    with av.open(str(folder/f"{view['view']}_video.mp4")) as container:
        container.streams.video[0].thread_count=1
        frames=[frame.to_ndarray(format='rgb24') for frame in container.decode(video=0)]
    if len(frames)!=int(view['nframes']):raise ValueError('frame_count_mismatch')
    result=[]
    for slot in view['indices']:
        row=rows[slot];chosen=np.linspace(row['left'],row['right'],13).round().astype(int)
        points=[]
        for frame_index in set(chosen):
            p=pose.get(str(frame_index+1),{})
            for xy,confidence in zip(p.get('points',[]),p.get('confidence',[])):
                if isinstance(confidence,(int,float)) and confidence>=.2 and len(xy)==2 and all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=1 for v in xy):points.append(xy)
        if len(points)<4:result.append((slot,None,'insufficient_pose'));continue
        points=np.asarray(points);height,width=frames[0].shape[:2]
        lo=points.min(0)*[width,height];hi=points.max(0)*[width,height]
        side=max(float((hi-lo).max()),min(width,height)*.25)*1.4;cx,cy=(lo+hi)/2
        crop=(round(cx-side/2),round(cy-side/2),round(cx+side/2),round(cy+side/2))
        images={i:np.asarray(Image.fromarray(frames[i]).crop(crop).resize((160,160),Image.Resampling.BILINEAR)).copy() for i in set(chosen)}
        array=np.stack([images[i] for i in chosen]).transpose(3,0,1,2)
        tensor=torch.from_numpy(array).float().div_(255).sub_(.45).div_(.225)
        result.append((slot,tensor,None))
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--max-views',type=int,default=256);args=parser.parse_args()
    saved=index();rows=saved['rows'];n=len(rows)
    def mmap(name,dtype,shape):
        path=OUT/name
        return np.lib.format.open_memmap(path,mode='r+' if path.exists() else 'w+',dtype=dtype,shape=shape)
    rebuild_prefix=not (OUT/'early.npy').exists()
    status=mmap('status.npy',np.int8,(n,))
    if rebuild_prefix:
        status[status==1]=0;status.flush()
        (OUT/'CACHE_READY').unlink(missing_ok=True)
    early=mmap('early.npy',np.float16,(n,*saved['prefix_shape']))
    pooled=mmap('pooled.npy',np.float32,(n,2048))
    available=[]
    missing=[]
    for view in saved['views']:
        pending=[i for i in view['indices'] if status[i]==0]
        directory=DATA/view['split']/view['sample']
        if pending and all((directory/name).exists() for name in [f"{view['view']}_video.mp4",f"{view['view']}_poses.json"]):
            available.append({**view,'indices':pending})
        elif pending and (DATA/'pose_inventory_complete.json').exists() and not (directory/f"{view['view']}_poses.json").exists():
            status[pending]=-1
            missing.extend({'slot':slot,'reason':'missing_pose_export'} for slot in pending)
    if missing:
        status.flush()
        with (OUT/'cache_errors.jsonl').open('a') as stream:
            for error in missing:stream.write(json.dumps(error)+'\n')
    if args.max_views:available=available[:args.max_views]
    print(f'Cache valid {int((status==1).sum())}/{n}; ready views in this chunk {len(available)}',flush=True)
    if not available:
        if np.all(status!=0) and (DATA/'READY').exists():(OUT/'CACHE_READY').write_text('Complete\n')
        return
    torch.set_num_threads(2);torch.cuda.set_per_process_memory_fraction(.65)
    model=x3d_s(pretrained=False);model.load_state_dict(torch.load(ROOT/'models/X3D_S.pyth',weights_only=True,map_location='cpu')['model_state'])
    model.blocks[-1].proj=torch.nn.Identity();model.blocks[-1].activation=torch.nn.Identity()
    model=model.eval().requires_grad_(False).cuda()
    prefix=torch.nn.Sequential(*list(model.blocks)[:4]);tail=torch.nn.Sequential(*list(model.blocks)[4:])
    queue=[];errors=[];started=time.monotonic()
    def flush():
        if not queue:return
        ids=[x[0] for x in queue];batch=torch.stack([x[1] for x in queue]).cuda()
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):
            activation=prefix(batch);features=tail(activation)
        if list(activation.shape[1:])!=saved['prefix_shape']:raise ValueError('Unexpected activation shape')
        if not torch.isfinite(features).all():raise ValueError('Non-finite features')
        early[ids]=activation.cpu().numpy();pooled[ids]=features.float().cpu().numpy()
        status[ids]=1;queue.clear()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        pending=deque();iterator=iter(available)
        for _ in range(min(8,len(available))):
            view=next(iterator);pending.append((view,executor.submit(prepare,view,rows)))
        completed=0
        while pending:
            view,future=pending.popleft()
            try:
                for slot,tensor,error in future.result():
                    if error:status[slot]=-1;errors.append({'slot':slot,'reason':error})
                    else:
                        queue.append((slot,tensor))
                        if len(queue)>=8:flush()
            except json.JSONDecodeError:
                pass # A concurrently written pose file will be retried.
            except Exception as error:
                if not (DATA/'READY').exists():raise
                for slot in view['indices']:status[slot]=-1;errors.append({'slot':slot,'reason':str(error)})
            completed+=1
            if completed%32==0:
                flush();early.flush();pooled.flush();status.flush()
                print(f'cached views {completed}/{len(available)}, valid windows {int((status==1).sum())}, {time.monotonic()-started:.1f}s',flush=True)
            view=next(iterator,None)
            if view is not None:pending.append((view,executor.submit(prepare,view,rows)))
    flush();early.flush();pooled.flush();status.flush()
    if errors:
        with (OUT/'cache_errors.jsonl').open('a') as stream:
            for error in errors:stream.write(json.dumps(error)+'\n')
    summary={'valid':int((status==1).sum()),'skipped':int((status==-1).sum()),'pending':int((status==0).sum())}
    (OUT/'cache_progress.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)
    if not summary['pending'] and (DATA/'READY').exists():(OUT/'CACHE_READY').write_text('Complete\n')


if __name__=='__main__':main()
