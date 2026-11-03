"""Run a small curated demo set through the actual application, not mock scores."""
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8766'


def api(path,body=None):
    request=urllib.request.Request(BASE+path,json.dumps(body).encode() if body is not None else None,{'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=45) as response:return json.load(response)


def run():
    for attempt in range(50):
        try:api('/api/status');break
        except OSError:
            if attempt==49:raise
            time.sleep(.1)
    examples=[('000088','rank1','annotated_take'),('001050','rank1','annotated_take'),
        ('000179','rank1','annotated_take'),('000142','rank0','annotated_put_no_take')]
    cases=[]
    for sample,view,kind in examples:
        cases.append({'path':str(ROOT/f'data/retailaction_v2/validation/{sample}/{view}_video.mp4'),
            'store_id':'retailaction-pickup-demo','camera_id':f'validation-{sample}-{view}',
            'stream_session_id':f'pickup-demo-{sample}-{view}','collection_session':f'retailaction/validation/{sample}',
            'source_kind':'sparse_clip','reference':kind})
    receipt=json.loads((ROOT/'runs/full_audit_2026-11-03/hd-import-receipt.json').read_text())
    hd=next(x for x in receipt if x['native_activity']=='Picking and Putting')
    cases.append({'video_id':hd['video_id'],'source_kind':'continuous','reference':'CARR coarse picking activity; exact event times not annotated'})
    output=[]
    for case in cases:
        reference=case.pop('reference');kind=case.pop('source_kind')
        video=api('/api/videos/'+case['video_id']) if 'video_id' in case else api('/api/videos/import',case)
        job=api('/api/action-analysis/videos/'+video['id'],{'source_kind':kind,
            'tracking_config':{'detector':'rtdetrv2_r34','device':'auto','min_person_area_ratio':.005,'person_nms_iou':.4}})
        while job['status'] in ('queued','running','cancelling'):
            time.sleep(.2);job=api('/api/action-analysis/jobs/'+job['id'])
        if job['status']!='completed':raise RuntimeError(job.get('error'))
        pickups=api('/api/action-analysis/analyses/'+job['analysis_id']+'/pickups')
        paths=api('/api/tracking/jobs/'+job['tracking_job_id']+'/paths')
        link=BASE+'/?video='+video['id']+'&analysis='+job['analysis_id']+'&tracking_job='+job['tracking_job_id']
        output.append({'reference':reference,'video_id':video['id'],'job_id':job['id'],
            'analysis_id':job['analysis_id'],'tracking_job_id':job['tracking_job_id'],'review_url':link,
            'pickup_events':pickups['events'],'path_tracks':len(paths['tracks']),
            'observed_path_points':sum(len(s) for t in paths['tracks'] for s in t['segments']),
            'seconds':job['elapsed_seconds'],'action_runtime_cache_hit':job.get('action_runtime_cache_hit')})
        print(reference,video['display_name'],'pickups',len(pickups['events']),'paths',len(paths['tracks']),'seconds',round(job['elapsed_seconds'],2),flush=True)
        (ROOT/'runs/take_demo_v1/demo-suite.json').write_text(json.dumps({'selection':'Curated demonstration, not an independent accuracy test','cases':output},indent=2)+'\n')
    default=next((x for x in output if x['reference'].startswith('CARR') and len(x['pickup_events'])==1),next(x for x in output if x['reference']=='annotated_take' and len(x['pickup_events'])==1))
    config=json.loads((ROOT/'demo_config.json').read_text())
    config['default_demo']={k:default[k] for k in ['video_id','analysis_id','tracking_job_id']}
    config.setdefault('demo_library',[x['video_id'] for x in output])
    (ROOT/'demo_config.json').write_text(json.dumps(config,indent=2)+'\n')
    (ROOT/'runs/take_demo_v1/demo-link.txt').write_text(default['review_url']+'\n')
    print('Default pickup demo:',default['review_url'],flush=True)


if __name__=='__main__':run()
