"""One bounded offline inference worker, durable progress and immutable run scope."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from fractions import Fraction
import json
import os
from pathlib import Path
import threading
import time

import av
import psutil

from app.p1.geometry import load_geometry
from app.p1.media import available
from app.p1.source_profile import source_profile, interpret_summary
from app.p2.config import TrackingConfig
from app.p2.detector import PretrainedDetector, model_metadata
from app.p2.persistence import StreamingSink, read_database, replay_output
from app.p2.setup import DEFAULT_CACHE
from app.p2.tracker import ByteTracker, TRACKER_VERSION
from app.storage import canonical, processing_time, stable_id

PIPELINE_VERSION='sentient-p2/2-source-aware'


class DetectorFactory:
    def __init__(self,cache):self.cache=cache
    def provenance(self,config):return model_metadata(self.cache,config.detector)
    def __call__(self,config):return PretrainedDetector(config,self.cache)


class Cancelled(Exception):pass


class TimeSampler:
    def __init__(self,period_ms):self.period=Fraction(str(period_ms));self.last=None
    def due(self,time_ms):
        time_ms=Fraction(time_ms)
        if self.last is None or time_ms-self.last>=self.period:
            self.last=time_ms;return True
        return False


class MemoryMonitor:
    def __init__(self):self.stop=threading.Event();self.peak=0;self.baseline=psutil.Process().memory_info().rss
    def __enter__(self):
        self.started=time.perf_counter()
        def sample():
            while not self.stop.is_set():
                self.peak=max(self.peak,psutil.Process().memory_info().rss)
                self.stop.wait(.05)
        self.thread=threading.Thread(target=sample,daemon=True);self.thread.start();return self
    def __exit__(self,*args):self.stop.set();self.thread.join()
    def report(self):return {'processing_seconds':time.perf_counter()-self.started,'process_rss_baseline_bytes':self.baseline,
                             'process_rss_peak_bytes':max(self.peak,self.baseline),
                             'memory_scope':'sampled server process RSS, including model and other server threads'}


class JobManager:
    def __init__(self,catalog,detector_factory=None,model_dir=None):
        self.catalog=catalog
        self.model_dir=Path(model_dir or os.environ.get('SENTIENT_MODEL_DIR',str(DEFAULT_CACHE))).resolve()
        self.factory=detector_factory or DetectorFactory(self.model_dir)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='sentient-inference')
        self.lock=threading.RLock();self.inference_lock=threading.RLock();self.cancellations={};self.closed=False
        (catalog.root/'tracking').mkdir(exist_ok=True)
        with catalog.connect(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS tracking_jobs(id TEXT PRIMARY KEY,video_id TEXT NOT NULL,geometry_id TEXT NOT NULL,payload TEXT NOT NULL)')
            # An interrupted process is not evidence of zero people or completion.
            for row in db.execute('SELECT id,payload FROM tracking_jobs').fetchall():
                job=json.loads(row['payload'])
                if job['status'] in ('queued','running','cancelling'):
                    job.update(status='error',error='Worker interrupted by server restart; submit the same run to retry')
                    db.execute('UPDATE tracking_jobs SET payload=? WHERE id=?',(canonical(job),row['id']))

    def get(self,job_id):
        with self.catalog.connect() as db:row=db.execute('SELECT payload FROM tracking_jobs WHERE id=?',(job_id,)).fetchone()
        if row is None:raise KeyError('Tracking job not found')
        job=json.loads(row[0])
        if job.get('summary') is not None:
            kind=source_profile(self.catalog.video(job['video_id']))['source_kind']
            job['summary']=interpret_summary(job['summary'],job['config'].get('source_kind',kind))
        return job

    def update(self,job_id,**values):
        with self.lock,self.catalog.connect(write=True) as db:
            row=db.execute('SELECT payload FROM tracking_jobs WHERE id=?',(job_id,)).fetchone()
            job=json.loads(row[0]);job.update(values)
            db.execute('UPDATE tracking_jobs SET payload=? WHERE id=?',(canonical(job),job_id))
        return job

    def list(self,video_id):
        with self.catalog.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM tracking_jobs WHERE video_id=? ORDER BY rowid DESC',(video_id,))]

    def start(self,video_id,geometry_id=None,parameters=None):
        if self.closed:raise RuntimeError('Tracking worker is shutting down')
        video=self.catalog.video(video_id);available(video)
        parameters=dict(parameters or {})
        profile=source_profile(video)
        if profile['source_kind']=='sparse_clip':
            if parameters.get('source_kind','sparse_clip')!='sparse_clip':raise ValueError('RetailAction is motion-sampled; continuous tracking semantics are unavailable')
            parameters['source_kind']='sparse_clip'
        config=TrackingConfig.parse(parameters)
        geometry=load_geometry(self.catalog,video_id,geometry_id)
        provenance=self.factory.provenance(config)
        scope={'video_sha256':video['media_sha256'],'video_id':video_id,'geometry_id':geometry['id'],
               'detector':provenance,'tracker':TRACKER_VERSION,'pipeline':PIPELINE_VERSION,'parameters':config.to_dict()}
        job_id=stable_id(scope)
        with self.lock:
            try:
                previous=self.get(job_id)
                if previous['status'] in ('queued','running','cancelling','completed'):return previous
                attempt=previous['attempt']+1
            except KeyError:attempt=1
            folder=self.catalog.root/'tracking'/job_id/f'attempt-{attempt:04d}'
            folder.mkdir(parents=True,exist_ok=False)
            # Preserve the registered camera/session in the stable P1 APIs and
            # export schema. A run-scoped local ID prevents tracker epoch reuse
            # when a different detector/configuration starts its counter at 1.
            session=video['stream_session_id']
            p0_config=json.loads(canonical(geometry['p0_config']))
            # There is no independent service-start signal in person boxes.
            p0_config.pop('queue',None)
            job={'id':job_id,'video_id':video_id,'geometry_id':geometry['id'],'attempt':attempt,
                 'source_session_id':session,'tracking_epoch':job_id[:16],'status':'queued','error':None,'progress':0.,'frames_processed':0,
                 'inference_frames':0,'observed_records':0,'predicted_records':0,'config':config.to_dict(),
                 'detector':provenance,'tracker_version':TRACKER_VERSION,'pipeline_version':PIPELINE_VERSION,'p0_config':p0_config,
                 'folder':str(folder),'database_path':str(folder/'analytics.sqlite'),
                 'output_path':str(folder/'tracks.jsonl'),'analysis_id':None,'metrics':None,
                 'created_at_utc':processing_time(),'artifacts_complete':False,
                 'queue_service_inference':'disabled: person proximity cannot establish service start'}
            with self.catalog.connect(write=True) as db:
                db.execute('INSERT INTO tracking_jobs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                           (job_id,video_id,geometry['id'],canonical(job)))
            cancellation=threading.Event();self.cancellations[job_id]=cancellation
            self.executor.submit(self._run,job_id,video,config,cancellation)
            return job

    def cancel(self,job_id):
        with self.lock:
            job=self.get(job_id)
            if job['status'] not in ('queued','running','cancelling'):return job
            self.cancellations[job_id].set()
            return self.update(job_id,status='cancelling')

    @staticmethod
    def base_record(job,video,time_ms,kind,identity):
        return {'schema_version':'1.0','type':kind,'record_id':stable_id([job['id'],kind,identity]),
                'store_id':video['store_id'],'camera_id':video['camera_id'],'stream_session_id':job['source_session_id'],
                'media_time_ms':float(time_ms),'capture_time_utc':None,'clock_status':'playback_only'}

    def _run(self,job_id,video,config,cancellation):
        with self.inference_lock:
            return self._run_locked(job_id,video,config,cancellation)

    def _run_locked(self,job_id,video,config,cancellation):
        detector=None;sink=None;output=None;current_time=Fraction(str(video['first_media_time_ms']));last_health=None
        frame_count=inferences=observed=predicted=0;model_metrics={};failure=None;result=None
        monitor=MemoryMonitor();monitor.__enter__()
        job=self.update(job_id,status='running',stage='loading_model')
        try:
            if cancellation.is_set():raise Cancelled('Tracking cancelled before model loading')
            sink=StreamingSink(job['database_path'],job['p0_config'])
            output=Path(job['output_path']).with_suffix('.partial.jsonl').open('w',encoding='utf-8')
            detector=self.factory(config)
            model_metrics=dict(detector.metadata)
            if any(model_metrics.get(k)!=job['detector'].get(k) for k in ('checkpoint_sha256','source_revision')):
                raise ValueError('Loaded model provenance changed after job creation; start a distinct run')
            self.update(job_id,detector_runtime=model_metrics,stage='decoding_and_tracking')
            tracker=ByteTracker(config);sampler=TimeSampler(config.sample_period_ms)
            model_id=config.detector+':'+job['detector']['checkpoint_sha256'][:16]+':'+str(job['detector']['source_revision'])[:12]
            with av.open(str(available(video))) as container,self.catalog.connect() as catalog_db, closing(
                catalog_db.execute('SELECT * FROM frames WHERE sha=? ORDER BY frame_index',(video['media_sha256'],))
            ) as indexed:
                stream=container.streams[video['video_stream_index']]
                for index,frame in enumerate(container.decode(stream)):
                    reference=next(indexed,None)
                    if reference is None or frame.pts is None or frame.time_base is None:
                        raise ValueError('Decoded frames disagree with the registered PTS index')
                    current_time=frame.pts*frame.time_base*1000
                    if (frame.pts!=reference['pts'] or frame.time_base!=Fraction(reference['tb_num'],reference['tb_den'])
                            or [frame.width,frame.height]!=video['frame_dims_px']):
                        raise ValueError('Source frame/PTS/dimensions changed after registration')
                    if current_time<0:raise ValueError('Negative source PTS cannot be represented by v1; no silent time shift is applied')
                    if cancellation.is_set():raise Cancelled('Tracking cancelled by operator')
                    infer=config.source_kind=='sparse_clip' or sampler.due(current_time)
                    detections=detector.detect(frame.to_image(),index) if infer else None
                    if infer:
                        inferences+=1
                        if any(d.frame_index!=index for d in detections):raise ValueError('Detector returned a mismatched source frame reference')
                    tracks=tracker.advance(detections,float(current_time),video['frame_dims_px'])
                    records=[]
                    for track in tracks:
                        local_id=job['tracking_epoch']+':'+track['local_track_id']
                        record=self.base_record(job,video,current_time,'track.observed',[index,local_id])
                        record.update(frame_index=index,local_track_id=local_id,global_visit_id=None,
                                      association_status='local_only',bbox_xyxy_px=list(track['box']),frame_dims_px=video['frame_dims_px'],
                                      score=track['score'],position_status=track['position_status'],world_xy_m=None,
                                      calibration_version=None,pose_ref=None,model_id=model_id)
                        records.append(record)
                        if track['position_status']=='observed':observed+=1
                        else:predicted+=1
                    terminal=index==video['frame_count']-1
                    health=self.base_record(job,video,current_time,'stream.health',index)
                    health.update(state='ended' if terminal else ('degraded' if tracker.capacity_drops else 'healthy'),
                                  dropped_frames=0,reason='end_of_recording' if terminal else ('track_capacity_exceeded' if tracker.capacity_drops else None))
                    records.append(health)
                    sink.write_frame(records,(index,int(infer),len(detections) if detections is not None else 0))
                    for record in records:output.write(canonical(record)+'\n')
                    last_health=current_time;frame_count+=1
                    if frame_count%5==0 or terminal:
                        output.flush()
                        self.update(job_id,progress=frame_count/video['frame_count'],frames_processed=frame_count,
                                    inference_frames=inferences,observed_records=observed,predicted_records=predicted,
                                    last_media_time_ms=float(current_time),capacity_drops=tracker.capacity_drops)
                if frame_count!=video['frame_count']:raise ValueError('Video ended before all indexed frames were decoded')
            available(video)
            result=interpret_summary(sink.finish(),config.source_kind);sink.close();sink=None
            output.close();output=None
            Path(job['output_path']).with_suffix('.partial.jsonl').replace(job['output_path'])
            self.update(job_id,stage='finalizing')
        except BaseException as error:
            failure=error
            # A failing job never publishes a successful analysis. Its available
            # prefix and explicit failed health remain diagnostic artifacts only.
            if sink and current_time>=0 and (last_health is None or current_time>last_health):
                try:
                    health=self.base_record(job,video,current_time,'stream.health','failure')
                    health.update(state='disconnected',dropped_frames=0,reason=('cancelled' if isinstance(error,Cancelled) else 'inference_or_processing_error'))
                    sink.write_frame([health])
                    if output:output.write(canonical(health)+'\n')
                    sink.finish()
                except Exception:pass
            self.update(job_id,status='cancelled' if isinstance(error,Cancelled) else 'error',stage='failed',
                        error=f'{type(error).__name__}: {error}',artifacts_complete=False)
        finally:
            if output:output.close()
            if sink:sink.close()
            gpu={}
            if detector:
                try:gpu=detector.memory()
                except Exception:gpu={'gpu_peak_allocated_bytes':None,'gpu_peak_reserved_bytes':None}
                try:detector.close()
                except Exception as error:
                    if failure is None:
                        failure=error
                        self.update(job_id,status='error',error='Model cleanup failed: '+str(error),artifacts_complete=False)
            monitor.__exit__(None,None,None)
            metrics=monitor.report()|gpu|{'frames_decoded':frame_count,'inference_frames':inferences,
                                         'observed_records':observed,'predicted_records':predicted,
                                         'video_indexed_span_ms':video['indexed_span_ms'],
                                         'frame_dims_px':video['frame_dims_px'],'runtime':model_metrics}
            values={'metrics':metrics,'finished_at_utc':processing_time()}
            if failure is None:
                # Publish only after model cleanup succeeds. A cleanup failure
                # must remain retryable without a stale successful analysis row.
                try:
                    analysis_id=stable_id(['p2',job_id,job['geometry_id']])
                    with self.catalog.connect(write=True) as db:
                        db.execute('INSERT INTO analyses VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                                   'path=excluded.path,summary=excluded.summary',
                                   (analysis_id,job['video_id'],job['geometry_id'],job['database_path'],canonical(job['p0_config']),canonical(result),processing_time()))
                    values.update(status='completed',stage='completed',progress=1.,artifacts_complete=True,
                                  analysis_id=analysis_id,summary=result)
                except Exception as error:
                    values.update(status='error',stage='failed',error='Analysis publication failed: '+str(error),artifacts_complete=False)
            final=self.update(job_id,**values)
            (Path(job['folder'])/'run-report.json').write_text(json.dumps(final,indent=2)+'\n',encoding='utf-8')
            self.cancellations.pop(job_id,None)

    def replay(self,job_id):
        with self.lock:
            job=self.get(job_id)
            if job['status']!='completed':raise ValueError('Only completed outputs can be replayed')
            result=interpret_summary(replay_output(job['output_path'],job['database_path'],job['p0_config']),job['config'].get('source_kind','continuous'))
            with self.catalog.connect(write=True) as db:
                db.execute('UPDATE analyses SET summary=? WHERE id=?',(canonical(result),job['analysis_id']))
            return result

    def shutdown(self):
        with self.lock:
            self.closed=True
            for signal in list(self.cancellations.values()):signal.set()
        self.executor.shutdown(wait=True,cancel_futures=True)
