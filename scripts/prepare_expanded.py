"""Select all rare actions, more common data, and a preregistered fresh holdout."""
import concurrent.futures
from collections import Counter, defaultdict
import hashlib
import io
import json
import os
from pathlib import Path
import random
import time
import threading
import urllib.error
import urllib.request
import ijson

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/retailaction_v2'
REV='24fd9c4092a7a2a677542d61b41fc2dd644ec544'
BASE=f'https://huggingface.co/datasets/harpreetsahota/RetailAction/resolve/{REV}'
REQUEST_LOCK=threading.Lock()
NEXT_REQUEST=0.0
PAUSE_UNTIL=0.0


def request(url):
    global NEXT_REQUEST
    with REQUEST_LOCK:
        now=time.monotonic();scheduled=max(now,NEXT_REQUEST,PAUSE_UNTIL);NEXT_REQUEST=scheduled+.35
    if scheduled>now:time.sleep(scheduled-now)
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Sentient-research/0.2'}),timeout=60)


def select():
    existing=OUT/'manifest.json'
    if existing.exists():
        saved=json.loads(existing.read_text())
        if saved['revision']!=REV:raise ValueError('Existing dataset revision differs')
        print('Resuming fixed dataset plan: '+json.dumps(saved['role_groups']),flush=True)
        return saved['views']
    rows=json.loads((ROOT/'data/retailaction_inventory/mapped_samples.json').read_text())
    groups=defaultdict(list)
    for row in rows:groups[(row['split'],row['sample_id'])].append(row)
    oldtrain=set(json.loads((ROOT/'data/retailaction/train/manifest.json').read_text())['samples'])
    oldval=set(json.loads((ROOT/'data/retailaction/validation/manifest.json').read_text())['samples'])
    chosen={('train',sid) for sid in oldtrain}
    for key,views in groups.items():
        if key[0]=='train' and set(views[0]['action_classes']) & {'put','touch'}:chosen.add(key)
    rng=random.Random(20261029)
    for category,limit in [('take',1200),('background',200)]:
        candidates=[key for key,v in groups.items() if key[0]=='train' and key not in chosen
                    and (v[0]['action_classes']==['take'] if category=='take' else not v[0]['action_classes'])]
        rng.shuffle(candidates);chosen.update(candidates[:limit])
    roles={key:'train' for key in chosen}
    strata=defaultdict(list)
    for key,views in groups.items():
        if key[0]!='validation':continue
        if key[1] in oldval:roles[key]='tune';continue
        labels=set(views[0]['action_classes'])
        primary=next((label for label in ['touch','put','take'] if label in labels),'background')
        strata[primary].append(key)
    for primary,keys in strata.items():
        keys.sort(key=lambda k:hashlib.sha256(('fresh-holdout-v2/'+k[1]).encode()).hexdigest())
        holdout=(len(keys)+1)//2
        for i,key in enumerate(keys):roles[key]='holdout' if i<holdout else 'tune'
    selected=[];oldhash={}
    for split in ['train','validation']:
        manifest=json.loads((ROOT/f'data/retailaction/{split}/manifest.json').read_text())
        for sid,files in manifest['samples'].items():
            for view in ['rank0','rank1']:oldhash[(split,sid,view)]=files[f'{view}_video.mp4']['sha256']
    verified=0
    for key,role in roles.items():
        for row in groups[key]:
            row=dict(row);row['role']=role
            existing=oldhash.get((row['split'],row['sample_id'],row['group']['name']))
            if existing:
                if existing!=row['remote_sha256']:raise ValueError('Mirror bytes differ from original pilot')
                verified+=1
            if not row.get('remote_sha256'):raise ValueError('Missing upstream SHA-256')
            selected.append(row)
    OUT.mkdir(parents=True,exist_ok=True)
    # This plan is fixed before fitting or inspecting holdout model predictions.
    manifest={'revision':REV,'seed':20261029,'selection':'All training put/touch groups + old pilot + 1200 extra take + 200 extra background groups; all original validation groups.',
              'holdout':'Half of previously unseen validation groups within action strata; pilot validation remains tuning only. Test split untouched.',
              'original_media_hash_matches':verified,'views':selected,
              'role_groups':dict(Counter(roles.values()))}
    path=OUT/'manifest.json'
    if path.exists() and json.loads(path.read_text())!=manifest:raise ValueError('Existing experiment plan differs')
    path.write_text(json.dumps(manifest,indent=2)+'\n')
    for filename in ['LICENSE','NOTICE']:
        (OUT/filename).write_bytes((ROOT/'data/retailaction'/filename).read_bytes())
    counts={role:dict(Counter(label for key,v in groups.items() if roles.get(key)==role for label in v[0]['action_classes']))
            for role in ['train','tune','holdout']}
    print(json.dumps({'role_groups':manifest['role_groups'],'group_label_counts':counts,'verified_original_video_hashes':verified}),flush=True)
    return selected


def folder(row):return OUT/row['split']/row['sample_id']


def fetch_video(row):
    global PAUSE_UNTIL
    target=folder(row)/f"{row['group']['name']}_video.mp4";target.parent.mkdir(parents=True,exist_ok=True)
    expected=row['remote_sha256']
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==expected:return
    old=ROOT/'data/retailaction'/row['split']/row['sample_id']/target.name
    if old.exists() and hashlib.sha256(old.read_bytes()).hexdigest()==expected:
        if target.exists():target.unlink()
        os.link(old,target);return
    for attempt in range(8):
        try:
            with request(f"{BASE}/{row['remote_path']}?download=true") as response:payload=response.read()
            if hashlib.sha256(payload).hexdigest()!=expected:raise ValueError('Video hash mismatch')
            temporary=target.with_suffix('.partial');temporary.write_bytes(payload);temporary.replace(target);return
        except urllib.error.HTTPError as error:
            if error.code!=429 or attempt==7:raise
            retry=error.headers.get('Retry-After','')
            delay=float(retry) if retry.isdigit() else min(300,30*2**attempt)
            with REQUEST_LOCK:PAUSE_UNTIL=max(PAUSE_UNTIL,time.monotonic()+delay)
            print(f'Dataset host rate limit: pausing requests for {delay:.0f}s',flush=True)
        except Exception:
            if attempt==7:raise
            time.sleep(1+attempt)


def videos(rows):
    started=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(fetch_video,row) for row in rows]
        for i,future in enumerate(concurrent.futures.as_completed(futures),1):
            future.result()
            if i%256==0:print(f'videos {i}/{len(rows)} complete ({time.monotonic()-started:.0f}s)',flush=True)


def poses(rows):
    marker=OUT/'pose_inventory_complete.json'
    if marker.exists():print('Pose inventory already complete',flush=True);return
    lookup={row['_id']['$oid']:row for row in rows}
    current=None;frames={};processed=0;kept=0;started=time.monotonic()
    def flush(uid,values):
        if uid not in lookup:return
        row=lookup[uid];directory=folder(row);directory.mkdir(parents=True,exist_ok=True)
        target=directory/f"{row['group']['name']}_poses.json"
        payload={'source_revision':REV,'sample_oid':uid,'pose_coordinate_order':'x,y','frames':values}
        target.write_text(json.dumps(payload,separators=(',',':')))
    with request(f'{BASE}/frames.json?download=true') as response:
        for frame in ijson.items(io.BufferedReader(response,1024*1024),'frames.item',use_float=True):
            uid=frame['_sample_id']['$oid']
            if uid!=current:
                if current is not None:flush(current,frames)
                current=uid;frames={}
            if uid in lookup:
                keypoints=frame.get('pose',{}).get('keypoints',[]) if frame.get('pose') else []
                record=keypoints[0] if keypoints else {}
                frames[str(frame['frame_number'])]={'points':record.get('points',[]),'confidence':record.get('confidence',[])}
                kept+=1
            processed+=1
            if processed%100000==0:print(f'pose frames inspected {processed}, retained {kept} ({time.monotonic()-started:.0f}s)',flush=True)
        if current is not None:flush(current,frames)
    marker.write_text(json.dumps({'frames_inspected':processed,'selected_frames':kept,'seconds':time.monotonic()-started,'revision':REV},indent=2))


if __name__=='__main__':
    rows=select()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        jobs=[executor.submit(videos,rows),executor.submit(poses,rows)]
        for job in concurrent.futures.as_completed(jobs):job.result()
    (OUT/'READY').write_text('Verified media and extracted pose inventory complete.\n')
    print('EXPANDED DATA READY',flush=True)
