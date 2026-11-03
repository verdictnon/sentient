"""Acquire a bounded, provenance-preserving CARR diagnostic, not the 40+ GB corpus.

Labels remain in the publisher's six-class namespace. Only the designated person
is annotated; other people must never silently become detector negatives.
"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import struct
import time
import urllib.request
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
DOI = 'doi:10.7910/DVN/CY24UB'
HEADERS = {'User-Agent': 'SentientResearch/1.0 (bounded public dataset audit)'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


class RangeFile(io.RawIOBase):
    """Exact bounded ranges, immutable object checks and reusable disk caching."""
    def __init__(self, url, size, cache, budget=1024**3):
        self.url, self.size, self.position = url, size, 0
        self.origin_url = url
        self.cache = Path(cache); self.cache.mkdir(parents=True, exist_ok=True)
        self.transferred = 0; self.budget = budget; self.etag = None
        self.prefetched = []

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position
    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        if self.position < 0: raise ValueError('Negative archive offset')
        return self.position

    def fetch(self, start, size):
        if size > 24 * 1024**2 or start < 0 or start + size > self.size:
            raise ValueError('Archive range exceeds its explicit bound')
        for begin, payload in self.prefetched:
            if begin <= start and start + size <= begin + len(payload):
                return payload[start-begin:start-begin+size]
        path = self.cache / f'{start}-{size}.bin'
        if path.exists():
            value = path.read_bytes()
            if len(value) != size: raise ValueError('Truncated cached archive range')
            return value
        if self.transferred + size > self.budget: raise ValueError('Per-archive transfer budget exceeded')
        headers = HEADERS | {'Range': f'bytes={start}-{start+size-1}'}
        if self.etag: headers['If-Match'] = self.etag
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(self.url, headers=headers), timeout=30) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{start+size-1}/{self.size}':
                        raise ValueError('Server did not honor exact range; refusing a whole archive')
                    if self.etag and response.headers.get('ETag') != self.etag: raise ValueError('Archive changed')
                    self.etag = response.headers.get('ETag')
                    self.url = response.geturl()
                    value = response.read(size+1)
                break
            except (OSError, TimeoutError):
                self.url = self.origin_url
                if attempt == 2: raise
                time.sleep(.2 * (attempt+1))
        if len(value) != size: raise ValueError('Truncated or oversized range response')
        self.transferred += size
        temporary=path.with_suffix('.tmp');temporary.write_bytes(value);temporary.replace(path)
        return value

    def read(self, size=-1):
        if size < 0: raise ValueError('Unbounded archive read is forbidden')
        size = min(size, self.size-self.position)
        if size <= 0: return b''
        result = self.fetch(self.position, size); self.position += size
        return result

    def prefetch(self, infos):
        """Coalesce adjacent JPEG members; avoid one network round trip per frame."""
        spans = []
        for info in sorted(infos, key=lambda x: x.header_offset):
            start = info.header_offset
            end = start + 30 + len(info.filename.encode('utf-8')) + len(info.extra) + info.compress_size + 128
            end = min(end, self.size)
            if spans and start-spans[-1][1] < 128*1024 and end-spans[-1][0] <= 16*1024**2:
                spans[-1][1] = max(spans[-1][1], end)
            else: spans.append([start, end])
        if self.transferred+sum(end-start for start,end in spans)>self.budget:
            raise ValueError('Prefetch transfer plan exceeds archive budget')
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.prefetched=list(pool.map(lambda span:(span[0],self.fetch(span[0],span[1]-span[0])),spans))


def contiguous_runs(rows):
    chunks = []; chunk = []
    for row in sorted(rows, key=lambda r: int(r['frame_index'])):
        if chunk and int(row['frame_index']) != int(chunk[-1]['frame_index'])+1:
            chunks.append(chunk); chunk = []
        chunk.append(row)
    if chunk: chunks.append(chunk)
    return chunks


def plan(folder, frames_per_demo=120):
    folder = Path(folder)
    with zipfile.ZipFile(folder/'CARR_annotations.zip') as archive:
        for name in ['carr_manifest.csv', 'classes_and_persons.yaml']:
            (folder/name).write_bytes(archive.read('CARR/'+name))
    with zipfile.ZipFile(folder/'CARR_splits.zip') as archive:
        (folder/'video_splits.csv').write_bytes(archive.read('CARR/splits/video_splits.csv'))
    groups = defaultdict(list)
    with (folder/'carr_manifest.csv').open() as stream:
        for row in csv.DictReader(stream): groups[row['video_id']].append(row)
    with (folder/'video_splits.csv').open() as stream:
        splits = {r['video_id']:r for r in csv.DictReader(stream)}
    selected = []; wanted = {}
    for role in ['train', 'test']:
        used_people = set(); used_stores = set()
        for cls in range(6):
            candidates = []
            for vid, rows in groups.items():
                if int(rows[0]['class_id']) != cls or splits[vid]['CS1'] != role: continue
                ordered = sorted(rows, key=lambda r: int(r['frame_index']))
                run = max(contiguous_runs(rows), key=len)
                candidates.append((vid, ordered, run))
            # Two designated subjects/stores when possible; frozen before model output.
            for slot in range(2):
                if not candidates: break
                candidates.sort(key=lambda c: (
                    c[1][0]['participant_code'] not in used_people,
                    c[1][0]['location_name'] not in used_stores,
                    min(len(c[2]), frames_per_demo), len(c[2]), c[0]), reverse=True)
                vid, rows, run = candidates.pop(0); first = rows[0]
                used_people.add(first['participant_code']); used_stores.add(first['location_name'])
                indices = sorted({round(i*(len(rows)-1)/5) for i in range(6)})
                samples = [rows[i] for i in indices]
                # Only one diagnostic view per native class is added to the app.
                demo = run[:frames_per_demo] if role == 'test' and slot == 0 and len(run) >= 30 else []
                item = {k:first[k] for k in ['video_id','class_id','class_name','participant_code','location_name']}
                item.update(native_protocol='CS1', native_role=role,
                    use='detector_selection' if role == 'train' else 'frozen_diagnostic_check',
                    diagnostic_samples=samples, demo_frames=demo,
                    original_video_frames=len(rows), longest_contiguous_frames=len(run))
                selected.append(item)
                for row in samples+demo: wanted[row['file_name']+'.jpg'] = row
    subjects = [{x['participant_code'] for x in selected if x['native_role']==r} for r in ['train','test']]
    if subjects[0] & subjects[1]: raise ValueError('Publisher cross-subject roles overlap')
    protocol = {'format':'sentient.carr-diagnostic/1', 'doi':DOI, 'native_frame_rate':15,
        'timing_basis':'Publisher-declared frame index / 15, not original video PTS or capture UTC',
        'label_scope':'Publisher-designated person only; other visible people are unannotated',
        'class_mapping':'No automatic mapping to RetailAction take/put/touch or theft',
        'selection_rule':'Two videos per class/CS1 role; prefer participant/store diversity then long contiguous runs; six evenly spaced diagnostic frames; test slot 0 contiguous preview <=120 frames',
        'annotation_archive_sha256':sha(folder/'CARR_annotations.zip'),
        'split_archive_sha256':sha(folder/'CARR_splits.zip'), 'videos':selected,
        'unique_images':len(wanted), 'stores':sorted({x['location_name'] for x in selected}),
        'participants':sorted({x['participant_code'] for x in selected})}
    path = folder/'protocol.json'
    if path.exists() and json.loads(path.read_text()) != protocol: raise ValueError('Frozen diagnostic protocol differs')
    save(path, protocol)
    return protocol, wanted


def acquire(folder, metadata, prepared=None):
    folder = Path(folder); protocol, wanted = prepared if prepared is not None else plan(folder)
    images = folder/'images'; images.mkdir(exist_ok=True)
    labels = folder/'labels'; labels.mkdir(exist_ok=True)
    with zipfile.ZipFile(folder/'CARR_annotations.zip') as archive:
        for name in wanted:
            path=labels/(Path(name).stem+'.txt');content=archive.read('CARR/labels/'+Path(name).stem+'.txt')
            if path.exists():
                if path.read_bytes()!=content:raise ValueError('Cached source label changed')
            else:path.write_bytes(content)
    files = metadata['data']['files']; found = {}; total_transferred = 0; archive_sources = []
    for entry in files:
        data = entry['dataFile']; name = data['filename']
        if not name.startswith('Images_carr_part_') or '-' in name: continue
        if len(found) == len(wanted): break
        cache = folder/'range_cache'/str(data['id'])
        remote = RangeFile('https://dataverse.harvard.edu/api/access/datafile/'+str(data['id']), data['filesize'], cache)
        with zipfile.ZipFile(remote) as archive:
            selected = [i for i in archive.infolist() if Path(i.filename).name in wanted and Path(i.filename).name not in found]
            pending = [i for i in selected if not (images/Path(i.filename).name).is_file()]
            remote.prefetch(pending)
            for info in selected:
                target = images/Path(info.filename).name
                if not target.exists(): target.write_bytes(archive.read(info))  # zipfile verifies member CRC.
                if zlib.crc32(target.read_bytes()) != info.CRC: raise ValueError('Cached publisher frame CRC mismatch')
                found[target.name] = {'sha256':sha(target), 'zip_crc32':f'{info.CRC:08x}',
                    'archive':name, 'datafile_id':data['id'], 'archive_member':info.filename}
        total_transferred += remote.transferred
        archive_sources.append({'filename':name,'datafile_id':data['id'],'bytes':data['filesize'],
            'publisher_md5_not_whole_archive_verified':data['checksum']['value'],
            'object_etag':remote.etag,'transferred_bytes_this_run':remote.transferred})
        print(name, 'matched', len(selected), 'total', len(found), '/', len(wanted), 'network', total_transferred, flush=True)
    missing = sorted(set(wanted)-set(found))
    if missing: raise ValueError('Missing publisher images: '+str(missing[:8]))
    save(folder/'acquisition.json', {'doi':DOI,'dataset_version':'1.0','use':'Research diagnostic only; CC BY-NC 4.0; preserve publisher terms',
        'protocol_sha256':sha(folder/'protocol.json'), 'transferred_bytes_this_run':total_transferred,
        'image_count':len(found),'sources':archive_sources,'images':found})
    return protocol


def encode(folder, protocol):
    import av
    from PIL import Image
    folder = Path(folder); videos = []
    for item in protocol['videos']:
        rows = item['demo_frames']
        if not rows: continue
        if len(contiguous_runs(rows)) != 1: raise ValueError('Never compress source-time gaps into a continuous demo')
        name = f"CARR_{item['video_id']}_{item['class_name'].replace(' ', '_')}_720p.mp4"
        path = folder/'videos'/name; path.parent.mkdir(exist_ok=True)
        if not path.exists():
            with av.open(str(path), 'w') as output:
                stream = output.add_stream('libx264', rate=15)
                stream.width=1280; stream.height=720; stream.pix_fmt='yuv420p'
                stream.options={'crf':'18','preset':'fast'}
                for i,row in enumerate(rows):
                    with Image.open(folder/'images'/(row['file_name']+'.jpg')) as image:
                        if image.size != (1280,720): raise ValueError('Unexpected publisher image dimensions')
                        frame=av.VideoFrame.from_image(image.convert('RGB'));frame.pts=i;frame.time_base=Fraction(1,15)
                        for packet in stream.encode(frame): output.mux(packet)
                for packet in stream.encode(): output.mux(packet)
        videos.append({**{k:v for k,v in item.items() if k not in ['demo_frames','diagnostic_samples']},
            'path':str(path.resolve()),'sha256':sha(path),'frames':len(rows),
            'source_frame_indices':[int(r['frame_index']) for r in rows],
            'source_file_names':[r['file_name']+'.jpg' for r in rows],
            'timing_basis':protocol['timing_basis'], 'capture_time_utc':None,
            'native_coarse_activity_is_not_an_action_event_label':True})
    save(folder/'video-manifest.json', {'doi':DOI,'protocol_sha256':sha(folder/'protocol.json'),'videos':videos})
    return videos



def long_preview(folder, metadata):
    """Add one longer, genuinely contiguous run without changing the benchmark."""
    import copy
    folder=Path(folder)
    baseline=json.loads((folder/'protocol.json').read_text())
    candidate=max((x for x in baseline['videos'] if x['demo_frames']),key=lambda x:x['longest_contiguous_frames'])
    with (folder/'carr_manifest.csv').open() as stream:
        rows=[row for row in csv.DictReader(stream) if row['video_id']==candidate['video_id']]
    chosen=max(contiguous_runs(rows),key=len)[:900]
    target=folder.parent/'carr_long_preview';target.mkdir(parents=True,exist_ok=True)
    for name in ['CARR_annotations.zip','CARR_splits.zip','images','labels','range_cache']:
        path=target/name
        if not path.exists():path.symlink_to((folder/name).resolve(),target_is_directory=(folder/name).is_dir())
    item=copy.deepcopy(candidate);item['demo_frames']=chosen;item['diagnostic_samples']=[]
    item['use']='Longer qualitative continuous replay, not new evaluation or training'
    protocol={**baseline,'format':'sentient.carr-long-preview/1','parent_protocol_sha256':sha(folder/'protocol.json'),
        'videos':[item],'unique_images':len(chosen),'selection_rule':'Longest contiguous run of an already frozen held-out preview video, capped at 900 source frames; no model scores used'}
    existing=target/'protocol.json'
    if existing.exists() and json.loads(existing.read_text())!=protocol:raise ValueError('Long preview protocol changed')
    save(existing,protocol)
    wanted={row['file_name']+'.jpg':row for row in chosen}
    acquire(target,metadata,prepared=(protocol,wanted))
    return encode(target,protocol)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,default=ROOT/'data/public_retail_hd/carr')
    parser.add_argument('--metadata',type=Path,default=ROOT/'runs/full_audit_2026-11-03/carr-dataverse-metadata.json')
    parser.add_argument('--plan-only',action='store_true')
    parser.add_argument('--long-preview',action='store_true')
    args=parser.parse_args()
    if args.long_preview:
        print(json.dumps({'long_previews':long_preview(args.folder,json.loads(args.metadata.read_text()))},indent=2));return
    if args.plan_only:
        p,_=plan(args.folder);print(json.dumps({k:p[k] for k in ['unique_images','stores','participants']},indent=2));return
    protocol=acquire(args.folder,json.loads(args.metadata.read_text()))
    videos=encode(args.folder,protocol)
    print(json.dumps({'images':protocol['unique_images'],'diagnostic_videos':len(protocol['videos']),'encoded_previews':len(videos)},indent=2))


if __name__=='__main__': main()
