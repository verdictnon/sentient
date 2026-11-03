"""Annotation-free proposals, exact PTS binding and bounded offline model inference."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import resource
import time

import av
import numpy as np
from PIL import Image

from .contracts import Validator, canonical, track_key
from .model import CLASSES, sha256

VERSION = 'sentient.action.worker/1'


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Policy:
    source_kind: str = 'continuous'
    window_ms: float = 2600.
    stride_ms: float = 1300.
    window_frames: int = 13
    stride_frames: int = 6
    max_gap_ms: float = 1500.
    min_unique_frames: int = 4
    batch_size: int = 2
    max_candidates: int = 10000
    max_frames: int = 1000000
    max_active_windows: int = 64

    def __post_init__(self):
        if self.source_kind not in ('continuous','sparse_clip'):
            raise ValueError('Declare continuous or sparse_clip; never infer physical cadence from an MP4')
        for name in ('window_ms','stride_ms','max_gap_ms'):
            value = getattr(self,name)
            if isinstance(value,bool) or not math.isfinite(value) or value<=0:
                raise ValueError(f'{name} must be finite and positive')
        for name in ('window_frames','stride_frames','min_unique_frames','batch_size','max_candidates','max_frames','max_active_windows'):
            value = getattr(self,name)
            if type(value) is not int or value<=0:
                raise ValueError(f'{name} must be a positive integer')
        if self.window_frames<2 or self.min_unique_frames<2 or self.min_unique_frames>13:
            raise ValueError('A candidate needs 2..13 unique input frames')
        if self.stride_frames>self.window_frames or self.stride_ms>self.window_ms:
            raise ValueError('Stride must not leave gaps between proposed windows')
        if self.batch_size>16:
            raise ValueError('Batch size is bounded to 16; use 1-2 on a 6GB GPU')


def index_video(path, max_frames=1000000):
    rows, dimensions = [], None
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_count = 1
        for i, frame in enumerate(container.decode(stream)):
            if i>=max_frames:
                raise ValueError('Video exceeds configured frame-index bound')
            if frame.pts is None or frame.time_base is None:
                raise ValueError('Video contains a frame without source PTS')
            dims = [frame.width, frame.height]
            if dimensions is not None and dims!=dimensions:
                raise ValueError('Frame dimensions changed during the recording')
            dimensions = dims
            ms = float(frame.pts*frame.time_base*1000)
            if ms<0 or (rows and ms<=rows[-1]['media_ms']):
                raise ValueError('Source presentation times must be nonnegative and strictly increasing')
            rows.append({'frame_index':i,'pts':frame.pts,
                'time_base':[frame.time_base.numerator,frame.time_base.denominator],'media_ms':ms})
    if len(rows)<2:
        raise ValueError('At least two decodable source frames are required')
    return {'frame_dims_px':dimensions,'frame_count':len(rows),'frames':rows}


def validate_binding(records, index):
    for row in records:
        if row['type']!='track.observed':continue
        i = row['frame_index']
        if i>=index['frame_count'] or row['frame_dims_px']!=index['frame_dims_px']:
            raise ValueError('Tracking dimensions/frame index do not match the video')
        if abs(row['media_time_ms']-index['frames'][i]['media_ms'])>.001:
            raise ValueError('Tracking time disagrees with source PTS (tolerance 0.001ms); do not round or assume FPS')


def segments(records, policy):
    """Predictions are excluded; source gaps and non-running health break a track."""
    grouped = defaultdict(list)
    health = defaultdict(list)
    for row in records:
        if row['type']=='stream.health' and row['state']!='healthy':
            health[(row['store_id'],row['camera_id'],row['stream_session_id'])].append(row['media_time_ms'])
        if row['type']=='track.observed' and row['position_status']=='observed':
            grouped[track_key(row)].append(row)
    for key, rows in sorted(grouped.items()):
        chunk = []
        for row in rows:
            discontinuity = chunk and (
                policy.source_kind=='continuous' and row['media_time_ms']-chunk[-1]['media_time_ms']>policy.max_gap_ms
                or any(chunk[-1]['media_time_ms']<t<=row['media_time_ms'] for t in health[key[:3]]))
            if discontinuity:
                yield key, chunk
                chunk = []
            chunk.append(row)
        if chunk:yield key, chunk


def nearest_rows(rows, count=13):
    times = np.array([r['media_time_ms'] for r in rows])
    targets = np.linspace(times[0],times[-1],count)
    return [rows[int(np.abs(times-target).argmin())] for target in targets]


def propose(records, index, media_sha, policy):
    """Only tracks/timing enter here: no source action labels or action locations."""
    validate_binding(records,index)
    plans = []
    observations_sha = digest(records)
    for key, rows in segments(records,policy):
        windows = []
        if len(rows)<2:continue
        if policy.source_kind=='sparse_clip':
            n = len(rows)
            starts = list(range(0,max(1,n-policy.window_frames+1),policy.stride_frames))
            if n>policy.window_frames and starts[-1]!=n-policy.window_frames:
                starts.append(n-policy.window_frames)
            for start in starts:
                part = rows[start:start+policy.window_frames]
                selected = [part[int(i)] for i in np.linspace(0,len(part)-1,13).round().astype(int)]
                windows.append(selected)
        else:
            times=[r['media_time_ms'] for r in rows]
            first,last = times[0],times[-1]
            start = first
            while start<last:
                part = rows[bisect_left(times,start):bisect_right(times,start+policy.window_ms)]
                if len(part)>=2:windows.append(nearest_rows(part))
                if start+policy.window_ms>=last:break
                start += policy.stride_ms
        for selected in windows:
            unique = {r['frame_index']:r for r in selected}
            values = list(unique.values())
            if len(values)<2:continue
            x1=min(r['bbox_xyxy_px'][0] for r in values);y1=min(r['bbox_xyxy_px'][1] for r in values)
            x2=max(r['bbox_xyxy_px'][2] for r in values);y2=max(r['bbox_xyxy_px'][3] for r in values)
            width,height=index['frame_dims_px']
            side=max(x2-x1,y2-y1,min(width,height)*.25)*1.4
            cx,cy=(x1+x2)/2,(y1+y2)/2
            crop=[round(cx-side/2),round(cy-side/2),round(cx+side/2),round(cy+side/2)]
            input_indices=[r['frame_index'] for r in selected]
            identity={'worker':VERSION,'policy':asdict(policy),'media_sha256':media_sha,
                      'observations_sha256':observations_sha,'track_key':key,
                      'model_input_indices':input_indices,'crop':crop}
            candidate_id='proposal:'+digest(identity)
            candidate={
                'schema_version':'1.0','type':'action.candidate','record_id':candidate_id,
                'candidate_id':candidate_id,'store_id':key[0],'camera_id':key[1],
                'stream_session_id':key[2],'local_track_id':key[3],
                'global_visit_id':None,'media_time_ms':values[-1]['media_time_ms'],
                'capture_time_utc':None,'clock_status':'playback_only',
                'start_media_ms':values[0]['media_time_ms'],'end_media_ms':values[-1]['media_time_ms'],
                'frame_indices':list(unique),'frame_media_times_ms':[r['media_time_ms'] for r in values],
                'capture_elapsed_ms':([r['media_time_ms']-index['frames'][0]['media_ms'] for r in values]
                    if policy.source_kind=='continuous' else None),
                'sampling_time_known':policy.source_kind=='continuous','source_kind':policy.source_kind,
                'video_ref':'asset://sha256/'+media_sha,'observations_ref':'asset://sha256/'+observations_sha,
            }
            # Continuous media deltas describe elapsed source time, not camera UTC.
            # Sparse exports explicitly retain unknown original capture cadence.
            plans.append({'candidate':candidate,'crop':crop,'model_input_indices':input_indices,
                          'unique_frames':len(values),'identity':identity})
            if len(plans)>policy.max_candidates:
                raise ValueError('Proposal count exceeds configured bound; split the video or raise the explicit limit')
    return sorted(plans,key=lambda p:(max(p['model_input_indices']),p['candidate']['candidate_id']))


def crop_tensor(frames, crop, indices):
    """Small reference helper also used by parity checks; frames are RGB arrays."""
    images={i:np.asarray(Image.fromarray(frames[i]).crop(crop).resize((160,160),Image.Resampling.BILINEAR)).copy() for i in set(indices)}
    return normalize(np.stack([images[i] for i in indices]))


def normalize(array):
    return ((array.transpose(3,0,1,2).astype(np.float32)/255-.45)/.225)


def score_record(plan, model_id, scores, quality, reason):
    candidate = plan['candidate']
    base={key:candidate[key] for key in ('store_id','camera_id','stream_session_id','local_track_id',
        'global_visit_id','media_time_ms','capture_time_utc','clock_status','candidate_id','start_media_ms','end_media_ms')}
    return {**base,'schema_version':'1.0','type':'action.scored',
        'record_id':'scored:'+digest([candidate['candidate_id'],model_id]),'model_id':model_id,
        'action':'unknown','task':'window_classification','scores':scores,'abstained':True,
        'quality':quality,'reason':reason,'score_semantics':'softmax_single_label' if scores is not None else 'unavailable',
        'evidence_ref':'asset://candidate/'+candidate['candidate_id'].split(':',1)[1]}


def infer_video(video, index, plans, runtime, policy, cancel_check=None, progress=None):
    """Decode in presentation order; retain only resized frames for active windows."""
    wanted=defaultdict(list)
    buffers={}
    ready, results, details = [], {}, {}
    model_id=runtime.metadata['model_id']+':'+digest({'policy':asdict(policy),'device':runtime.device,
        'torch':runtime.torch.__version__,'model_sha256':runtime.metadata['model_sha256'],'worker_source':sha256(Path(__file__))})[:16]
    for number,plan in enumerate(plans):
        if plan['unique_frames']<policy.min_unique_frames:
            row=score_record(plan,model_id,None,'insufficient_visibility','Fewer than the required unique observed subject frames; inference was not run.')
            results[number]=row;details[number]={'status':'insufficient_frames','raw_top_class':None,'scores':None}
            continue
        for i in set(plan['model_input_indices']):wanted[i].append(number)
    peak_active=0
    started=time.monotonic()

    def flush():
        if cancel_check:cancel_check()
        if not ready:return
        slots, arrays = zip(*ready)
        values=runtime.predict(np.stack(arrays))
        for number, score in zip(slots, values):
            scores={name:round(float(value),7) for name,value in zip(CLASSES,score)}
            quality='time_unknown' if policy.source_kind=='sparse_clip' else 'valid'
            reason='Uncalibrated model scores for human review only; no action is confirmed. '+(
                'Sparse export: original capture cadence unknown.' if policy.source_kind=='sparse_clip' else
                'Continuous detector-box crops are an unvalidated transfer from sparse pose-crop training.')
            row=score_record(plans[number],model_id,scores,quality,reason)
            results[number]=row
            raw_top=CLASSES[int(np.argmax(score))]
            entropy=float(-np.sum(score*np.log(np.clip(score,1e-12,1)))/math.log(4))
            details[number]={'status':'scored','raw_top_class':raw_top,'scores':scores,
                'normalized_entropy':entropy,'margin':float(np.sort(score)[-1]-np.sort(score)[-2])}
        ready.clear()

    try:
        if wanted:
            end=max(wanted)
            with av.open(str(video)) as container:
                stream=container.streams.video[0];stream.thread_count=1
                for i,frame in enumerate(container.decode(stream)):
                    if cancel_check:cancel_check()
                    if progress:progress((i+1)/(end+1))
                    if i in wanted:
                        if frame.pts!=index['frames'][i]['pts'] or [frame.time_base.numerator,frame.time_base.denominator]!=index['frames'][i]['time_base']:
                            raise ValueError('Source timing changed between indexing and inference')
                        image=frame.to_image()
                        for number in wanted[i]:
                            plan=plans[number]
                            if number not in buffers and len(buffers)>=policy.max_active_windows:
                                raise ValueError('Active window memory bound exceeded; reduce concurrent tracks or window overlap')
                            buffer=buffers.setdefault(number,{})
                            buffer[i]=np.asarray(image.crop(plan['crop']).resize((160,160),Image.Resampling.BILINEAR)).copy()
                            if i==max(plan['model_input_indices']):
                                ready.append((number,normalize(np.stack([buffer[j] for j in plan['model_input_indices']]))))
                                del buffers[number]
                                if len(ready)>=policy.batch_size:flush()
                        peak_active=max(peak_active,len(buffers))
                    if i>=end:break
            flush()
        if len(results)!=len(plans):raise ValueError('Video ended before all requested frames decoded')
    except (RuntimeError,ValueError) as error:
        # No partially successful run is written by run_video; caller can retry safely.
        raise ValueError('Inference failed; output was not committed: '+str(error)) from error
    output=[]
    for number,plan in enumerate(plans):
        details[number].update({'candidate_id':plan['candidate']['candidate_id'],'model_id':model_id,
            'track_key':list(track_key(plan['candidate'])),'start_media_ms':plan['candidate']['start_media_ms'],
            'end_media_ms':plan['candidate']['end_media_ms'],'model_input_indices':plan['model_input_indices'],
            'crop_xyxy_px':plan['crop'],'unique_frames':plan['unique_frames'],
            'evidence_ref':results[number]['evidence_ref']})
        output.extend([plan['candidate'],results[number]])
    return output,[details[i] for i in range(len(plans))],{
        'inference_and_decode_seconds':time.monotonic()-started,'peak_active_windows':peak_active,
        'peak_cuda_allocated_mib':runtime.memory_mib(),'process_peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        'scored_windows':sum(d['status']=='scored' for d in details.values()),
        'abstained_windows':len(plans),'confirmed_actions':0,'model_id':model_id,
    }


def run_video(video, tracks, model, output, policy, runtime=None, *, index=None, cancel_check=None, progress=None):
    from .model import Runtime
    video,tracks,output = Path(video).resolve(),Path(tracks).resolve(),Path(output).resolve()
    if output.exists():raise ValueError('Use a fresh output directory; existing evidence is never overwritten')
    started=time.monotonic()
    validator=Validator()
    records=validator.read_tracks(tracks)
    if cancel_check:cancel_check()
    index=index_video(video,policy.max_frames) if index is None else index
    if not 2<=index['frame_count']<=policy.max_frames or index['frame_count']!=len(index['frames']):
        raise ValueError('Provided frame index exceeds bounds or is incomplete')
    validate_binding(records,index)
    media_sha=sha256(video)
    plans=propose(records,index,media_sha,policy)
    runtime=runtime or Runtime(model)
    generated,details,performance=infer_video(video,index,plans,runtime,policy,cancel_check,progress)
    for row in generated:validator.validate(row)
    if media_sha!=sha256(video):raise ValueError('Video changed during inference')
    if cancel_check:cancel_check()
    output.mkdir(parents=True)
    (output/'records.jsonl').write_text(''.join(canonical(row)+'\n' for row in generated))
    (output/'evidence.json').write_text(json.dumps({'format':VERSION,'video_path':str(video),
        'media_sha256':media_sha,'tracks_path':str(tracks),'tracks_file_sha256':sha256(tracks),
        'policy':asdict(policy),'frame_index':index,'windows':details},indent=2)+'\n')
    summary={'format':VERSION,'video_path':str(video),'media_sha256':media_sha,'source_kind':policy.source_kind,
        'observations':sum(r['type']=='track.observed' and r['position_status']=='observed' for r in records),
        'predictions_excluded':sum(r['type']=='track.observed' and r['position_status']=='predicted' for r in records),
        'candidates':len(plans),'device':runtime.device,'policy':asdict(policy),**performance,
        'elapsed_total_seconds':time.monotonic()-started,'records_sha256':sha256(output/'records.jsonl'),
        'execution_context':getattr(runtime,'execution_context','standalone_worker'),
        'memory_scope':('Application process high-watermarks, including cached models' if getattr(runtime,'execution_context',None)=='integrated_application' else 'Standalone worker process / PyTorch allocator high-watermarks'),
        'warning':'Real model outputs, not verified action labels; not theft detection. Track producer/identity quality is external.'}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary
