"""One durable app job: P2 tracking -> real action worker -> review analysis."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from app.p1.geometry import load_geometry
from app.p1.media import available
from app.p1.source_profile import source_profile, PHYSICAL_FIELDS
from app.p1.workspace import run_analysis
from app.p2.config import TrackingConfig
from app.pickups import merge_pickups
from app.storage import canonical, processing_time, stable_id


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda:stream.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def worker_root():
    override=os.environ.get('SENTIENT_ACTION_WORKER_ROOT')
    if override:return Path(override).resolve()
    return next((p for p in Path(__file__).resolve().parents if (p/'scripts/action_worker.py').is_file()),None)


class Cancelled(Exception):pass


class ActionAnalysisManager:
    def __init__(self,catalog,tracking):
        self.catalog,self.tracking=catalog,tracking
        self.lock=threading.RLock();self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='action-analysis')
        self.cancellations={};self.closed=False;self.runtime=None;self.runtime_key=None
        self.root=worker_root()
        self.python=(self.root/'.venv/bin/python') if self.root else None
        config_path=self.root/'demo_config.json' if self.root else None
        demo=json.loads(config_path.read_text()) if config_path and config_path.is_file() else {}
        self.demo_entry=demo.get('default_demo');self.demo_library=demo.get('demo_library',[])
        self.model=(self.root/demo.get('model_dir','runs/action_worker_v1/model')) if self.root else None
        with catalog.connect(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS pickup_event_sets(analysis_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS action_analysis_jobs(id TEXT PRIMARY KEY,video_id TEXT NOT NULL,payload TEXT NOT NULL)')
            for row in db.execute('SELECT id,payload FROM action_analysis_jobs').fetchall():
                item=json.loads(row['payload'])
                if item['status'] in ('queued','running','cancelling'):
                    item.update(status='error',stage='interrupted',error='Application restarted; Analyze video again to retry.')
                    db.execute('UPDATE action_analysis_jobs SET payload=? WHERE id=?',(canonical(item),row['id']))

    def readiness(self):
        ok=bool(self.root and (self.model/'model.pt').is_file() and (self.model/'manifest.json').is_file())
        return {'available':ok,'mode':'review_only','automatic_human_labels':False,'runtime':'in_process_cached','frame_index':'reuses_catalog'}

    def get(self,identity):
        with self.catalog.connect() as db:r=db.execute('SELECT payload FROM action_analysis_jobs WHERE id=?',(identity,)).fetchone()
        if r is None:raise KeyError('Analysis job not found')
        return json.loads(r[0])

    def list(self,video_id):
        self.catalog.video(video_id)
        with self.catalog.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM action_analysis_jobs WHERE video_id=? ORDER BY rowid DESC',(video_id,))]

    def update(self,identity,**values):
        with self.lock,self.catalog.connect(write=True) as db:
            r=db.execute('SELECT payload FROM action_analysis_jobs WHERE id=?',(identity,)).fetchone()
            job=json.loads(r[0]);job.update(values)
            db.execute('UPDATE action_analysis_jobs SET payload=? WHERE id=?',(canonical(job),identity))
        return job

    def start(self,video_id,geometry_id=None,source_kind='continuous',tracking_config=None):
        if self.closed:raise RuntimeError('Analysis worker is stopping')
        if not self.readiness()['available']:raise ValueError('The local action worker/model is not configured')
        if source_kind not in ('continuous','sparse_clip'):raise ValueError('Declare continuous recording or sparse clip')
        video=self.catalog.video(video_id);available(video)
        geometry=load_geometry(self.catalog,video_id,geometry_id)
        if source_profile(video)['source_kind']=='sparse_clip' and source_kind!='sparse_clip':
            raise ValueError('This RetailAction clip is motion-sampled; choose Sparse sampled clip')
        config_values=dict(tracking_config or {})
        if 'source_kind' in config_values and config_values['source_kind']!=source_kind:raise ValueError('Tracking and action source semantics disagree')
        config_values['source_kind']=source_kind
        config=TrackingConfig.parse(config_values).to_dict()
        manifest=json.loads((self.model/'manifest.json').read_text())
        identity=stable_id(['analyze-video/1',video_id,geometry['id'],source_kind,config,
            manifest['model_sha256'],sha(self.root/'scripts/worker/pipeline.py'),'source-aware-cached-analysis/3'])
        with self.lock:
            try:prior=self.get(identity)
            except KeyError:prior=None
            if prior and prior['status'] in ('queued','running','cancelling','completed'):return prior
            if any(x['status'] in ('queued','running','cancelling') for x in self.list(video_id)):
                raise RuntimeError('This video already has an active analysis')
            attempt=1+(prior['attempt'] if prior else 0)
            folder=self.catalog.root/'action_analysis'/identity/f'attempt-{attempt:04d}';folder.mkdir(parents=True)
            job={'id':identity,'video_id':video_id,'geometry_id':geometry['id'],'source_kind':source_kind,
                'config':config,'status':'queued','stage':'queued','progress':0.,'attempt':attempt,
                'created_at_utc':processing_time(),'error':None,'analysis_id':None,'tracking_job_id':None,
                'folder':str(folder),'model_id':manifest['model_id'],'model_sha256':manifest['model_sha256'],'pickup_policy':manifest.get('pickup_policy')}
            with self.catalog.connect(write=True) as db:
                db.execute('INSERT INTO action_analysis_jobs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                    (identity,video_id,canonical(job)))
            event=threading.Event();self.cancellations[identity]=event
            self.pool.submit(self._run,identity,video,geometry,config,event)
            return job

    def _run(self,identity,video,geometry,config,cancel):
        start=time.monotonic();job=self.get(identity);folder=Path(job['folder'])
        try:
            if cancel.is_set():raise Cancelled()
            tracking=self.tracking.start(video['id'],geometry['id'],config)
            self.update(identity,status='running',stage='tracking_people',tracking_job_id=tracking['id'],progress=.02)
            while tracking['status'] in ('queued','running','cancelling'):
                if cancel.wait(.15):raise Cancelled()
                tracking=self.tracking.get(tracking['id'])
                self.update(identity,progress=.02+.58*tracking['progress'])
            if tracking['status']!='completed':raise ValueError('Tracking failed: '+str(tracking.get('error')))
            if cancel.is_set():raise Cancelled()
            self.update(identity,stage='scoring_actions',progress=.62)
            tracks=Path(tracking['output_path']);out=folder/'actions'
            def check_cancel():
                if cancel.is_set():raise Cancelled()
            last_progress=[0.]
            def scoring_progress(fraction):
                if fraction-last_progress[0]>=.025:
                    last_progress[0]=fraction
                    self.update(identity,progress=.62+.27*fraction)
            if str(self.root) not in sys.path:sys.path.insert(0,str(self.root))
            from scripts.worker.model import Runtime
            from scripts.worker.pipeline import Policy, run_video
            with self.catalog.connect() as db:
                frames=[{'frame_index':r['frame_index'],'pts':r['pts'],
                    'time_base':[r['tb_num'],r['tb_den']],'media_ms':r['media_ms']}
                    for r in db.execute('SELECT * FROM frames WHERE sha=? ORDER BY frame_index',(video['media_sha256'],))]
            index={'frame_dims_px':video['frame_dims_px'],'frame_count':video['frame_count'],'frames':frames}
            with self.tracking.inference_lock:
                check_cancel()
                stat=(self.model/'model.pt').stat()
                key=(job['model_sha256'],stat.st_size,stat.st_mtime_ns)
                cache_hit=self.runtime is not None and self.runtime_key==key
                if not cache_hit:
                    if self.runtime is not None:self.runtime.close()
                    self.runtime=None;self.runtime_key=None
                    self.runtime=Runtime(self.model)
                    if self.runtime.metadata['model_sha256']!=job['model_sha256']:
                        self.runtime.close();self.runtime=None
                        raise ValueError('Action model changed after job creation; submit a new analysis')
                    self.runtime.execution_context='integrated_application'
                    self.runtime_key=key
                actions=run_video(available(video),tracks,self.model,out,Policy(source_kind=job['source_kind']),
                    self.runtime,index=index,cancel_check=check_cancel,progress=scoring_progress)
            (folder/'worker.log').write_text(json.dumps(actions,indent=2)+'\n')
            self.update(identity,action_runtime_cache_hit=cache_hit,frame_index_reused=True,runtime_mode='in_process_cached')
            if cancel.is_set():raise Cancelled()
            self.update(identity,stage='building_review',progress=.92)
            combined=folder/'combined.jsonl'
            with combined.open('wb') as dest:
                for path in (tracks,out/'records.jsonl'):
                    with path.open('rb') as stream:
                        for block in iter(lambda:stream.read(1024*1024),b''):dest.write(block)
            analysis=run_analysis(self.catalog,combined,video['id'],geometry['id'],isolated_run=True,source_kind=job['source_kind'],analytics_config_override=tracking['p0_config'])
            for key in PHYSICAL_FIELDS:
                if analysis['summary'][key]!=tracking['summary'][key]:raise ValueError('Action integration changed tracking analytics')
            if analysis['summary']['confirmed_physical_actions']!=0:raise ValueError('Review-only output became a confirmed action')
            if cancel.is_set():raise Cancelled()
            actions=json.loads((out/'summary.json').read_text())
            records=[json.loads(line) for line in (out/'records.jsonl').read_text().splitlines() if line]
            pickups=merge_pickups(records,job.get('pickup_policy'),analysis['id'])
            with self.catalog.connect(write=True) as db:
                db.execute('INSERT OR REPLACE INTO pickup_event_sets VALUES (?,?)',(analysis['id'],canonical({'policy':job.get('pickup_policy'),'events':pickups})))
            self.update(identity,status='completed',stage='ready_for_review',progress=1.,analysis_id=analysis['id'],
                action_windows=actions['scored_windows'],abstained_windows=actions['abstained_windows'],pickup_events=len(pickups),
                elapsed_seconds=time.monotonic()-start,finished_at_utc=processing_time(),
                tracking_cached=tracking['finished_at_utc']<job['created_at_utc'])
        except Cancelled:
            self.update(identity,status='cancelled',stage='cancelled',error=None)
        except Exception as error:
            self.update(identity,status='error',stage='failed',error=str(error))
        finally:
            with self.lock:self.cancellations.pop(identity,None)

    def cancel(self,identity):
        # Publish cancellation before signalling: a fast worker must not finish
        # and then be overwritten with a permanently cancelling state.
        with self.lock:
            job=self.get(identity)
            if job['status'] not in ('queued','running','cancelling'):return job
            result=self.update(identity,status='cancelling',stage='cancelling')
            if identity in self.cancellations:self.cancellations[identity].set()
        if job.get('tracking_job_id'):
            tracking=self.tracking.get(job['tracking_job_id'])
            if tracking['status'] in ('queued','running','cancelling'):self.tracking.cancel(tracking['id'])
        return result

    def shutdown(self):
        self.closed=True
        for identity in list(self.cancellations):self.cancel(identity)
        self.pool.shutdown(wait=True,cancel_futures=True)
        if self.runtime is not None:
            with self.tracking.inference_lock:self.runtime.close()
            self.runtime=None;self.runtime_key=None


class AnalyzeRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    geometry_id:str|None=None
    source_kind:Literal['continuous','sparse_clip']
    tracking_config:dict=Field(default_factory=dict)


def router(manager):
    api=APIRouter(prefix='/api/action-analysis')
    @api.get('/status')
    def status():return manager.readiness()
    @api.get('/videos/{video_id}')
    def jobs(video_id:str):return manager.list(video_id)
    @api.post('/videos/{video_id}')
    def start(video_id:str,body:AnalyzeRequest):return manager.start(video_id,body.geometry_id,body.source_kind,body.tracking_config)
    @api.get('/jobs/{job_id}')
    def job(job_id:str):return manager.get(job_id)
    @api.get('/analyses/{analysis_id}/pickups')
    def pickups(analysis_id:str):
        manager.catalog.analysis(analysis_id)
        with manager.catalog.connect() as db:r=db.execute('SELECT payload FROM pickup_event_sets WHERE analysis_id=?',(analysis_id,)).fetchone()
        return json.loads(r[0]) if r else {'policy':None,'events':[]}
    @api.post('/jobs/{job_id}/cancel')
    def cancel(job_id:str):return manager.cancel(job_id)
    return api
