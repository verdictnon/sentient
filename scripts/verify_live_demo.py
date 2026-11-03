"""Run actual causal inference on every configured HD demo, then save a receipt."""
import json
from pathlib import Path
import statistics
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1];URL='http://127.0.0.1:8766'
def get(path,post=False):
    req=urllib.request.Request(URL+path,data=b'' if post else None,method='POST' if post else 'GET')
    return json.load(urllib.request.urlopen(req,timeout=60))
def main():
    results=[]
    for video_id in json.loads((ROOT/'demo_config.json').read_text())['demo_library']:
        v=get('/api/videos/'+video_id);s=get('/api/live/videos/'+video_id,True);key=s['id'];deadline=time.monotonic()+180
        while True:
            value=get('/api/live/sessions/'+key)
            if value['status'] in ('completed','stopped','error'):break
            if time.monotonic()>deadline:
                get('/api/live/sessions/'+key+'/stop',True);raise TimeoutError(key)
            time.sleep(.25)
        p=value['latest'];folder=ROOT/'runs/review_workspace/live'/key
        rows=[json.loads(line) for line in (folder/'observations.jsonl').read_text().splitlines()]
        causal=all(max(pred['input_frame_indices'])<=r['frame_index'] and pred['emitted_at_frame']==r['frame_index'] for r in rows for pred in r['predictions'])
        result={k:p.get(k) for k in ['status','error','processed_frames','scored_windows','elapsed_seconds','model_id','shelves','pickups']}
        result.update(video_id=video_id,name=v['display_name'],session_id=key,source_seconds=(v['last_media_time_ms']-v['first_media_time_ms'])/1000,
                      causal_inputs_verified=causal,inference_ms_median=round(statistics.median(r['inference_ms'] for r in rows),2) if rows else None,
                      lag_ms_p95=round(sorted(r['behind_source_ms'] for r in rows)[int((len(rows)-1)*.95)],2) if rows else None)
        results.append(result);print(v['display_name'],p['status'],p.get('processed_frames'),p.get('scored_windows'),'windows',len(p.get('pickups',[])),'alerts','causal',causal,flush=True)
        (ROOT/'runs/demo_live_v2/live-suite.json').write_text(json.dumps({'purpose':'Integration/causality check, not accuracy ground truth','cases':results},indent=2)+'\n')
        if p['status']!='completed' or not causal:raise RuntimeError(result)
    print('Verified',len(results),'real HD live runs.',flush=True)
if __name__=='__main__':main()
