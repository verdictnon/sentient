"""Causal, source-paced detector + pickup inference with frame-aligned SSE output."""
import base64
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import threading
import time
import uuid

import av
import numpy as np
from fastapi import APIRouter
from fastapi.responses import StreamingResponse, FileResponse
from app.p1.geometry import load_geometry
from app.p1.media import available
from app.p2.config import TrackingConfig
from app.p2.tracker import ByteTracker
from app.shelves import ShelfStats


class LiveManager:
    def __init__(self,catalog,tracking,actions):
        self.catalog,self.tracking,self.actions=catalog,tracking,actions
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='live-analysis')
        self.lock=threading.RLock();self.sessions={};self.detector=None;self.runtime=None;self.closed=False
        self.root=catalog.root/'live';self.root.mkdir(exist_ok=True)

    def get(self,key):
        with self.lock:
            if key not in self.sessions:
                try:valid=uuid.UUID(key).hex==key
                except ValueError:valid=False
                folder=self.root/key if valid else None
                if folder is None or not (folder/'summary.json').is_file():raise KeyError('Live session not found')
                payload=json.loads((folder/'summary.json').read_text())
                session={k:payload[k] for k in ('id','video_id','geometry_id','status')}
                session.update(sequence=1,payload=payload,folder=folder,condition=threading.Condition(),stop=threading.Event(),done=threading.Event())
                session['done'].set();self.sessions[key]=session
            return self.sessions[key]

    def start(self,video_id):
        if self.closed:raise RuntimeError('Live worker is stopping')
        video=self.catalog.video(video_id);available(video)
        if video['source_profile']['source_kind']=='sparse_clip':raise ValueError('Choose a continuous recording for live mode; this clip contains selected frames')
        geometry=load_geometry(self.catalog,video_id)
        with self.lock:
            for s in self.sessions.values():
                if not s['done'].is_set():raise RuntimeError('Stop the active live analysis before starting another camera')
            # Bound process memory; completed results stay on disk.
            self.sessions={k:s for k,s in list(self.sessions.items())[-7:]}
            key=uuid.uuid4().hex
            s={'id':key,'video_id':video_id,'geometry_id':geometry['id'],'status':'starting','sequence':0,
               'payload':{'status':'starting','message':'Loading detector and pickup model'},'condition':threading.Condition(),
               'stop':threading.Event(),'done':threading.Event(),'folder':self.root/key}
            s['folder'].mkdir();self.sessions[key]=s
            self.pool.submit(self._run,s,video,geometry['geometry'])
            return self.public(s)

    def public(self,s):
        with s['condition']:
            return {k:s[k] for k in ('id','video_id','geometry_id','status','sequence')}|{'latest':s['payload']}

    def list(self,video_id):
        """Discover persisted runs without sending frame images or loading models."""
        self.catalog.video(video_id)
        rows={}
        for path in self.root.glob('*/summary.json'):
            try:
                payload=json.loads(path.read_text())
            except (OSError,ValueError):
                continue
            if payload.get('video_id')==video_id:
                rows[payload['id']]={k:payload.get(k) for k in
                    ('id','video_id','geometry_id','status','processed_frames','scored_windows','elapsed_seconds')}
                rows[payload['id']]['saved_at']=path.stat().st_mtime
        with self.lock:
            for key,s in self.sessions.items():
                if s['video_id']==video_id and not s['done'].is_set():
                    rows[key]={k:s[k] for k in ('id','video_id','geometry_id','status')}
                    rows[key]['saved_at']=time.time()
        return sorted(rows.values(),key=lambda r:r['saved_at'],reverse=True)[:20]

    def publish(self,s,payload):
        with s['condition']:
            s['sequence']+=1;s['status']=payload['status'];s['payload']=payload
            s['condition'].notify_all()

    def stop(self,key):
        s=self.get(key);s['stop'].set();return {'id':key,'status':'stopping'}

    def _run(self,s,video,geometry):
        events=[];processed=0;scored=0;last=None;error=None;seen=set()
        config=TrackingConfig(detector='rtdetrv2_r34',min_person_area_ratio=.005,person_nms_iou=.4,max_tracks=32)
        stats=ShelfStats(geometry);start=None
        try:
            # All GPU work shares the same lock as offline analysis.
            with self.tracking.inference_lock:
                if s['stop'].is_set():return
                if self.detector is None:self.detector=self.tracking.factory(config)
                if self.runtime is None:
                    from scripts.worker.model import Runtime
                    self.runtime=Runtime(self.actions.model)
                    # Compile both bounded batch shapes before the source clock
                    # starts. Warm-up tensors never become video predictions.
                    for batch in (1,1,1,2,2,2):self.runtime.predict(np.zeros((batch,3,13,160,160),dtype=np.float32))
                from scripts.worker.pipeline import crop_tensor
                threshold=self.runtime.metadata['pickup_policy']['threshold']
                tracker=ByteTracker(config);history=defaultdict(lambda:deque(maxlen=13));frames={};order=deque()
                paths=defaultdict(lambda:deque(maxlen=300));last_scored={};active={};next_sample=0.;first=video['first_media_time_ms']
                start=time.monotonic()
                with (s['folder']/'observations.jsonl').open('w') as output,av.open(str(available(video))) as container:
                    stream=container.streams.video[0];stream.thread_count=1
                    for index,frame in enumerate(container.decode(stream)):
                        if s['stop'].is_set():break
                        ms=float(frame.pts*frame.time_base*1000)
                        if ms+1e-4<next_sample:continue
                        next_sample=(int((ms-first+1e-4)/config.sample_period_ms)+1)*config.sample_period_ms+first
                        if s['stop'].wait(max(0,start+(ms-first)/1000-time.monotonic())):break
                        tick=time.perf_counter();image=frame.to_image().convert('RGB');dimensions=image.size
                        tracks=tracker.advance(self.detector.detect(image,index),ms,dimensions)
                        observed=[t for t in tracks if t['position_status']=='observed']
                        seen.update(t['local_track_id'] for t in observed)
                        frames[index]=np.asarray(image);order.append(index)
                        if len(order)>16:frames.pop(order.popleft(),None)
                        stats.update(ms,observed);windows=[];owners=[]
                        for t in observed:
                            identity=t['local_track_id'];rows=history[identity]
                            if rows and ms-rows[-1][1]>1000:rows.clear()
                            rows.append((index,ms,t['box']));b=t['box']
                            p=paths[identity]
                            if p and ms-p[-1][1]>1500:p.clear()
                            p.append([index,ms,(b[0]+b[2])/2,b[3]])
                            if len(rows)<13 or any(r[0] not in frames for r in rows) or ms-last_scored.get(identity,-10000)<1200:continue
                            boxes=[r[2] for r in rows];x1=min(b[0] for b in boxes);y1=min(b[1] for b in boxes);x2=max(b[2] for b in boxes);y2=max(b[3] for b in boxes)
                            side=max(x2-x1,y2-y1,min(dimensions)*.25)*1.4;cx,cy=(x1+x2)/2,(y1+y2)/2
                            crop=[round(cx-side/2),round(cy-side/2),round(cx+side/2),round(cy+side/2)]
                            indices=[r[0] for r in rows]
                            windows.append(crop_tensor(frames,crop,indices));owners.append((t,list(rows),indices));last_scored[identity]=ms
                        predictions=[]
                        for offset in range(0,len(windows),2):
                            scores=self.runtime.predict(np.ascontiguousarray(np.stack(windows[offset:offset+2])))
                            for score,(t,rows,indices) in zip(scores,owners[offset:offset+2]):
                                scored+=1;identity=t['local_track_id'];value=float(score[0])
                                predictions.append({'local_track_id':identity,'take_score':value,'input_frame_indices':indices,'emitted_at_frame':index})
                                if value>=threshold:
                                    prior=active.get(identity)
                                    if prior and rows[0][1]<=prior['end_media_ms']+500:
                                        prior['end_media_ms']=ms;prior['score']=max(prior['score'],value)
                                    else:
                                        prior={'id':f'{s["id"]}:{identity}:{index}','local_track_id':identity,'start_media_ms':rows[0][1],
                                               'end_media_ms':ms,'detected_media_ms':ms,'score':value,'status':'model_prediction'}
                                        active[identity]=prior;events.append(prior);stats.pickup(prior,t)
                        for t in tracks:
                            t['take_score']=next((p['take_score'] for p in predictions if p['local_track_id']==t['local_track_id']),None)
                        processed+=1;latency=(time.perf_counter()-tick)*1000
                        visible_ids={t['local_track_id'] for t in tracks}
                        # Trail state is bounded to visible tracks; full observations are streamed to disk.
                        for identity in list(paths):
                            if identity not in visible_ids:paths.pop(identity,None);history.pop(identity,None);last_scored.pop(identity,None);active.pop(identity,None)
                        buf=BytesIO();image.save(buf,format='JPEG',quality=80)
                        last={'status':'running','frame_index':index,'media_ms':ms,'source_elapsed_seconds':(ms-first)/1000,
                              'frame_dims_px':list(dimensions),'tracks':tracks,'paths':{k:list(v) for k,v in paths.items()},
                              'pickups':events[-100:],'pickup_count':len(events),'shelves':stats.snapshot(),'predictions':predictions,
                              'processed_frames':processed,'scored_windows':scored,'unique_tracks':len(seen),'warmup_frames':max((len(history[t['local_track_id']]) for t in observed),default=0),
                              'inference_ms':round(latency,1),'behind_source_ms':round(max(0,(time.monotonic()-start)*1000-(ms-first)),1),
                              'analysis_fps':round(processed/max(.001,time.monotonic()-start),2),'threshold':threshold,
                              'image':base64.b64encode(buf.getvalue()).decode('ascii')}
                        output.write(json.dumps({k:v for k,v in last.items() if k not in ('image','paths','pickups','shelves')})+'\n');output.flush()
                        self.publish(s,last)
                status='stopped' if s['stop'].is_set() else 'completed'
        except Exception as exc:
            status='error';error=f'{type(exc).__name__}: {exc}'
            # A failed GPU operation must not poison a cached model on retry.
        finally:
            status=locals().get('status','stopped')
            summary={'id':s['id'],'video_id':video['id'],'geometry_id':s['geometry_id'],'status':status,'error':error,
                     'processed_frames':processed,'scored_windows':scored,'unique_tracks':len(seen),
                     'threshold':self.runtime.metadata['pickup_policy']['threshold'] if self.runtime else None,
                     'pickups':events,'shelves':stats.snapshot(),
                     'elapsed_seconds':round(time.monotonic()-start,2) if start else None,
                     'model_id':self.runtime.metadata['model_id'] if self.runtime else None,
                     'execution':'causal source-paced recording; no future frames; camera-local identities'}
            (s['folder']/'summary.json').write_text(json.dumps(summary,indent=2))
            self.publish(s,(last or {})|summary);s['done'].set()
            with s['condition']:s['condition'].notify_all()

    def shutdown(self):
        self.closed=True
        for s in self.sessions.values():s['stop'].set()
        self.pool.shutdown(wait=True)
        for model in (self.detector,self.runtime):
            if model is not None:model.close()


def router(manager):
    r=APIRouter(prefix='/api/live')
    @r.get('/videos/{video_id}')
    def sessions(video_id:str):return manager.list(video_id)
    @r.post('/videos/{video_id}')
    def start(video_id:str):return manager.start(video_id)
    @r.get('/sessions/{key}')
    def status(key:str):return manager.public(manager.get(key))
    @r.post('/sessions/{key}/stop')
    def stop(key:str):return manager.stop(key)
    @r.get('/sessions/{key}/observations')
    def observations(key:str):
        s=manager.get(key)
        if not s['done'].is_set():raise ValueError('Stop analysis before replaying saved frames')
        path=s['folder']/'observations.jsonl'
        if not path.is_file():raise ValueError('No observations were produced')
        return FileResponse(path,media_type='application/x-ndjson')
    @r.get('/sessions/{key}/export')
    def export(key:str):
        s=manager.get(key)
        if not s['done'].is_set():raise ValueError('Stop analysis or wait for completion before exporting')
        return FileResponse(s['folder']/'summary.json',filename='sentient-live-results.json')
    @r.get('/sessions/{key}/shelves')
    def shelves(key:str):
        s=manager.get(key)
        if not s['done'].is_set():raise ValueError('Stop analysis before recalculating areas')
        saved=load_geometry(manager.catalog,s['video_id'])
        stats=ShelfStats(saved['geometry'])
        pending=list(s['payload'].get('pickups',[]))
        path=s['folder']/'observations.jsonl'
        if path.is_file():
            with path.open() as source:
                for line in source:
                    row=json.loads(line);ms=row['media_ms'];tracks=row.get('tracks',[])
                    stats.update(ms,tracks)
                    for event in pending[:]:
                        detected=event.get('detected_media_ms',event['end_media_ms'])
                        if detected>ms:continue
                        match=next((t for t in tracks if t['local_track_id']==event['local_track_id'] and t['position_status']=='observed'),None)
                        if match and abs(detected-ms)<.01:stats.pickup(dict(event),match)
                        pending.remove(event)
        return stats.snapshot()|{'geometry_id':saved['id'],'session_id':key}
    @r.get('/sessions/{key}/stream')
    def stream(key:str):
        s=manager.get(key)
        def generate():
            sequence=-1
            while True:
                with s['condition']:
                    if sequence==s['sequence'] and not s['done'].is_set():s['condition'].wait(timeout=5)
                    if sequence==s['sequence']:
                        if s['done'].is_set():return
                        yield ': keepalive\n\n';continue
                    sequence=s['sequence'];payload=s['payload'].copy()
                yield 'id: '+str(sequence)+'\ndata: '+json.dumps(payload)+'\n\n'
                if payload['status'] in ('completed','stopped','error'):return
        return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
    return r
