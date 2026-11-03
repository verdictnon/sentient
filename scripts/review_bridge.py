"""Review queue -> unchanged P1 HTTP interfaces -> checked, masked supervision.

Run with .venv-app/bin/python scripts/review_bridge.py --help.
This tool owns no application source, trains no model and never edits labels.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import html
import json
import math
from pathlib import Path
import tempfile
from urllib.parse import urlparse

import av
import httpx
from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT
CLASSES = ['take', 'put', 'touch']
REVIEW_LABELS = CLASSES + ['background','unknown','inward','outward','zone_visit',
                           'queue_abandoned','service_completed','other']
VERSION = 'sentient.review-pack/1'


class BridgeError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identity(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise BridgeError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _constant(value):
    raise BridgeError(f'Nonfinite JSON: {value}')


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise BridgeError('Nonfinite numeric value')
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def parse(text):
    result = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    _finite(result)
    return result


def read_json(path):
    return parse(Path(path).read_text(encoding='utf-8-sig'))


def read_jsonl(path):
    result = []
    for line, text in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if text.strip():
            try:
                result.append(parse(text))
            except (ValueError, TypeError) as error:
                raise BridgeError(f'{path}:{line}: {error}') from error
    return result


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2, allow_nan=False) + '\n'
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     delete=False, suffix='.tmp') as stream:
        stream.write(data)
        tmp = Path(stream.name)
    tmp.replace(path)


def write_jsonl(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ''.join(canonical(v) + '\n' for v in values)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     delete=False, suffix='.tmp') as stream:
        stream.write(data)
        tmp = Path(stream.name)
    tmp.replace(path)


def local_path(root, ref):
    if not isinstance(ref, str) or '\\' in ref or ':' in ref or Path(ref).is_absolute():
        raise BridgeError('Asset references must be relative project paths')
    root = Path(root).resolve()
    path = (root / ref).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise BridgeError(f'Missing or escaping asset reference: {ref}')
    return path


def validator(path):
    schema = read_json(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_wire(row, check):
    _finite(row)
    try:
        check.validate(row)
    except Exception as error:
        raise BridgeError('Record violates original v1 schema') from error
    if row['clock_status'] == 'verified' and row['capture_time_utc'] is None:
        raise BridgeError('Verified clock requires actual capture UTC')
    if row['type'] in ['action.candidate', 'action.scored']:
        if row['start_media_ms'] > row['end_media_ms']:
            raise BridgeError('Reversed action interval')
    if row['type'] == 'action.candidate':
        indices, times = row['frame_indices'], row['frame_media_times_ms']
        if len(indices) != len(times) or any(b <= a for a,b in zip(indices, indices[1:])) or any(b <= a for a,b in zip(times, times[1:])):
            raise BridgeError('Candidate frame/time arrays disagree')
        if not row['start_media_ms'] <= times[0] <= times[-1] <= row['end_media_ms']:
            raise BridgeError('Candidate frames outside interval')
    if row['type'] == 'action.scored' and row['scores'] is None:
        if row['action'] != 'unknown' or not row['abstained'] or row['score_semantics'] != 'unavailable':
            raise BridgeError('Missing inference must be explicit')


def frame_index(path):
    frames, dims = [], None
    with av.open(str(path)) as video:
        if not video.streams.video:
            raise BridgeError('No video stream')
        stream = video.streams.video[0]
        for i, f in enumerate(video.decode(stream)):
            if f.pts is None or f.time_base is None:
                raise BridgeError('Frame lacks PTS/time base')
            exact = f.pts * f.time_base
            current = [f.width, f.height]
            if exact < 0 or (frames and exact <= Fraction(frames[-1]['pts']) * Fraction(*frames[-1]['time_base'])):
                raise BridgeError('Unsupported non-increasing/negative source PTS')
            if dims is not None and current != dims:
                raise BridgeError('Changing frame dimensions')
            dims = current
            frames.append({'frame_index': i, 'pts': f.pts,
                           'time_base': [f.time_base.numerator, f.time_base.denominator],
                           'media_ms': float(exact * 1000)})
    if len(frames) < 2:
        raise BridgeError('Review clips need at least two indexed frames')
    return {'frame_dims_px': dims, 'frames': frames, 'frame_count': len(frames)}


def make_preview(path, poses, annotations, target):
    """Review aid only: supplied subject poses; never a training oracle crop."""
    with av.open(str(path)) as video:
        frames = [f.to_image() for f in video.decode(video=0)]
    center = annotations[0]['start_frame'] if annotations else len(frames)//2
    indices = sorted(set([max(0, center-2), center, min(len(frames)-1, center+2)]))
    sheet = Image.new('RGB', (480*len(indices), 390), 'white')
    for column, index in enumerate(indices):
        frame = frames[index].copy()
        record = poses.get(str(index+1), {})
        draw = ImageDraw.Draw(frame)
        points = [xy for xy,c in zip(record.get('points', []), record.get('confidence', []))
                  if isinstance(c,(int,float)) and c >= .2 and len(xy)==2
                  and all(isinstance(v,(int,float)) and math.isfinite(v) and 0 <= v <= 1 for v in xy)]
        if points:
            xs, ys = zip(*points)
            box = [min(xs)*frame.width, min(ys)*frame.height, max(xs)*frame.width, max(ys)*frame.height]
            draw.rectangle(box, outline='red', width=3)
            for x,y in points:
                x,y=x*frame.width,y*frame.height
                draw.ellipse([x-2,y-2,x+2,y+2],fill='yellow')
        frame.thumbnail((480,350))
        sheet.paste(frame, (column*480, 35))
        ImageDraw.Draw(sheet).text((column*480+5,5), f'Frame {index}; PROVIDED subject pose, unverified', fill='black')
    sheet.save(target)


def load_pack(path, root=ROOT, verify_media=True):
    pack = read_json(path)
    if pack.get('pack_version') != VERSION or pack.get('pack_id') != identity(pack['identity']):
        raise BridgeError('Invalid pack identity/version')
    if sha256(Path(root)/'contracts/v1/messages.schema.json') != pack['identity']['wire_schema_sha256']:
        raise BridgeError('Source transport schema changed')
    source_index = local_path(root,pack['identity']['source_index_ref'])
    if sha256(source_index) != pack['identity']['source_index_sha256']:
        raise BridgeError('Authoritative source roles changed')
    authorized = {(r['split']+'/'+r['sample_id'],r['group']['name']):r
                  for r in read_json(source_index)['views']}
    seen, hashes, groups = set(), {}, {}
    for entry in pack['entries']:
        if entry['entry_id'] in seen:
            raise BridgeError('Duplicate pack entry')
        seen.add(entry['entry_id'])
        if entry['role'] != 'train' or entry['source_split'] != 'train':
            raise BridgeError('This queue bridge accepts source-training groups only')
        source = authorized.get((entry['clip_group'],entry['camera_view']))
        if not source or source['pilot_role']!='train' or source['remote_sha256']!=entry['media_sha256']:
            raise BridgeError('Pack view is not authorized for training by source index')
        key = entry['clip_group']
        if groups.setdefault(key, entry['role']) != entry['role']:
            raise BridgeError('Paired group has conflicting roles')
        if hashes.setdefault(entry['media_sha256'], key) != key:
            raise BridgeError('Same media assigned to different source groups')
        if verify_media:
            media = local_path(root, entry['video_ref'])
            pose = local_path(root, entry['pose_ref'])
            if sha256(media) != entry['media_sha256'] or sha256(pose) != entry['pose_sha256']:
                raise BridgeError('Media or pose checksum changed')
            if frame_index(media) != entry['media_index']:
                raise BridgeError('Media frame index changed')
    if identity(pack['entries']) != pack['entries_sha256']:
        raise BridgeError('Pack entries changed')
    return pack


def prepare_pack(queue, output, root=ROOT, app_root=APP, limit_groups=None, source_index=None):
    queue, output, root = Path(queue), Path(output), Path(root).resolve()
    rows = read_jsonl(queue)
    if limit_groups is not None:
        rows = rows[:limit_groups]
    if not rows:
        raise BridgeError('Empty review queue')
    source_index = Path(source_index) if source_index else root/'runs/hand_detail_v6_scale512/index.json'
    try:
        source_ref = str(source_index.resolve().relative_to(root))
    except ValueError as error:
        raise BridgeError('Source index must remain inside the project root') from error
    authorized = {(r['split']+'/'+r['sample_id'],r['group']['name']):r
                  for r in read_json(source_index)['views']}
    spec = {'queue_sha256': sha256(queue), 'selected_groups': [r['clip_group'] for r in rows],
            'source_index_ref':source_ref,'source_index_sha256':sha256(source_index),
            'wire_schema_sha256': sha256(root/'contracts/v1/messages.schema.json'),
            'review_schema_sha256': sha256(Path(app_root)/'app/p1/annotation.schema.json'),
            'review_schema_canonical_sha256': identity(read_json(Path(app_root)/'app/p1/annotation.schema.json')),
            'bridge_version': 1}
    pack_id = identity(spec)
    check = validator(root/'contracts/v1/messages.schema.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.review-pack-') as tmp_name:
        tmp = Path(tmp_name)
        (tmp/'records').mkdir()
        (tmp/'previews').mkdir()
        entries, seen_groups = [], set()
        for group in rows:
            if group['role'] != 'train' or not group['clip_group'].startswith('train/') or group['clip_group'] in seen_groups:
                raise BridgeError('Review queue groups must be unique source-training groups')
            seen_groups.add(group['clip_group'])
            cameras = set()
            for view in group['views']:
                if view['camera_view'] in cameras or view.get('frame_index_base') != 0 or view.get('source_kind') != 'sparse_clip':
                    raise BridgeError('Invalid paired-view/frame convention')
                cameras.add(view['camera_view'])
                source = authorized.get((group['clip_group'],view['camera_view']))
                if not source or source['pilot_role']!='train' or source['remote_sha256']!=view['sha256']:
                    raise BridgeError('Queue view is not in the source training role')
                video = local_path(root, view['video_ref'])
                pose = local_path(root, view['pose_ref'])
                if sha256(video) != view['sha256']:
                    raise BridgeError('Queue media hash does not match source bytes')
                index = frame_index(video)
                frames = index['frames']
                entry_id = identity([group['clip_group'], view['camera_view'], view['sha256']])
                subject = ['retailaction-research', group['clip_group'].replace('/','-')+'-'+view['camera_view'],
                           'sparse-'+view['sha256'][:20], 'provided-subject']
                entry = {'entry_id': entry_id, 'clip_group': group['clip_group'], 'role': 'train',
                         'source_split': 'train', 'camera_view': view['camera_view'],
                         'video_ref': view['video_ref'], 'media_sha256': view['sha256'],
                         'pose_ref': view['pose_ref'], 'pose_sha256': sha256(pose),
                         'subject_track_key': subject, 'subject_provenance': 'supplied pose; not human verified',
                         'media_index': index, 'source_kind': 'sparse_clip',
                         'sampling_time_known': False, 'capture_elapsed_ms': None, 'capture_time_utc': None,
                         'clock_status': 'playback_only', 'collection_session': 'retailaction/'+group['clip_group'],
                         'source_annotations_not_human_reverified': view['source_annotations_not_human_reverified'],
                         'review_questions': group['review_questions'], 'review_reasons': group['review_reasons'],
                         'records_ref': f'records/{entry_id}.jsonl', 'preview_ref': f'previews/{entry_id}.png', 'targets': []}
                records = []
                for number, suggestion in enumerate(entry['source_annotations_not_human_reverified']):
                    first, end = suggestion['start_frame'], suggestion['end_frame_exclusive']
                    if (type(first) is not int or type(end) is not int or not 0 <= first < end <= len(frames)
                            or suggestion['action'] not in CLASSES):
                        raise BridgeError('Invalid source annotation bounds/class')
                    context = list(range(max(0,first-1), min(len(frames),end+1)))
                    cid = identity([pack_id, entry_id, 'source-event', number])
                    common = {'schema_version':'1.0', 'store_id':subject[0], 'camera_id':subject[1],
                              'stream_session_id':subject[2], 'local_track_id':subject[3], 'global_visit_id':None,
                              'capture_time_utc':None, 'clock_status':'playback_only',
                              'media_time_ms':frames[context[-1]]['media_ms'], 'candidate_id':cid}
                    asset = f'asset://review-pack/{pack_id}/{entry_id}'
                    candidate = {**common, 'type':'action.candidate', 'record_id':identity([cid,'candidate']),
                                 'start_media_ms':frames[context[0]]['media_ms'], 'end_media_ms':frames[context[-1]]['media_ms'],
                                 'frame_indices':context, 'frame_media_times_ms':[frames[i]['media_ms'] for i in context],
                                 'capture_elapsed_ms':None, 'sampling_time_known':False, 'source_kind':'sparse_clip',
                                 'video_ref':asset+'/video', 'observations_ref':asset+'/poses'}
                    scored = {**common, 'type':'action.scored', 'record_id':identity([cid,'review-target']),
                              'model_id':'review-bridge-unscored-proposal-v1', 'action':'unknown', 'scores':None,
                              'abstained':True, 'quality':'mock', 'score_semantics':'unavailable',
                              'reason':f"Unverified source suggestion: {suggestion['action']}, frames {first}:{end}. No inference or human adjudication performed.",
                              'evidence_ref':asset+'/video', 'task':'temporal_detection',
                              'start_media_ms':frames[first]['media_ms'], 'end_media_ms':frames[end-1]['media_ms']}
                    for row in [candidate,scored]:
                        validate_wire(row,check)
                    records.extend([candidate,scored])
                    entry['targets'].append({'target_id':'record:'+scored['record_id'], 'candidate_id':cid,
                                             'source_event_index':number, 'suggested_label_unverified':suggestion['action']})
                entry['wire_records'] = records
                write_jsonl(tmp/entry['records_ref'], records)
                make_preview(video, read_json(pose)['frames'], entry['source_annotations_not_human_reverified'], tmp/entry['preview_ref'])
                entries.append(entry)
        pack = {'pack_version':VERSION, 'pack_id':pack_id, 'identity':spec, 'entries':entries,
                'entries_sha256':identity(entries), 'groups':len(rows), 'views':len(entries),
                'warning':'Source suggestions and supplied subject poses are unverified. PTS is playback time, not original sparse-frame capture cadence. No model inference.'}
        write_json(tmp/'pack.json', pack)
        cards = []
        for entry in entries:
            suggestions = ', '.join(f"{s['action']} frames {s['start_frame']}:{s['end_frame_exclusive']}" for s in entry['source_annotations_not_human_reverified'])
            cards.append(f'<article><h2>{html.escape(entry["clip_group"])} / {html.escape(entry["camera_view"])}</h2>'
                         f'<p>UNVERIFIED suggestions: {html.escape(suggestions)}</p><img width="100%" src="{entry["preview_ref"]}">'
                         f'<p>App camera label: {html.escape(entry["subject_track_key"][1])}. Pair group: {html.escape(entry["clip_group"])}.</p></article>')
        (tmp/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Sentient review pack</title>'
            '<style>body{max-width:1100px;margin:40px auto;font:16px system-ui;background:#fafafa}article{background:white;padding:20px;margin:20px 0;border:1px solid #ccc}img{height:auto}</style>'
            '<h1>Source training examples for review</h1><p>Red box/yellow joints indicate the supplied subject pose, not verified identity. '
            'Suggestions are not ground truth. Review each view separately in the app; paired-view synchronization is unavailable. '
            'Frame indices are zero-based; sparse MP4 playback timing is not original capture cadence.</p>'+''.join(cards), encoding='utf-8')
        write_json(tmp/'review.schema.json', read_json(Path(app_root)/'app/p1/annotation.schema.json'))
        if output.exists():
            old = load_pack(output/'pack.json', root)
            if canonical(old) != canonical(pack):
                raise BridgeError('Output pack differs; use a new directory')
            return output/'pack.json'
        tmp.rename(output)
    load_pack(output/'pack.json',root)
    return output/'pack.json'


def client(base_url):
    url = urlparse(base_url)
    if url.scheme != 'http' or url.hostname not in ['127.0.0.1','localhost','::1'] or url.path not in ['', '/']:
        raise BridgeError('P1 path registration requires a local loopback HTTP app URL')
    return httpx.Client(base_url=base_url.rstrip('/'), timeout=60, trust_env=False)


def response_json(response):
    if response.is_error:
        raise BridgeError(f'App HTTP {response.status_code}: {response.text[:400]}')
    return response.json()


def import_pack(pack_path, receipt_path, base_url, root=ROOT, limit_groups=None):
    pack = load_pack(pack_path,root)
    prior = read_json(receipt_path) if Path(receipt_path).exists() else None
    if prior and (prior['pack_sha256'] != sha256(pack_path) or prior['app_url'] != base_url.rstrip('/')):
        raise BridgeError('Receipt belongs to a different pack/server')
    receipt = prior or {'receipt_version':'sentient.review-import/1','pack_id':pack['pack_id'],
                       'pack_sha256':sha256(pack_path),'app_url':base_url.rstrip('/'),'entries':[]}
    imported = {e['entry_id']:e for e in receipt['entries']}
    groups = list(dict.fromkeys(e['clip_group'] for e in pack['entries']))
    allowed = set(groups if limit_groups is None else groups[:limit_groups])
    summaries = {}
    with client(base_url) as api:
        status=response_json(api.get('/api/status'))
        if 'worker_version' in pack['identity'] and not status.get('isolated_replays'):
            raise BridgeError('Scored worker packs require the additive isolated replay capability; use the integrated application build')
        for entry in pack['entries']:
            if entry['clip_group'] not in allowed:
                continue
            key = entry['subject_track_key']
            video = response_json(api.post('/api/videos/import',json={
                'path':str(local_path(root,entry['video_ref'])), 'store_id':key[0], 'camera_id':key[1],
                'stream_session_id':key[2], 'collection_session':entry['collection_session'], 'collection_day':None}))
            if video['media_sha256'] != entry['media_sha256'] or video['frame_count'] != entry['media_index']['frame_count']:
                raise BridgeError('Imported media identity/frame count differs')
            actual = response_json(api.get(f'/api/videos/{video["id"]}/frames',params={'limit':1000}))
            expected = entry['media_index']['frames']
            if len(actual) != len(expected) or any(a['frame_index']!=b['frame_index'] or a['pts']!=b['pts'] or a['time_base']!=b['time_base'] for a,b in zip(actual,expected)):
                raise BridgeError('App frame index differs from original media')
            replay = response_json(api.post('/api/replays', data={'video_id':video['id'], **({'isolated_run':'true'} if 'worker_version' in pack['identity'] else {})},
                files={'file':('review-targets.jsonl',''.join(canonical(r)+'\n' for r in entry['wire_records']).encode(),'application/x-ndjson')}))
            evidence = response_json(api.get(f'/api/analyses/{replay["id"]}/events'))
            targets = {r['target_id'] for r in evidence['events']}
            if targets != {r['target_id'] for r in entry['targets']}:
                raise BridgeError('App evidence targets differ from pack')
            saved = imported.setdefault(entry['entry_id'], {'entry_id':entry['entry_id'], 'video_id':video['id'],'bindings':[]})
            if saved['video_id'] != video['id']:
                raise BridgeError('Video identity changed on retry')
            binding = {'analysis_id':replay['id'],'geometry_id':replay['geometry_id']}
            if binding not in saved['bindings']:
                saved['bindings'].append(binding)
            summaries[entry['entry_id']] = replay['summary']
            receipt['entries'] = sorted(imported.values(),key=lambda e:e['entry_id'])
            write_json(receipt_path,receipt)
    return {**receipt,'last_replay_summaries':summaries}


def export_reviews(receipt_path, output):
    receipt = read_json(receipt_path)
    rows = []
    with client(receipt['app_url']) as api:
        for entry in receipt['entries']:
            response = api.get('/api/annotations/export',params={'video_id':entry['video_id']})
            if response.is_error:
                raise BridgeError(f'Export failed: {response.status_code}')
            rows.extend(parse(line) for line in response.text.splitlines() if line.strip())
    write_jsonl(output,rows)
    return len(rows)


class ReviewReader:
    def __init__(self, pack_path, receipt_path, root=ROOT):
        self.root = Path(root)
        self.pack = load_pack(pack_path,root)
        self.receipt = read_json(receipt_path)
        if self.receipt.get('receipt_version') != 'sentient.review-import/1' or self.receipt['pack_id'] != self.pack['pack_id'] or self.receipt['pack_sha256'] != sha256(pack_path):
            raise BridgeError('Receipt/pack binding differs')
        schema = Path(pack_path).parent/'review.schema.json'
        if identity(read_json(schema)) != self.pack['identity']['review_schema_canonical_sha256']:
            raise BridgeError('Review schema differs')
        self.check = validator(schema)
        self.wire = validator(self.root/'contracts/v1/messages.schema.json')
        self.entries = {e['entry_id']:e for e in self.pack['entries']}
        self.bindings = {}
        for imported in self.receipt['entries']:
            entry = self.entries[imported['entry_id']]
            for binding in imported['bindings']:
                key = imported['video_id'],binding['analysis_id']
                if key in self.bindings and self.bindings[key][0]['entry_id'] != entry['entry_id']:
                    raise BridgeError('Ambiguous import binding')
                self.bindings[key] = entry,binding

    def validate(self, review):
        _finite(review)
        try:
            self.check.validate(review)
        except Exception as error:
            raise BridgeError('Review envelope schema violation') from error
        try:
            entry,binding = self.bindings[review['video_id'],review['provenance']['analysis_id']]
        except KeyError as error:
            raise BridgeError('Review is not bound to an imported pack view/analysis') from error
        if review['media_sha256'] != entry['media_sha256'] or review['subject_track_key'] != entry['subject_track_key']:
            raise BridgeError('Review media/subject mismatch')
        if [review[k] for k in ['store_id','camera_id','stream_session_id','local_track_id']] != entry['subject_track_key'] or review['global_visit_id'] is not None:
            raise BridgeError('Review identity fields disagree')
        if review['collection_session'] != entry['collection_session'] or review['collection_day'] is not None:
            raise BridgeError('Collection grouping changed')
        if review['capture_time_utc'] is not None or review['clock_status'] != 'playback_only':
            raise BridgeError('Sparse-export playback cannot establish capture UTC')
        p = review['provenance']
        if p['geometry_id'] != binding['geometry_id'] or p['media_manifest'] != review['video_id']+'.json':
            raise BridgeError('Review provenance differs from receipt')
        target = next((t for t in entry['targets'] if t['target_id']==p['target_id']),None)
        if target is None:
            raise BridgeError('Unknown review target')
        if review['annotation_id'] != identity([review['video_id'],p['analysis_id'],p['target_id'],review['revision']]):
            raise BridgeError('Annotation identity/revision mismatch')
        try:
            created = datetime.fromisoformat(review['created_at_utc'].replace('Z','+00:00'))
            if created.tzinfo is None:
                raise ValueError('missing timezone')
        except (ValueError,TypeError) as error:
            raise BridgeError('Invalid review creation time') from error
        human = review['human']
        if (not human['reviewer'].strip() or human['label'] not in REVIEW_LABELS
                or human['decision'] not in ['accepted','corrected','rejected','pending']
                or human['visibility'] not in ['clear','partial','occluded','unknown']
                or human['uncertainty'] not in ['low','medium','high']):
            raise BridgeError('Invalid reviewer/decision/quality values')
        frames = entry['media_index']['frames']
        start,end = review['start_media_ms'],review['end_media_ms']
        if not frames[0]['media_ms'] <= start <= end <= frames[-1]['media_ms']:
            raise BridgeError('Review interval outside source media')
        expected = [f for f in frames if start <= f['media_ms'] <= end]
        pts = [{'frame_index':f['frame_index'],'pts':f['pts'],'time_base':f['time_base']} for f in expected]
        if (not expected or any(type(i) is not int for i in review['frame_indices'])
                or review['frame_indices'] != [f['frame_index'] for f in expected]
                or review['source_frame_pts'] != pts
                or len(review['original_media_times_ms']) != len(expected)
                or any(not math.isclose(a,b['media_ms'],rel_tol=0,abs_tol=1e-5) for a,b in zip(review['original_media_times_ms'],expected))):
            raise BridgeError('Review frame indices, PTS, time bases and span disagree')
        known = {r['record_id']:r for r in entry['wire_records']}
        source = {}
        for row in review['source_records']:
            validate_wire(row,self.wire)
            rid = row['record_id']
            if rid in source or rid not in known or canonical(row)!=canonical(known[rid]):
                raise BridgeError('Embedded source record changed or duplicated')
            source[rid] = row
        scored_id = p['target_id'].removeprefix('record:')
        candidate_id = next(r['record_id'] for r in entry['wire_records'] if r['type']=='action.candidate' and r['candidate_id']==target['candidate_id'])
        if scored_id not in source or candidate_id not in source:
            raise BridgeError('Review lacks its original target/candidate')
        outputs = [r for r in review['source_records'] if r['type']=='action.scored']
        if review['model_outputs'] != outputs:
            raise BridgeError('Model output provenance differs from source records')
        return entry

    def latest(self, reviews):
        ids, revisions, latest = {}, {}, {}
        for row in reviews:
            entry = self.validate(row)
            rid = row['annotation_id']
            if rid in ids:
                if canonical(ids[rid]) != canonical(row):
                    raise BridgeError('Conflicting duplicate annotation')
                continue
            ids[rid] = row
            p = row['provenance']
            key = row['video_id'],p['analysis_id'],p['target_id']
            revision_key = (*key,row['revision'])
            if revision_key in revisions and canonical(revisions[revision_key])!=canonical(row):
                raise BridgeError('Conflicting review revision')
            revisions[revision_key] = row
            if key not in latest or row['revision'] > latest[key][1]['revision']:
                latest[key] = entry,row
        logical = {}
        for entry,row in latest.values():
            key = entry['entry_id'],row['provenance']['target_id']
            if key in logical:
                previous = logical[key][1]
                if any(previous[k]!=row[k] for k in ['human','frame_indices','start_media_ms','end_media_ms']):
                    raise BridgeError('Conflicting reviews across analyses; adjudicate explicitly')
                if previous['annotation_id'] > row['annotation_id']:
                    continue
            logical[key] = entry,row
        return [logical[k] for k in sorted(logical)]


def attest(reviews_path, output, annotation_ids, kind, attested_by):
    if kind not in ['human','automated_fixture'] or not attested_by.strip():
        raise BridgeError('Explicit attestation kind and named attestor required')
    by_id = {}
    for row in read_jsonl(reviews_path):
        aid = row['annotation_id']
        if aid in by_id and canonical(by_id[aid]) != canonical(row):
            raise BridgeError('Conflicting annotation payloads')
        by_id[aid] = row
    if not annotation_ids or len(set(annotation_ids)) != len(annotation_ids):
        raise BridgeError('Name distinct annotation IDs to attest')
    records = read_jsonl(output) if Path(output).exists() else []
    existing = {r['annotation_id']:r for r in records}
    for aid in annotation_ids:
        if aid not in by_id:
            raise BridgeError(f'Unknown annotation ID: {aid}')
        row = by_id[aid]
        value = {'attestation_version':'sentient.review-attestation/1','annotation_id':aid,
                 'review_sha256':identity(row),'reviewer':row['human']['reviewer'],
                 'kind':kind,'attested_by':attested_by}
        if aid in existing and existing[aid] != value:
            raise BridgeError('Conflicting attestation; preserve prior file and resolve explicitly')
        existing[aid] = value
    write_jsonl(output,[existing[k] for k in sorted(existing)])


def latest_ids_for_reviewer(reviews_path, reviewer):
    latest = {}
    for row in read_jsonl(reviews_path):
        p = row['provenance']
        key = row['video_id'],p['analysis_id'],p['target_id']
        previous = latest.get(key)
        if previous and previous['revision']==row['revision'] and canonical(previous)!=canonical(row):
            raise BridgeError('Conflicting review revision')
        if previous is None or row['revision'] > previous['revision']:
            latest[key] = row
    # Filter after resolving revisions across ALL reviewers; never resurrect an
    # older label just because a different reviewer owns the latest revision.
    return sorted(r['annotation_id'] for r in latest.values() if r['human']['reviewer']==reviewer)


def eligibility(review, attestation):
    reasons = []
    h = review['human']
    if h['decision'] not in ['accepted','corrected']:
        reasons.append('decision_'+h['decision'])
    if h['label'] not in CLASSES+['background']:
        reasons.append('non_training_label')
    if h['visibility'] != 'clear':
        reasons.append('visibility_'+h['visibility'])
    if h['uncertainty'] != 'low':
        reasons.append('uncertainty_'+h['uncertainty'])
    if not attestation:
        reasons.append('unattested_review_origin')
    elif (attestation.get('attestation_version') != 'sentient.review-attestation/1'
          or attestation.get('annotation_id') != review['annotation_id']
          or attestation.get('review_sha256') != identity(review)
          or attestation.get('reviewer') != h['reviewer'] or not str(attestation.get('attested_by','')).strip()):
        raise BridgeError('Attestation does not bind this exact review payload')
    elif attestation.get('kind') != 'human':
        if attestation.get('kind') != 'automated_fixture':
            raise BridgeError('Unknown attestation kind')
        reasons.append('automated_fixture')
    return reasons


def build_dataset(pack_path, receipt_path, exports, output, attestations=None, root=ROOT, fixture_output=None):
    reader = ReviewReader(pack_path,receipt_path,root)
    all_reviews = [r for path in exports for r in read_jsonl(path)]
    assertions = {}
    for row in read_jsonl(attestations) if attestations else []:
        if row['annotation_id'] in assertions and assertions[row['annotation_id']] != row:
            raise BridgeError('Conflicting attestation entries')
        assertions[row['annotation_id']] = row
    rows, excluded, fixtures = [], [], []
    latest = reader.latest(all_reviews)
    for entry,review in latest:
        proof = assertions.get(review['annotation_id'])
        reasons = eligibility(review,proof)
        value = {'entry_id':entry['entry_id'],'clip_group':entry['clip_group'],'role':entry['role'],
                 'source_split':entry['source_split'],'camera_view':entry['camera_view'],
                 'video_ref':entry['video_ref'],'media_sha256':entry['media_sha256'],
                 'pose_ref':entry['pose_ref'],'pose_sha256':entry['pose_sha256'],
                 'label':review['human']['label'],'review':review,'attestation':proof,
                 'training_eligible':not reasons}
        if reasons:
            excluded.append({'annotation_id':review['annotation_id'],'entry_id':entry['entry_id'],'reasons':reasons})
            if reasons == ['automated_fixture']:
                fixtures.append(value)
        else:
            rows.append(value)
    imported_entries = {e['entry_id'] for e in reader.receipt['entries']}
    imported_targets = sum(len(e['targets']) for e in reader.pack['entries'] if e['entry_id'] in imported_entries)
    base = {'manifest_version':'sentient.reviewed-dataset/1','dataset_kind':'human_reviewed',
            'pack_id':reader.pack['pack_id'],'pack_sha256':sha256(pack_path),'receipt_sha256':sha256(receipt_path),
            'review_exports_sha256':[sha256(p) for p in exports],
            'attestations_sha256':sha256(attestations) if attestations else None,
            'classes':CLASSES,'supervision':'Positive action labels supervise only that action; explicitly reviewed background supervises negatives for all three. Unreviewed frames/classes are masked, not background.',
            'records':rows,'excluded_latest_reviews':excluded,
            'summary':{'input_revisions':len(all_reviews),'latest_targets':len(latest),'eligible_records':len(rows),
                       'excluded_targets':len(excluded),'pack_targets':sum(len(e['targets']) for e in reader.pack['entries']),
                       'imported_targets':imported_targets,'unreviewed_imported_targets':imported_targets-len(latest)}}
    base['content_sha256'] = identity(base)
    # Verify contradictory overlapping labels before committing any output.
    _examples(base,reader)
    fixture = None
    if fixture_output:
        fixture = {**base,'dataset_kind':'integration_fixture','records':fixtures,
                   'summary':{**base['summary'],'eligible_records':0,'fixture_records':len(fixtures)}}
        fixture.pop('content_sha256')
        fixture['content_sha256'] = identity(fixture)
        _examples(fixture,reader,allow_fixtures=True)
    write_json(output,base)
    if fixture is not None:
        write_json(fixture_output,fixture)
    return base['summary']


def _examples(manifest, reader, allow_fixtures=False):
    kind = manifest['dataset_kind']
    if kind != 'human_reviewed' and not (allow_fixtures and kind=='integration_fixture'):
        raise BridgeError('Integration fixtures cannot be used as training data')
    result = {}
    for row in manifest['records']:
        entry = reader.validate(row['review'])
        for field in ['entry_id','clip_group','role','source_split','camera_view','video_ref','media_sha256','pose_ref','pose_sha256']:
            if row[field] != entry[field]:
                raise BridgeError('Dataset record changed source grouping/media')
        reasons = eligibility(row['review'],row['attestation'])
        expected = ['automated_fixture'] if kind=='integration_fixture' else []
        if reasons != expected or type(row['training_eligible']) is not bool or row['training_eligible'] != (kind=='human_reviewed') or row['label'] != row['review']['human']['label']:
            raise BridgeError('Dataset eligibility/label differs from review')
        n = entry['media_index']['frame_count']
        item = result.setdefault(entry['entry_id'], {'entry_id':entry['entry_id'],'clip_group':entry['clip_group'],
            'role':entry['role'],'video_ref':entry['video_ref'],'pose_ref':entry['pose_ref'],
            'camera_view':entry['camera_view'],'media_sha256':entry['media_sha256'],
            'pose_sha256':entry['pose_sha256'],'frame_media_times_ms':[f['media_ms'] for f in entry['media_index']['frames']],
            'subject_track_key':entry['subject_track_key'],'frame_count':n,'classes':CLASSES,
            'targets':[[0.0]*n for _ in CLASSES],'label_mask':[[False]*n for _ in CLASSES],
            'sampling_time_known':False,'capture_elapsed_ms':None,'dataset_kind':kind,'annotation_ids':[]})
        label = row['label']
        channels = range(3) if label=='background' else [CLASSES.index(label)]
        target = 0.0 if label=='background' else 1.0
        for c in channels:
            for i in row['review']['frame_indices']:
                if item['label_mask'][c][i] and item['targets'][c][i] != target:
                    raise BridgeError('Conflicting overlapping action/background reviews')
                item['label_mask'][c][i] = True
                item['targets'][c][i] = target
        item['annotation_ids'].append(row['review']['annotation_id'])
    return list(result.values())


def load_dataset(manifest_path, pack_path, receipt_path, root=ROOT, allow_fixtures=False):
    manifest = read_json(manifest_path)
    content_hash = manifest.pop('content_sha256',None)
    if identity(manifest) != content_hash or manifest.get('manifest_version') != 'sentient.reviewed-dataset/1':
        raise BridgeError('Dataset manifest changed/version unsupported')
    if manifest['pack_sha256'] != sha256(pack_path) or manifest['receipt_sha256'] != sha256(receipt_path) or manifest['classes'] != CLASSES:
        raise BridgeError('Dataset source binding/classes changed')
    reader = ReviewReader(pack_path,receipt_path,root)
    return _examples(manifest,reader,allow_fixtures)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command',required=True)
    pack = sub.add_parser('prepare',help='Create source-bound queue pack and subject previews')
    pack.add_argument('--queue',type=Path,default=ROOT/'runs/hand_detail_v6_scale512/touch_review_queue.jsonl')
    pack.add_argument('--output',type=Path,required=True)
    pack.add_argument('--limit-groups',type=int)
    pack.add_argument('--app-root',type=Path,default=APP)
    pack.add_argument('--source-index',type=Path)
    imp = sub.add_parser('import',help='Register pack media and unscored targets through P1 HTTP APIs')
    imp.add_argument('--pack',type=Path,required=True)
    imp.add_argument('--receipt',type=Path,required=True)
    imp.add_argument('--app-url',default='http://127.0.0.1:8765')
    imp.add_argument('--limit-groups',type=int)
    exp = sub.add_parser('export')
    exp.add_argument('--receipt',type=Path,required=True)
    exp.add_argument('--output',type=Path,required=True)
    att = sub.add_parser('attest',help='Record declared human adjudication or explicit automated-fixture origin')
    att.add_argument('--reviews',type=Path,required=True)
    selection = att.add_mutually_exclusive_group(required=True)
    selection.add_argument('--annotation-id',action='append')
    selection.add_argument('--reviewer',help='Attest only targets whose latest revision belongs to this reviewer')
    att.add_argument('--kind',choices=['human','automated_fixture'],required=True)
    att.add_argument('--attested-by',required=True)
    att.add_argument('--output',type=Path,required=True)
    build = sub.add_parser('build')
    build.add_argument('--pack',type=Path,required=True)
    build.add_argument('--receipt',type=Path,required=True)
    build.add_argument('--reviews',type=Path,action='append',required=True)
    build.add_argument('--attestations',type=Path)
    build.add_argument('--output',type=Path,required=True)
    build.add_argument('--fixture-output',type=Path)
    args = p.parse_args()
    if getattr(args,'limit_groups',None) is not None and args.limit_groups < 1:
        p.error('--limit-groups must be positive')
    try:
        if args.command == 'prepare':
            print(prepare_pack(args.queue,args.output,app_root=args.app_root,limit_groups=args.limit_groups,source_index=args.source_index))
        elif args.command == 'import':
            r = import_pack(args.pack,args.receipt,args.app_url,limit_groups=args.limit_groups)
            print(canonical({'imported_views':len(r['entries']),'receipt':str(args.receipt)}))
        elif args.command == 'export':
            print(canonical({'exported_revisions':export_reviews(args.receipt,args.output)}))
        elif args.command == 'attest':
            selected = args.annotation_id if args.annotation_id else latest_ids_for_reviewer(args.reviews,args.reviewer)
            attest(args.reviews,args.output,selected,args.kind,args.attested_by)
            print(args.output)
        elif args.command == 'build':
            print(canonical(build_dataset(args.pack,args.receipt,args.reviews,args.output,args.attestations,fixture_output=args.fixture_output)))
    except (BridgeError,OSError,KeyError,TypeError,httpx.HTTPError) as error:
        p.exit(2,f'Review bridge rejected input: {error}\n')


if __name__ == '__main__':
    main()
