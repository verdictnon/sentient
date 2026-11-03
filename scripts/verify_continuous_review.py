"""Actual continuous P2 + action output -> isolated pending review -> v1 export."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import time

from jsonschema import Draft202012Validator, FormatChecker
import review_bridge as b


def run(milestone_path, output, app_root, app_python):
    m=b.read_json(milestone_path);source=Path(milestone_path).parent
    output=Path(output).resolve();app_root=Path(app_root).resolve()
    if output.exists():raise b.BridgeError('Use a fresh verification directory')
    output.mkdir(parents=True)
    rows=b.read_jsonl(source/'combined.jsonl')
    scored=next(r for r in rows if r['type']=='action.scored' and r['scores'])
    subject=[scored[k] for k in ['store_id','camera_id','stream_session_id','local_track_id']]
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    with (output/'server.log').open('w') as log:
        proc=subprocess.Popen([str(Path(app_python).absolute()),'-m','app.p1.server','--port',str(port),
            '--data-dir',str(output/'workspace')],cwd=app_root,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+20
            while True:
                try:
                    with b.client(url) as api:b.response_json(api.get('/api/status'))
                    break
                except Exception:
                    if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Server failed')
                    time.sleep(.1)
            with b.client(url) as api:
                video=b.response_json(api.post('/api/videos/import',json={'path':m['source_video'],
                    'store_id':subject[0],'camera_id':subject[1],'stream_session_id':subject[2],
                    'collection_session':'AUTOMATED_CONTINUOUS_FIXTURE'}))
                replay=b.response_json(api.post('/api/replays',data={'video_id':video['id'],'isolated_run':'true'},
                    files={'file':('combined.jsonl',(source/'combined.jsonl').read_bytes(),'application/x-ndjson')}))
                body={'analysis_id':replay['id'],'target_id':'record:'+scored['record_id'],
                    'request_id':'continuous-pending-fixture','expected_revision':0,
                    'reviewer':'AUTOMATED CONTINUOUS ROUNDTRIP FIXTURE','decision':'pending','label':'unknown',
                    'visibility':'unknown','uncertainty':'high','notes':'Fixture only; no human label or action confirmation.',
                    'start_media_ms':scored['start_media_ms'],'end_media_ms':scored['end_media_ms']}
                saved=b.response_json(api.post(f'/api/videos/{video["id"]}/reviews',json=body))
                export=api.get('/api/annotations/export',params={'video_id':video['id']})
                export.raise_for_status();(output/'reviews.jsonl').write_text(export.text)
            reviews=b.read_jsonl(output/'reviews.jsonl');assert len(reviews)==1
            r=reviews[0]
            check=Draft202012Validator(b.read_json(app_root/'app/p1/annotation.schema.json'),format_checker=FormatChecker())
            check.validate(r)
            assert r['human']['decision']=='pending' and r['human']['label']=='unknown'
            assert r['model_outputs']==[scored] and r['subject_track_key']==subject
            assert r['media_sha256']==m['media_sha256'] and r['capture_time_utc'] is None
            assert any(x['type']=='action.candidate' and x['candidate_id']==scored['candidate_id'] for x in r['source_records'])
            proof={'continuous_original_tracking_and_real_scores_exported':True,
                'actual_model_output_preserved':True,'original_v1_review_schema_valid':True,
                'pending_unknown_fixture_only':True,'human_adjudications_performed':0,
                'training_eligible_labels_created':0,'source_media_sha256':m['media_sha256'],
                'source_combined_records_sha256':b.sha256(source/'combined.jsonl'),
                'annotation_id':saved['annotation_id'],'export_sha256':b.sha256(output/'reviews.jsonl')}
            b.write_json(output/'verification.json',proof)
            return proof
        finally:proc.terminate();proc.wait(timeout=10)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--milestone',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--app-root',type=Path,required=True)
    p.add_argument('--app-python',type=Path,default=b.ROOT/'.venv-app/bin/python');a=p.parse_args()
    print(json.dumps(run(a.milestone,a.output,a.app_root,a.app_python),indent=2))
