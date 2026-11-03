"""One local MP4 -> partner P2 tracks -> action scores -> replayable review evidence.

This runner composes the existing APIs. It does not train, invent payment status,
change transport schemas, or certify model outputs as human labels.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode

import review_bridge as b


def run(video, output, app_url, model, source_kind, model_python, geometry=None, tracking_config=None):
    video,output,model=Path(video).resolve(),Path(output).resolve(),Path(model).resolve()
    if output.exists():raise b.BridgeError('Use a fresh output directory; past runs are immutable')
    if source_kind not in ('continuous','sparse_clip'):raise b.BridgeError('Declare source kind explicitly')
    output.mkdir(parents=True)
    source_hash=b.sha256(video)
    session='diagnostic-'+source_hash[:16]
    start=time.monotonic()
    with b.client(app_url) as api:
        deadline=time.monotonic()+20
        while True:
            try:
                status=b.response_json(api.get('/api/status'));break
            except Exception:
                if time.monotonic()>deadline:raise b.BridgeError('The local application is not reachable; start it before running the pipeline')
                time.sleep(.1)
        if not status.get('isolated_replays'):raise b.BridgeError('Use the integrated application with isolated replay support')
        registered=b.response_json(api.post('/api/videos/import',json={'path':str(video),
            'store_id':'public-research-diagnostic','camera_id':'external-continuous',
            'stream_session_id':session,'collection_session':'external-diagnostic/'+source_hash[:16],
            'collection_day':None}))
        if registered['media_sha256']!=source_hash:raise b.BridgeError('Registered media hash mismatch')
        if geometry:
            saved=b.response_json(api.put(f'/api/videos/{registered["id"]}/geometry',json=b.read_json(geometry)))
        else:
            saved=b.response_json(api.get(f'/api/videos/{registered["id"]}/geometry'))
        config={'device':'auto','sample_period_ms':200}
        if tracking_config:config.update(b.read_json(tracking_config))
        config['source_kind']=source_kind
        job=b.response_json(api.post(f'/api/tracking/videos/{registered["id"]}',
            json={'geometry_id':saved['id'],'config':config}))
        last_stage=None
        while job['status'] in ('queued','running','cancelling'):
            stage=(job['status'],job.get('stage',job['status']),int(job['progress']*10))
            if stage!=last_stage:print(f'Tracking: {job["status"]} / {job.get("stage",job["status"])} / {job["progress"]:.0%}',flush=True);last_stage=stage
            time.sleep(.3)
            job=b.response_json(api.get(f'/api/tracking/jobs/{job["id"]}'))
        b.write_json(output/'tracking-job.json',job)
        if job['status']!='completed':raise b.BridgeError('Tracking failed: '+str(job.get('error')))
        response=api.get(f'/api/tracking/jobs/{job["id"]}/output')
        if response.is_error:raise b.BridgeError('Completed tracking output unavailable')
        track_path=output/'tracks.jsonl';track_path.write_bytes(response.content)
        replay=b.response_json(api.post(f'/api/tracking/jobs/{job["id"]}/replay'))
        if replay['records_inserted'] or replay['events_added']:raise b.BridgeError('Tracking replay is not idempotent')
        command=[str(Path(model_python).absolute()),str(b.ROOT/'scripts/action_worker.py'),'run',
            '--video',str(video),'--tracks',str(track_path),'--model',str(model),
            '--output',str(output/'actions'),'--source-kind',source_kind]
        print('Running the action worker on observed tracks (predicted positions excluded).',flush=True)
        with (output/'action-worker.log').open('w') as log:
            subprocess.run(command,check=True,cwd=b.ROOT,stdout=log,stderr=log)
        actions=b.read_jsonl(output/'actions/records.jsonl')
        tracks=b.read_jsonl(track_path)
        combined=tracks+actions
        # Preserve every original P2 record, including health and prediction status.
        combined_path=output/'combined.jsonl';b.write_jsonl(combined_path,combined)
        combined_bytes=combined_path.read_bytes()
        data={'video_id':registered['id'],'geometry_id':saved['id'],'isolated_run':'true','tracking_job_id':job['id']}
        analysis=b.response_json(api.post('/api/replays',data=data,
            files={'file':('combined.jsonl',combined_bytes,'application/x-ndjson')}))
        retry=b.response_json(api.post('/api/replays',data=data,
            files={'file':('combined.jsonl',combined_bytes,'application/x-ndjson')}))
        if retry['summary']['records_inserted'] or retry['summary']['events_added']:
            raise b.BridgeError('Combined output retry duplicated evidence')
        for metric in ['inward_crossings','outward_crossings','complete_observed_dwell_ms']:
            if analysis['summary'][metric]!=job['summary'][metric]:raise b.BridgeError('Action composition changed physical tracking analytics')
        if analysis['summary']['confirmed_physical_actions']!=0:
            raise b.BridgeError('Review-only model suggestions became confirmed actions')
        link=app_url.rstrip('/')+'/?'+urlencode({'video':registered['id'],'analysis':analysis['id'],'tracking_job':job['id']})
        proof={'milestone':'recorded-video-to-real-tracks-to-real-action-scores-to-review/1',
            'video_id':registered['id'],'source_video':str(video),'media_sha256':source_hash,
            'source_kind':source_kind,'tracking_job_id':job['id'],'tracking_analysis_id':job['analysis_id'],
            'combined_analysis_id':analysis['id'],'geometry_id':saved['id'],
            'tracking_metrics':job['metrics'],'tracking_summary':job['summary'],
            'action_summary':b.read_json(output/'actions/summary.json'),
            'combined_summary':analysis['summary'],'tracking_retry_records_added':0,
            'combined_retry_records_added':0,'physical_analytics_unchanged':True,
            'all_original_p2_records_preserved':True,'combined_records_sha256':b.sha256(combined_path),
            'human_adjudications_performed':0,'training_eligible_labels_created':0,
            'elapsed_seconds':time.monotonic()-start,'review_url':link,
            'warning':'External diagnostic only. Source actions are suggestions; no payment, theft, or production accuracy is established.'}
        b.write_json(output/'milestone.json',proof)
        print(json.dumps(proof,indent=2))
        return proof


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--app-url',default='http://127.0.0.1:8767')
    p.add_argument('--model',type=Path,default=b.ROOT/'runs/action_worker_v1/model')
    p.add_argument('--model-python',type=Path,default=b.ROOT/'.venv/bin/python')
    p.add_argument('--source-kind',choices=['continuous','sparse_clip'],required=True)
    p.add_argument('--geometry',type=Path);p.add_argument('--tracking-config',type=Path)
    a=p.parse_args();run(a.video,a.output,a.app_url,a.model,a.source_kind,a.model_python,a.geometry,a.tracking_config)


if __name__=='__main__':main()
