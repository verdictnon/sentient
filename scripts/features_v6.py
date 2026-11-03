"""Bounded V6 pilot: frozen frame encoder, fixed body and separate hand ROIs."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import subprocess
import sys
import time
import av
import numpy as np
from PIL import Image,ImageDraw
import torch
from features_v3 import ROOT,DATA,clean_pose

TRAIN_COUNT=int(os.environ.get('SENTIENT_V6_TRAIN_GROUPS','256'))
if TRAIN_COUNT not in [256,512]:raise ValueError('Only the bounded 256/512-group V6 runs are supported')
OUT=ROOT/('runs/hand_detail_v6' if TRAIN_COUNT==256 else 'runs/hand_detail_v6_scale512')
SOURCE=ROOT/'runs/temporal_v3';WEIGHTS=ROOT/'models/dinov2_vits14_pretrain.pth'
sys.path.insert(0,str(ROOT/'vendor/dinov2'))


def encoder():
    from dinov2.hub.backbones import dinov2_vits14
    model=dinov2_vits14(pretrained=False)
    model.load_state_dict(torch.load(WEIGHTS,weights_only=True,map_location='cpu'))
    return model.eval().requires_grad_(False)


def plan():
    OUT.mkdir(parents=True,exist_ok=True);target=OUT/'index.json'
    source=json.loads((SOURCE/'index.json').read_text());status=np.load(SOURCE/'status.npy',mmap_mode='r')
    labels=np.load(SOURCE/'labels.npy',mmap_mode='r');groups=defaultdict(list)
    for i,row in enumerate(source['views']):
        if row['role'] in ['train','tune'] and status[i]==1:groups[(row['split'],row['sample_id'])].append(i)
    def ordered(keys,salt):return sorted(keys,key=lambda k:hashlib.sha256((salt+'/'.join(k)).encode()).hexdigest())
    selected={};strata={}
    for role,target_count,put_count,bg_count in [('train',TRAIN_COUNT,96 if TRAIN_COUNT==256 else 224,48 if TRAIN_COUNT==256 else 112),('tune',128,40,24)]:
        keys=[k for k,v in groups.items() if source['views'][v[0]]['role']==role]
        positive={k:labels[groups[k]].any((0,2)) for k in keys};chosen=[]
        choices=[('touch',[k for k in keys if positive[k][2]],999),('put',[k for k in keys if positive[k][1]],put_count),
                 ('background',[k for k in keys if not positive[k].any()],bg_count),('common',keys,target_count)]
        for category,options,limit in choices:
            count=0
            for key in ordered(options,'v6-'+role+'-'+category):
                if key in chosen:continue
                if count>=limit or len(chosen)>=target_count:break
                chosen.append(key);strata[key]=category;count+=1
        if role=='train':selected.update({k:'train' for k in chosen})
        else:
            for category in ['touch','put','background','common']:
                values=ordered([k for k in chosen if strata[k]==category],'v6-development-halves')
                selected.update({k:'selection' if i%2==0 else 'check' for i,k in enumerate(values)})
    views=[]
    for key,role in selected.items():
        for slot in groups[key]:views.append({**source['views'][slot],'source_slot':int(slot),'pilot_role':role,'pilot_stratum':strata[key]})
    views.sort(key=lambda r:(r['pilot_role'],r['split'],r['sample_id'],r['group']['name']))
    signature={'version':6,'source_signature':source['signature'],'dinov2_sha256':hashlib.sha256(WEIGHTS.read_bytes()).hexdigest(),
        'dinov2_git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT/'vendor/dinov2',text=True).strip(),
        'crop_version':'fixed-body-two-hand-q05-q95-margin-max40px-0.8forearm-min96px-v1','image_size':224}
    if TRAIN_COUNT==512:signature['sample_roles_sha256']=hashlib.sha256(json.dumps([(v['source_slot'],v['pilot_role']) for v in views]).encode()).hexdigest()
    tiny=[];used=set()
    for c,name in [(2,'touch'),(1,'put'),(0,'take'),(None,'background')]:
        count=0
        for slot,row in enumerate(views):
            key=(row['split'],row['sample_id']);target_labels=labels[row['source_slot']]
            good=(not target_labels.any()) if c is None else bool(target_labels[c].any())
            if c==0:good=good and not target_labels[1:].any()
            if row['pilot_role']=='train' and key not in used and good:
                tiny.append({'slot':slot,'category':name});used.add(key);count+=1
                if count==4:break
        if count!=4:raise ValueError('Insufficient diagnostic examples')
    result={'signature':signature,'views':views,'tiny_examples':tiny,'feature_layout':{'body_dino':[0,384],'left_dino':[384,768],'right_dino':[768,1152],'hand_validity':[1152,1154]},
        'protocol':{'training_clip_groups':TRAIN_COUNT,'development_clip_groups':128,'selection':'frame macro AP, same matched pilot examples across heads',
            'heads':['context','body','detail'],'seeds':[43,44],'max_epochs':30,'patience':6,
            'expansion_gate':'Detail mean selection macro AP >= body +0.02, put/touch mean AP does not decline, and development-check AP difference is positive.',
            'scope':'Development pilot only; selection/check groups were previously V3 tuning. No official-test evaluation.',
            'time':'Original nonuniform sampling retained in frame order; no invented physical frame intervals.',
            'context':'Original Kinetics frozen V3 body+pose features; no V2/V5 supervised features or neighbor bank.'}}
    if target.exists():
        saved=json.loads(target.read_text())
        if saved==result:return saved
        if (OUT/'detail_features.npy').exists():raise ValueError('Pilot inputs/config changed; use a fresh experiment')
    target.write_text(json.dumps(result,indent=2)+'\n')
    print('Pilot planned: '+json.dumps({r:len({(v['split'],v['sample_id']) for v in views if v['pilot_role']==r}) for r in ['train','selection','check']}),flush=True)
    return result


def fixed_boxes(cleaned,width,height):
    scale=np.array([width,height]);points=np.concatenate([p[c>=.2] for p,c in cleaned],axis=0)
    if len(points)<4:raise ValueError('Insufficient subject poses')
    lo=points.min(0)*scale;hi=points.max(0)*scale;center=(lo+hi)/2
    side=max(float((hi-lo).max()),min(width,height)*.25)*1.4
    def box(center,side):return tuple(int(round(v)) for v in [center[0]-side/2,center[1]-side/2,center[0]+side/2,center[1]+side/2])
    body=box(center,side);boxes=[body];valid=[]
    for elbow,wrist,hand in [(8,9,10),(4,5,6)]:
        positions=[];forearms=[]
        for p,c in cleaned:
            for joint in [wrist,hand]:
                if c[joint]>=.2:positions.append(p[joint]*scale)
            if min(c[elbow],c[wrist])>=.2:forearms.append(float(np.linalg.norm((p[elbow]-p[wrist])*scale)))
        if len(positions)<4:boxes.append(body);valid.append(0.);continue
        positions=np.array(positions);lo,hi=np.quantile(positions,[.05,.95],axis=0)
        margin=max(min(width,height)/15,.8*float(np.median(forearms)) if forearms else 0)
        side=max(min(width,height)*.16,float((hi-lo).max())+2*margin)
        boxes.append(box((lo+hi)/2,side));valid.append(1.)
    return boxes,valid


def raw(sample):
    folder=DATA/sample['split']/sample['sample_id'];view=sample['group']['name']
    poses=json.loads((folder/(view+'_poses.json')).read_text())['frames']
    with av.open(str(folder/(view+'_video.mp4'))) as video:
        video.streams.video[0].thread_count=1;frames=[frame.to_ndarray(format='rgb24') for frame in video.decode(video=0)]
    n=len(frames)
    if not 4<=n<=32 or n!=int(sample['metadata']['total_frame_count']):raise ValueError('Invalid frame count')
    cleaned=[clean_pose(poses.get(str(i+1),{})) for i in range(n)];height,width=frames[0].shape[:2]
    boxes,valid=fixed_boxes(cleaned,width,height)
    return frames,boxes,valid


def prepare(sample):
    frames,boxes,valid=raw(sample)
    regions=np.stack([np.stack([np.asarray(Image.fromarray(frame).crop(box).resize((224,224),Image.Resampling.BILINEAR)) for frame in frames]) for box in boxes])
    return regions,valid,len(frames),boxes


def inspect_samples(saved):
    source_labels=np.load(SOURCE/'labels.npy',mmap_mode='r');chosen=[]
    for c,name in [(0,'take'),(1,'put'),(2,'touch'),(None,'background')]:
        for row in saved['views']:
            if row['pilot_role']!='train':continue
            target=source_labels[row['source_slot']]
            if (not target.any()) if c is None else (target[c].any()):chosen.append((row,name,c));break
    sheet=Image.new('RGB',(1120,4*250),'white');draw=ImageDraw.Draw(sheet);metadata=[]
    for r,(row,name,c) in enumerate(chosen):
        frames,boxes,valid=raw(row);n=len(frames);target=source_labels[row['source_slot']]
        positive=np.flatnonzero(target[c,:n]) if c is not None else np.array([n//2]);center=int(positive[len(positive)//2])
        indices=[max(0,int(positive[0])-3),center,min(n-1,int(positive[-1])+3)]
        draw.text((5,r*250+3),f'{name}: {row["split"]}/{row["sample_id"]}/{row["group"]["name"]}; before / action / after / left / right',fill='black')
        for j,index in enumerate(indices):sheet.paste(Image.fromarray(frames[index]).resize((224,224)),(j*224,r*250+23))
        for j,box in enumerate(boxes[1:]):sheet.paste(Image.fromarray(frames[center]).crop(box).resize((224,224)),((j+3)*224,r*250+23))
        metadata.append({'class':name,'source_slot':row['source_slot'],'frames':indices,'boxes':boxes,'hand_validity':valid})
    sheet.save(OUT/'training_examples.png');(OUT/'training_examples.json').write_text(json.dumps(metadata,indent=2)+'\n')


def cache(max_views):
    saved=plan();views=saved['views'];n=len(views)
    def mmap(name,dtype,shape):
        path=OUT/name;return np.lib.format.open_memmap(path,mode='r+' if path.exists() else 'w+',dtype=dtype,shape=shape)
    array=mmap('detail_features.npy',np.float16,(n,1154,32));status=mmap('status.npy',np.int8,(n,));lengths=mmap('lengths.npy',np.int16,(n,))
    if TRAIN_COUNT==512 and not (OUT/'parent_cache_reused.json').exists():
        parent=ROOT/'runs/hand_detail_v6';old=json.loads((parent/'index.json').read_text())
        if any(saved['signature'].get(k)!=v for k,v in old['signature'].items()):raise ValueError('Parent feature provenance differs')
        source_status=np.load(parent/'status.npy',mmap_mode='r');source_values=np.load(parent/'detail_features.npy',mmap_mode='r');source_lengths=np.load(parent/'lengths.npy',mmap_mode='r')
        lookup={v['source_slot']:i for i,v in enumerate(old['views']) if source_status[i]==1};copied=0
        for i,row in enumerate(views):
            if row['source_slot'] in lookup and status[i]==0:
                previous=lookup[row['source_slot']];array[i]=source_values[previous];lengths[i]=source_lengths[previous];status[i]=1;copied+=1
        array.flush();lengths.flush();status.flush();(OUT/'parent_cache_reused.json').write_text(json.dumps({'views_reused':copied,'source':str(parent)},indent=2))
        print(f'Reused {copied} existing feature views',flush=True)
    tiny={r['slot'] for r in saved['tiny_examples']}
    slots=np.array(sorted(np.flatnonzero(status==0).tolist(),key=lambda i:(i not in tiny,i)))[:max_views]
    torch.set_num_threads(2);torch.cuda.set_per_process_memory_fraction(.7)
    model=encoder().cuda();mean=torch.tensor([.485,.456,.406],device='cuda')[None,:,None,None];std=torch.tensor([.229,.224,.225],device='cuda')[None,:,None,None]
    started=time.monotonic();errors=[];timings=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        # Keep only two large decoded views in flight.
        pending=[];iterator=iter(slots.tolist())
        for _ in range(min(2,len(slots))):
            slot=next(iterator);pending.append((slot,pool.submit(prepare,views[slot])))
        completed=0
        while pending:
            slot,future=pending.pop(0)
            try:regions,valid,length,boxes=future.result()
            except Exception as error:status[slot]=-1;errors.append({'slot':slot,'error':str(error)})
            else:
                images=torch.from_numpy(regions.reshape(-1,224,224,3).transpose(0,3,1,2)).cuda();encoded=[]
                with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):
                    for batch in images.split(32):encoded.append(model((batch.float()/255-mean)/std).float().cpu().numpy())
                value=np.concatenate(encoded).reshape(3,length,384).transpose(0,2,1).reshape(1152,length)
                value[384:768]*=valid[0];value[768:1152]*=valid[1]
                if not np.isfinite(value).all():raise ValueError('Nonfinite image features')
                array[slot,:,:]=0;array[slot,:1152,:length]=value;array[slot,1152:,:length]=np.array(valid)[:,None]
                lengths[slot]=length;status[slot]=1
            completed+=1
            if completed%16==0:
                array.flush();status.flush();lengths.flush();print(f'V6 cached {int((status==1).sum())}/{n}, chunk {completed}/{len(slots)}, {time.monotonic()-started:.1f}s',flush=True)
            next_slot=next(iterator,None)
            if next_slot is not None:pending.append((next_slot,pool.submit(prepare,views[next_slot])))
    array.flush();status.flush();lengths.flush()
    if errors:
        with (OUT/'errors.jsonl').open('a') as stream:
            for error in errors:stream.write(json.dumps(error)+'\n')
    summary={'valid':int((status==1).sum()),'failed':int((status==-1).sum()),'pending':int((status==0).sum()),'chunk_seconds':time.monotonic()-started}
    (OUT/'cache_progress.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary),flush=True)
    if not summary['pending']:(OUT/'CACHE_READY').write_text('Complete\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--max-views',type=int,default=64);parser.add_argument('--inspect',action='store_true');args=parser.parse_args()
    if args.inspect:inspect_samples(plan())
    else:cache(args.max_views)
