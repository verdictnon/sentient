"""Run train-only public clips and produce a bridge-compatible scored review pack."""
from copy import deepcopy
import html
import json
import math
from pathlib import Path
import shutil
import time

import numpy as np
from PIL import Image, ImageDraw
import av

from .contracts import Validator, canonical
from .model import ROOT, Runtime, sha256
from .pipeline import Policy, digest, run_video


def pose_tracks(entry, root=ROOT):
    """Supplied subject poses -> box observations, NOT automatic detections."""
    pose_path=(root/entry['pose_ref']).resolve()
    video_path=(root/entry['video_ref']).resolve()
    if not pose_path.is_relative_to(root.resolve()) or not video_path.is_relative_to(root.resolve()):
        raise ValueError('Escaping source asset reference')
    if sha256(pose_path)!=entry['pose_sha256'] or sha256(video_path)!=entry['media_sha256']:
        raise ValueError('Source assets changed')
    poses=json.loads(pose_path.read_text())['frames']
    width,height=entry['media_index']['frame_dims_px']
    key=entry['subject_track_key']
    rows=[]
    for frame in entry['media_index']['frames']:
        pose=poses.get(str(frame['frame_index']+1),{})
        points=[xy for xy,c in zip(pose.get('points',[]),pose.get('confidence',[]))
            if isinstance(c,(int,float)) and c>=.2 and len(xy)==2 and
            all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=1 for v in xy)]
        if len(points)<2:continue
        x1=min(p[0] for p in points)*width;x2=max(p[0] for p in points)*width
        y1=min(p[1] for p in points)*height;y2=max(p[1] for p in points)*height
        if x1>=x2 or y1>=y2:continue
        rows.append({'schema_version':'1.0','type':'track.observed',
            'record_id':'supplied-pose:'+digest([entry['media_sha256'],entry['pose_sha256'],frame['frame_index'],key]),
            'store_id':key[0],'camera_id':key[1],'stream_session_id':key[2],
            'local_track_id':key[3],'global_visit_id':None,'association_status':'local_only',
            'media_time_ms':frame['media_ms'],'capture_time_utc':None,'clock_status':'playback_only',
            'frame_index':frame['frame_index'],'bbox_xyxy_px':[x1,y1,x2,y2],
            'frame_dims_px':[width,height],'score':1.,'position_status':'observed',
            'world_xy_m':None,'calibration_version':None,'pose_ref':'asset://sha256/'+entry['pose_sha256'],
            'model_id':'provided-subject-pose-export; not detector confidence'})
    validator=Validator()
    for row in rows:validator.validate(row)
    return rows


def ranking(entries, runs):
    groups={}
    for entry in entries:
        result=runs[entry['entry_id']]
        windows=result['evidence']['windows']
        scored=[w for w in windows if w['status']=='scored']
        for w in scored:
            # This is a review ordering rule; never a probability or action decision.
            w['review_priority']=.5*w['normalized_entropy']+.25*(1-w['margin'])+.25*max(w['scores']['put'],w['scores']['touch'])
        top=sorted(scored,key=lambda w:(-w['review_priority'],w['candidate_id']))[:3]
        item=groups.setdefault(entry['clip_group'],{'clip_group':entry['clip_group'],'views':[],
            'priority':0.,'reason':'Uncertainty / rare-class score. Scores are uncalibrated; source poses and labels need review.'})
        item['views'].append({'entry_id':entry['entry_id'],'camera_view':entry['camera_view'],
            'top_windows':top,'camera_label':entry['subject_track_key'][1],
            'preview_ref':entry['preview_ref'],'video_ref':entry['video_ref']})
        item['priority']=max(item['priority'],max((w['review_priority'] for w in top),default=0))
    return sorted(groups.values(),key=lambda g:(-g['priority'],g['clip_group']))


def write_index(output, queue, summary):
    cards=[]
    for number,group in enumerate(queue,1):
        views=[]
        for view in group['views']:
            windows=[]
            for w in view['top_windows']:
                scores=' · '.join(f'{html.escape(k)} {v:.3f}' for k,v in w['scores'].items())
                windows.append(f'<li>'+ (f'<a href="{html.escape(w["review_url"])}">Review this window</a><br>' if w.get('review_url') else '')+f'Frames {w["model_input_indices"][0]}–{w["model_input_indices"][-1]}: '
                    f'<b>{html.escape(w["raw_top_class"])}</b> raw top score<br><small>{scores}</small></li>')
            views.append(f'<div class="view"><h3>{html.escape(view["camera_view"])}</h3>'
                f'<img loading="lazy" src="{html.escape(view["preview_ref"])}" alt="Supplied subject pose preview">'
                f'<p>App camera: <code>{html.escape(view["camera_label"])}</code></p><ul>{"".join(windows)}</ul></div>')
        cards.append(f'<article><h2>{number:02d} · {html.escape(group["clip_group"])}</h2>'
            f'<p>Review priority {group["priority"]:.3f} · {html.escape(group["reason"])}</p><div class="views">{"".join(views)}</div></article>')
    (output/'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sentient · Real model review queue</title><style>
body{margin:0;background:#0c1210;color:#e3ece6;font:15px/1.6 system-ui}main{max-width:1200px;margin:auto;padding:36px 24px}h1{font-size:32px;margin:0}h2{font-size:21px}h3{font-size:16px}p,small{color:#a6b9ac}header,.stats,article{border:1px solid #334239;border-radius:12px;padding:24px;margin-bottom:22px;background:#141e18}.stats{display:flex;gap:35px;flex-wrap:wrap}.stats b{font-size:24px;color:#c8e9a1;display:block}.views{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}img{width:100%;border-radius:6px}ul{padding-left:20px}code{overflow-wrap:anywhere}a{color:#c8e9a1}.tag{color:#c8e9a1;font-size:12px;letter-spacing:1px}li{margin:12px 0}</style><main><header>
<div class="tag">REAL MODEL OUTPUT · HUMAN REVIEW REQUIRED</div><h1>A working action review pipeline.</h1>
<p>The exported X3D model scored fixed sliding windows without receiving action boundaries or contact locations. Subject boxes here come from the dataset's supplied poses; automatic people tracking is your partner's task.</p>
<p>Raw take / put / touch / background scores help order review. Every transport result remains <b>unknown / abstained</b>. These values are not calibrated probabilities of actions or theft. The preview is a subject-location aid; it does not mark a model-detected interaction.</p>
<p>Open the matching camera in the <a href="http://127.0.0.1:8767">isolated scored-review workspace</a>, select an event, inspect the footage and save your own decision. No paired-view synchronization is implied.</p></header>'''+
        '<div class="stats">'+''.join(f'<div><b>{html.escape(str(value))}</b>{html.escape(label)}</div>' for label,value in [
            ('source training groups',summary['groups']),('public video views',summary['views']),
            ('real scored windows',summary['scored_windows']),('human labels fabricated',0)])+'</div>'+''.join(cards)+'<footer><p>Research data: RetailAction, Standard Cognition. Source labels and supplied poses remain unverified. See <a href="RETAILACTION_LICENSE.txt">dataset terms</a> and <a href="NOTICE.txt">attribution</a>.</p></footer></main></html>')


def build_pack(base_pack_path, model_dir, output, device='auto', limit_groups=None):
    # The existing bridge enforces authoritative split roles and source checksums.
    import review_bridge as bridge
    source=bridge.load_pack(base_pack_path)
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a fresh scored-pack output directory')
    started=time.monotonic()
    runtime=Runtime(model_dir,device)
    policy=Policy(source_kind='sparse_clip')
    groups=list(dict.fromkeys(e['clip_group'] for e in source['entries']))
    if limit_groups is not None:groups=groups[:limit_groups]
    selected=[e for e in source['entries'] if e['clip_group'] in groups]
    output.mkdir(parents=True)
    (output/'records').mkdir();(output/'previews').mkdir();(output/'tracks').mkdir()
    entries,runs=[],{}
    for n,base in enumerate(selected,1):
        entry=deepcopy(base)
        tracks=pose_tracks(entry)
        track_path=output/'tracks'/f'{entry["entry_id"]}.jsonl'
        track_path.write_text(''.join(canonical(r)+'\n' for r in tracks))
        run_dir=output/'inference'/entry['entry_id']
        summary=run_video(ROOT/entry['video_ref'],track_path,model_dir,run_dir,policy,runtime)
        generated=bridge.read_jsonl(run_dir/'records.jsonl')
        evidence=json.loads((run_dir/'evidence.json').read_text())
        entry['targets']=[{'target_id':'record:'+r['record_id'],'candidate_id':r['candidate_id'],
            'source_event_index':None,'suggested_label_unverified':next(w['raw_top_class'] or 'unknown' for w in evidence['windows'] if w['candidate_id']==r['candidate_id']),
            'suggestion_origin':'uncalibrated real model; not human adjudication'} for r in generated if r['type']=='action.scored']
        entry['wire_records']=generated
        entry['review_reasons']=['Fixed sliding-window model triage; no annotation boundaries entered inference.']
        # Preview media is inherited as an explicitly unverified subject aid.
        shutil.copy2(Path(base_pack_path).parent/entry['preview_ref'],output/entry['preview_ref'])
        (output/entry['records_ref']).write_text(''.join(canonical(r)+'\n' for r in generated))
        entries.append(entry);runs[entry['entry_id']]={'summary':summary,'evidence':evidence}
        print(f'Scored view {n}/{len(selected)}: {entry["clip_group"]}/{entry["camera_view"]} -> {summary["scored_windows"]} real windows',flush=True)
    spec={**source['identity'],'base_pack_sha256':sha256(base_pack_path),'worker_version':'sentient.action.worker/1',
          'worker_model_sha256':runtime.metadata['model_sha256'],'selected_groups':groups,
          'proposal_method':'fixed sliding sparse frame windows; no source action labels',
          'worker_pipeline_sha256':sha256(Path(__file__).with_name('pipeline.py'))}
    pack={'pack_version':bridge.VERSION,'pack_id':bridge.identity(spec),'identity':spec,'entries':entries,
        'entries_sha256':bridge.identity(entries),'groups':len(groups),'views':len(entries),
        'warning':'Real uncalibrated model scores from supplied subject crops. All results unknown/abstained. No human adjudication or automatic person detection.'}
    bridge.write_json(output/'pack.json',pack)
    shutil.copy2(Path(base_pack_path).parent/'review.schema.json',output/'review.schema.json')
    queue=ranking(entries,runs)
    result={'groups':len(groups),'views':len(entries),
        'scored_windows':sum(r['summary']['scored_windows'] for r in runs.values()),
        'candidates':sum(r['summary']['candidates'] for r in runs.values()),
        'elapsed_seconds':time.monotonic()-started,'device':runtime.device,
        'peak_cuda_allocated_mib':runtime.memory_mib(),
        'inference_and_decode_seconds':sum(r['summary']['inference_and_decode_seconds'] for r in runs.values()),
        'model_id':runtime.metadata['model_id'],'source_checkpoint_sha256':runtime.metadata['source_checkpoint_sha256'],
        'source_pack_sha256':sha256(base_pack_path),'worker_pack_sha256':sha256(output/'pack.json'),
        'human_labels_created':0,'training_eligible_labels_created':0,'new_model_training_runs':0,
        'inference_uses_action_boundaries':False,'automatic_person_detection':False,
        'scope':'Source-training review queue only; no model accuracy or continuous-store deployment claim.'}
    bridge.write_json(output/'summary.json',result)
    bridge.write_json(output/'priority_queue.json',{'queue_version':'sentient.action-triage/1','groups':queue})
    write_index(output,queue,result)
    bridge.load_pack(output/'pack.json')
    return result


def link_pack(output, receipt_path):
    """Bind review links to exact imported video/analysis/target IDs."""
    from urllib.parse import urlencode
    import review_bridge as bridge
    output=Path(output)
    pack=bridge.load_pack(output/'pack.json')
    receipt=bridge.read_json(receipt_path)
    if receipt['pack_sha256']!=sha256(output/'pack.json'):
        raise ValueError('Import receipt belongs to another scored pack')
    # Reuse the loopback-only URL rule before placing any links in the page.
    with bridge.client(receipt['app_url']):pass
    imported={e['entry_id']:e for e in receipt['entries']}
    entries={e['entry_id']:e for e in pack['entries']}
    queue=bridge.read_json(output/'priority_queue.json')
    for group in queue['groups']:
        for view in group['views']:
            item=imported[view['entry_id']]
            for window in view['top_windows']:
                target=next(t for t in entries[view['entry_id']]['targets'] if t['candidate_id']==window['candidate_id'])
                window['review_url']=receipt['app_url']+'/?'+urlencode({'video':item['video_id'],
                    'analysis':item['bindings'][-1]['analysis_id'],'target':target['target_id']})
    bridge.write_json(output/'priority_queue.json',queue)
    write_index(output,queue['groups'],bridge.read_json(output/'summary.json'))
    return len(imported)
