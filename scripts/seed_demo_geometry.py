"""Save hand-drawn floor approach areas for the seven CARR demo recordings.
Coordinates were drawn from the actual 1280x720 source views, not generated
entrance/checkout guesses. Re-running never overwrites a customized revision.
"""
import json
from pathlib import Path
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8766'

def request(path,body=None):
    req=urllib.request.Request(URL+path,data=json.dumps(body).encode() if body is not None else None,
         headers={'Content-Type':'application/json'},method='PUT' if body is not None else 'GET')
    return json.load(urllib.request.urlopen(req,timeout=60))

SCENES={
 'kpri': [('Left aisle shelves',[[235,720],[420,720],[395,455],[345,275],[300,315]]),
          ('Right aisle shelves',[[790,720],[1060,720],[810,335],[715,255],[680,335]])],
 'alif': [('Right aisle shelves',[[720,720],[905,720],[970,430],[925,345],[795,340]])],
 'budi': [('Counter-side shelf approach',[[575,720],[800,720],[845,350],[760,330],[690,435]])],
 'unej': [('Right aisle shelves',[[800,620],[1055,620],[980,360],[830,130],[750,130]]),
          ('Left aisle shelves',[[245,700],[430,700],[495,60],[440,60]])],
}

def main():
    receipts=json.loads((ROOT/'runs/full_audit_2026-11-03/hd-import-receipt.json').read_text())
    mapping={r['video_id']:('kpri' if r['source_video_id'] in ('V0761','V0762','V0765') else 'alif' if r['source_video_id']=='V0075' else 'budi' if r['source_video_id']=='V0097' else 'unej') for r in receipts}
    all_videos=request('/api/videos')
    for v in all_videos:
        if v.get('collection_session','').endswith('V0797-long'):mapping[v['id']]='unej'
    receipt=[]
    for video_id,scene in mapping.items():
        saved=request(f'/api/videos/{video_id}/geometry')
        if saved['geometry']['geometry_version']==2 and saved['geometry']['shapes']:
            print('Preserved existing v2 geometry',video_id);continue
        shapes=[{'id':'shelf-'+str(i+1),'kind':'shelf','name':name,'points':points} for i,(name,points) in enumerate(SCENES[scene])]
        payload={'geometry_version':2,'frame_dims_px':[1280,720],'max_gap_ms':1500,'shapes':shapes,'active':dict(line=None,zone=None,queue=None,checkout=None)}
        result=request(f'/api/videos/{video_id}/geometry',payload);receipt.append({'video_id':video_id,'scene':scene,'geometry_id':result['id'],'geometry':payload})
    (ROOT/'runs/demo_live_v2/geometry-seed.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('Saved',len(receipt),'scene geometry revisions; entrances and checkouts are not visible and remain unconfigured.')
if __name__=='__main__':main()
