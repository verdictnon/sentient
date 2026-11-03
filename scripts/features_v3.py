"""Whole-clip frame features: pretrained RGB body/hands plus provided body pose."""
import argparse
from collections import deque
import concurrent.futures
import hashlib
import json
import math
import time
import numpy as np
import torch
from PIL import Image
import av
from train_pilot import ROOT
from pytorchvideo.models.hub.x3d import x3d_s

DATA=ROOT/'data/retailaction_v3';OUT=ROOT/'runs/temporal_v3'
CLASSES=['take','put','touch'];CHANNELS=506;MAX_FRAMES=32


def index():
    OUT.mkdir(parents=True,exist_ok=True);path=DATA/'manifest.json';manifest=json.loads(path.read_text())
    signature={'version':3,'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
               'backbone_sha256':hashlib.sha256((ROOT/'models/X3D_S.pyth').read_bytes()).hexdigest()}
    target=OUT/'index.json'
    if target.exists():
        saved=json.loads(target.read_text())
        if saved['signature']!=signature:raise ValueError('Cache signature differs')
        return saved
    saved={'signature':signature,'classes':CLASSES,'channels':CHANNELS,
           'layout':{'body':[0,192],'hands':[192,384],'pose':[384,504],'metadata':[504,506]},
           'protocol':'Whole sparse clips; frame-indexed multi-label targets. Original Kinetics res5 frozen. Supplied subject poses; no annotation-centered windows.',
           'views':sorted(manifest['views'],key=lambda r:(r['split'],r['sample_id'],r['group']['name']))}
    target.write_text(json.dumps(saved,indent=2)+'\n');return saved


def labels_for(sample,n):
    labels=np.zeros((3,MAX_FRAMES),np.float32)
    for event in (sample.get('ground_truth') or {}).get('detections',[]):
        if event['label'] not in CLASSES:continue
        first,last=map(int,event['support'])
        if not (1<=first<=last<=n):raise ValueError('annotation_outside_clip')
        labels[CLASSES.index(event['label']),first-1:last]=1
    return labels


def clean_pose(record):
    points=np.zeros((24,2),np.float32);confidence=np.zeros(24,np.float32)
    for i,(xy,score) in enumerate(zip(record.get('points',[]),record.get('confidence',[]))):
        if i>=24:break
        if isinstance(score,(float,int)) and len(xy)==2 and all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=1 for v in xy):
            points[i]=xy;confidence[i]=np.clip(score,0,1)
    return points,confidence


def crop_box(points,confidence,width,height,hands=False):
    valid=confidence>=.2
    if hands:
        chosen=np.zeros(24,dtype=bool);chosen[[4,5,6,8,9,10]]=True;valid &= chosen
    if valid.sum()<2:return None
    lo=points[valid].min(0)*[width,height];hi=points[valid].max(0)*[width,height]
    side=max(float((hi-lo).max()),min(width,height)*(.30 if hands else .25))*(1.6 if hands else 1.4)
    cx,cy=(lo+hi)/2
    return tuple(map(round,(cx-side/2,cy-side/2,cx+side/2,cy+side/2)))


def prepare(sample):
    directory=DATA/sample['split']/sample['sample_id'];view=sample['group']['name']
    poses=json.loads((directory/f'{view}_poses.json').read_text())['frames']
    with av.open(str(directory/f'{view}_video.mp4')) as container:
        container.streams.video[0].thread_count=1
        frames=[f.to_ndarray(format='rgb24') for f in container.decode(video=0)]
    n=len(frames)
    if not 4<=n<=MAX_FRAMES or n!=int(sample['metadata']['total_frame_count']):raise ValueError('invalid_frame_count')
    cleaned=[clean_pose(poses.get(str(i+1),{})) for i in range(n)]
    height,width=frames[0].shape[:2]
    body=[crop_box(p,c,width,height) for p,c in cleaned]
    valid=[i for i,box in enumerate(body) if box is not None]
    if not valid:raise ValueError('no_usable_subject_pose')
    body_images=[];hand_images=[];pose_features=np.zeros((122,MAX_FRAMES),np.float32)
    for i,(p,c) in enumerate(cleaned):
        box=body[i] or body[min(valid,key=lambda j:abs(j-i))]
        hand=crop_box(p,c,width,height,True) or box
        image=Image.fromarray(frames[i])
        body_images.append(np.asarray(image.crop(box).resize((160,160),Image.Resampling.BILINEAR)).copy())
        hand_images.append(np.asarray(image.crop(hand).resize((160,160),Image.Resampling.BILINEAR)).copy())
        mask=c>=.2;center=p[mask].mean(0) if mask.any() else np.zeros(2)
        extent=max(float(np.ptp(p[mask],axis=0).max()),.15) if mask.any() else 1
        relative=np.clip((p-center)/extent,-2,2);relative[c==0]=0
        pose_features[:120,i]=np.concatenate([p,relative,c[:,None]],axis=1).ravel()
        pose_features[120,i]=i/max(n-1,1)
        pose_features[121,i]=min(float(sample['segment_duration_s']),60)/60
    def tensor(images):
        images+= [images[-1]]*(MAX_FRAMES-n)
        return torch.from_numpy(np.stack(images).transpose(3,0,1,2)).float().div_(255).sub_(.45).div_(.225)
    return tensor(body_images),tensor(hand_images),pose_features,labels_for(sample,n),n


def main():
    p=argparse.ArgumentParser();p.add_argument('--max-views',type=int,default=256);args=p.parse_args()
    saved=index();views=saved['views'];n=len(views)
    def mmap(name,dtype,shape):
        path=OUT/name;return np.lib.format.open_memmap(path,mode='r+' if path.exists() else 'w+',dtype=dtype,shape=shape)
    features=mmap('features.npy',np.float16,(n,CHANNELS,MAX_FRAMES))
    labels=mmap('labels.npy',np.float32,(n,3,MAX_FRAMES));lengths=mmap('lengths.npy',np.int16,(n,));status=mmap('status.npy',np.int8,(n,))
    available=[];missing=[]
    for i,sample in enumerate(views):
        if status[i]!=0:continue
        directory=DATA/sample['split']/sample['sample_id'];view=sample['group']['name']
        if all((directory/name).exists() for name in [view+'_video.mp4',view+'_poses.json']):available.append(i)
        elif (DATA/'READY').exists():status[i]=-1;missing.append({'slot':i,'reason':'missing_media_or_pose'})
    available=available[:args.max_views] if args.max_views else available
    print(f'V3 features {int((status==1).sum())}/{n}; ready in chunk {len(available)}',flush=True)
    errors=missing
    if available:
        torch.set_num_threads(2);torch.cuda.set_per_process_memory_fraction(.7)
        model=x3d_s(pretrained=False);model.load_state_dict(torch.load(ROOT/'models/X3D_S.pyth',weights_only=True,map_location='cpu')['model_state'])
        model=torch.nn.Sequential(*list(model.blocks)[:5]).eval().requires_grad_(False).cuda()
        queue=[];started=time.monotonic()
        def flush():
            if not queue:return
            inputs=torch.stack([value for _,values in queue for value in values[:2]]).cuda()
            with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):
                encoded=model(inputs).mean((-1,-2)).cpu().numpy()
            for j,(slot,values) in enumerate(queue):
                _,_,pose,target,length=values
                array=np.concatenate([encoded[j*2],encoded[j*2+1],pose],axis=0)
                array[:,length:]=0
                if not np.isfinite(array).all():raise ValueError('Non-finite features')
                features[slot]=array;labels[slot]=target;lengths[slot]=length;status[slot]=1
            queue.clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending=deque();iterator=iter(available)
            for _ in range(min(8,len(available))):
                slot=next(iterator);pending.append((slot,pool.submit(prepare,views[slot])))
            completed=0
            while pending:
                slot,future=pending.popleft()
                try:
                    values=future.result()
                except json.JSONDecodeError:pass
                except Exception as error:
                    if not (DATA/'READY').exists() and isinstance(error,FileNotFoundError):pass
                    else:status[slot]=-1;errors.append({'slot':slot,'reason':str(error)})
                else:
                    queue.append((slot,values))
                    if len(queue)>=4:flush()
                completed+=1
                if completed%64==0:
                    flush();features.flush();labels.flush();lengths.flush();status.flush()
                    print(f'V3 cached {completed}/{len(available)} views, {time.monotonic()-started:.1f}s',flush=True)
                slot=next(iterator,None)
                if slot is not None:pending.append((slot,pool.submit(prepare,views[slot])))
        flush()
    features.flush();labels.flush();lengths.flush();status.flush()
    if errors:
        with (OUT/'errors.jsonl').open('a') as stream:
            for error in errors:stream.write(json.dumps(error)+'\n')
    result={'valid':int((status==1).sum()),'skipped':int((status==-1).sum()),'pending':int((status==0).sum())}
    (OUT/'progress.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    if not result['pending'] and (DATA/'READY').exists():(OUT/'CACHE_READY').write_text('Complete\n')


if __name__=='__main__':main()
