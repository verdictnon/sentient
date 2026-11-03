"""Reuse existing data; fetch a preregistered official-test cohort and tail poses."""
import concurrent.futures
from collections import Counter
import hashlib
import io
import json
import os
import random
import re
import time
import urllib.request
import ijson
from train_pilot import ROOT
import prepare_expanded as source

DATA=ROOT/'data/retailaction_v3'
REV=source.REV


def plan():
    DATA.mkdir(parents=True,exist_ok=True)
    path=DATA/'manifest.json'
    if path.exists():return json.loads(path.read_text())
    all_rows=json.loads((ROOT/'data/retailaction_inventory/mapped_samples.json').read_text())
    original=json.loads((ROOT/'data/retailaction_v2/manifest.json').read_text())
    def ordered(values,salt):return sorted(values,key=lambda s:hashlib.sha256((salt+s).encode()).hexdigest())
    train={r['sample_id']:r for r in original['views'] if r['split']=='train'}
    touch=ordered([sid for sid,r in train.items() if 'touch' in r['action_classes']],'v3-touch-calibration')[:12]
    put=ordered([sid for sid,r in train.items() if 'put' in r['action_classes'] and sid not in touch],'v3-put-calibration')[:40]
    calibration=set(touch+put)
    test={r['sample_id']:r for r in all_rows if r['split']=='test'}
    rng=random.Random(31029);natural=set(rng.sample(sorted(test),500))
    rare={sid for sid,r in test.items() if set(r['action_classes'])&{'put','touch'}}
    keys={(r['split'],r['sample_id']) for r in original['views']}|{('test',sid) for sid in natural|rare}
    fields=['_id','split','sample_id','group','metadata','ground_truth','action_classes','segment_duration_s','remote_path','remote_sha256']
    rows=[]
    for r in all_rows:
        if (r['split'],r['sample_id']) not in keys:continue
        row={key:r[key] for key in fields}
        row['role']='test' if r['split']=='test' else ('tune' if r['split']=='validation' or r['sample_id'] in calibration else 'train')
        row['natural_test']=r['split']=='test' and r['sample_id'] in natural
        rows.append(row)
    result={'revision':REV,'seed':31029,'calibration_from_original_train':sorted(calibration),
            'natural_test_groups':sorted(natural),'extra_rare_test_groups':sorted(rare-natural),
            'protocol':'Fresh heads on frozen original Kinetics prefix. V2 validation reused for tuning; official test is evaluation only.',
            'views':rows}
    path.write_text(json.dumps(result,indent=2)+'\n')
    for name in ['LICENSE','NOTICE']:(DATA/name).write_bytes((ROOT/'data/retailaction_v2'/name).read_bytes())
    return result


def videos(rows):
    source.OUT=DATA
    def fetch(row):
        directory=DATA/row['split']/row['sample_id'];directory.mkdir(parents=True,exist_ok=True)
        for name in [row['group']['name']+'_video.mp4',row['group']['name']+'_poses.json']:
            old=ROOT/'data/retailaction_v2'/row['split']/row['sample_id']/name;target=directory/name
            if old.exists() and not target.exists():os.link(old,target)
        source.fetch_video(row)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(fetch,row) for row in rows]
        for i,f in enumerate(concurrent.futures.as_completed(futures),1):
            f.result()
            if i%512==0:print(f'V3 media {i}/{len(rows)} ready',flush=True)


class PrefixStream(io.RawIOBase):
    def __init__(self,prefix,response):self.prefix=io.BytesIO(prefix);self.response=response
    def readable(self):return True
    def readinto(self,buffer):
        data=self.prefix.read(len(buffer))
        if not data:data=self.response.read(len(buffer))
        buffer[:len(data)]=data;return len(data)


def test_poses(rows):
    marker=DATA/'test_pose_inventory_complete.json'
    if marker.exists():return
    selected={r['_id']['$oid']:r for r in rows if r['split']=='test'}
    all_rows=json.loads((ROOT/'data/retailaction_inventory/mapped_samples.json').read_text())
    order={r['_id']['$oid']:i for i,r in enumerate(all_rows)}
    first_test=min(i for i,r in enumerate(all_rows) if r['split']=='test')
    # Frames are exported in sample order. Read a generous suffix and verify coverage.
    offset=int(2089440040*.80)
    request=urllib.request.Request(f'{source.BASE}/frames.json?download=true',headers={'Range':f'bytes={offset}-','User-Agent':'Sentient-research/0.3'})
    seen=set();current=None;frames={};processed=0;first=None;last=-1;started=time.monotonic()
    def flush(uid,values):
        if uid not in selected:return
        row=selected[uid];folder=DATA/'test'/row['sample_id'];folder.mkdir(parents=True,exist_ok=True)
        target=folder/(row['group']['name']+'_poses.json')
        if target.exists():
            merged=json.loads(target.read_text())['frames'];merged.update(values);values=merged
        target.write_text(json.dumps({'source_revision':REV,'sample_oid':uid,'pose_coordinate_order':'x,y','frames':values},separators=(',',':')))
    with urllib.request.urlopen(request,timeout=60) as response:
        if response.status!=206:raise ValueError('Dataset host did not honor the byte range')
        initial=response.read(65536)
        match=re.search(rb'\{"_id":\s*\{"\$oid":\s*"[a-f0-9]+"\},\s*"frame_number":',initial)
        if match is None:raise ValueError('Could not find a complete frame boundary')
        stream=io.BufferedReader(PrefixStream(b'{"frames": ['+initial[match.start():],response),1024*1024)
        for frame in ijson.items(stream,'frames.item',use_float=True):
            uid=frame['_sample_id']['$oid'];position=order[uid]
            if first is None:
                first=position
                if first>first_test:raise ValueError('Range omitted the start of the official test split')
            # Extra validation-only annotation fields are appended after the test frames.
            # Read to EOF and merge repeated records; the suffix covers the whole test section.
            last=max(last,position)
            if uid!=current:
                if current is not None:flush(current,frames)
                current=uid;frames={}
            if uid in selected:
                seen.add(uid);points=(frame.get('pose') or {}).get('keypoints',[]);record=points[0] if points else {}
                frames[str(frame['frame_number'])]={'points':record.get('points',[]),'confidence':record.get('confidence',[])}
            processed+=1
            if processed%50000==0:print(f'V3 pose tail: {processed} frames inspected ({time.monotonic()-started:.0f}s)',flush=True)
        if current is not None:flush(current,frames)
    if last!=len(all_rows)-1:raise ValueError('Suffix did not reach the final official-test view')
    marker.write_text(json.dumps({'first_source_sample_index':first,'first_test_index':first_test,
        'frames_inspected':processed,'selected_views_with_frames':len(seen),'selected_views_without_export':len(selected)-len(seen),'seconds':time.monotonic()-started},indent=2))


if __name__=='__main__':
    p=plan();rows=p['views']
    print('V3 groups '+json.dumps(dict(Counter((r['role']) for r in rows if r['group']['name']=='rank0'))),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(videos,rows),pool.submit(test_poses,rows)]
        for f in concurrent.futures.as_completed(futures):f.result()
    (DATA/'READY').write_text('Complete\n');print('V3 DATA READY',flush=True)
