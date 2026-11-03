"""One controlled short-window hand-crop experiment, reusing V6 context/body.

Original labels and 512/64/64 roles stay unchanged. This is development only.
"""
import hashlib
import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import av
import numpy as np
from PIL import Image
import torch

from features_v6 import ROOT, encoder
from features_v3 import DATA, clean_pose
import train_v6 as trainer
from temporal_v3 import normalize, frame_metrics, calibrate, event_metrics

PARENT = ROOT / 'runs/hand_detail_v6_scale512'
OUT = ROOT / 'runs/short_hand_v8'


def short_boxes(cleaned, width, height):
    """Pose-only stabilization over +/-2 sparse frames; no annotation inputs."""
    boxes, validity = [], []
    scale = np.array([width, height])
    for i in range(len(cleaned)):
        frame_boxes, frame_valid = [], []
        local = cleaned[max(0, i-2):min(len(cleaned), i+3)]
        for elbow, wrist, hand in [(8, 9, 10), (4, 5, 6)]:
            points, lengths = [], []
            for p, c in local:
                for joint in [wrist, hand]:
                    if c[joint] >= .2:
                        points.append(p[joint] * scale)
                if min(c[elbow], c[wrist]) >= .2:
                    lengths.append(np.linalg.norm((p[elbow]-p[wrist]) * scale))
            if len(points) < 4:
                frame_boxes.append(None)
                frame_valid.append(0.)
                continue
            lo, hi = np.quantile(np.array(points), [.05, .95], axis=0)
            margin = max(min(width, height)/25, .65*float(np.median(lengths)) if lengths else 0)
            side = max(min(width, height)*.16, float((hi-lo).max()) + 2*margin)
            center = (lo+hi)/2
            frame_boxes.append(tuple(int(round(v)) for v in [
                center[0]-side/2, center[1]-side/2, center[0]+side/2, center[1]+side/2]))
            frame_valid.append(1.)
        boxes.append(frame_boxes)
        validity.append(frame_valid)
    return boxes, np.array(validity, np.float32).T


def prepare(row):
    folder = DATA/row['split']/row['sample_id']
    name = row['group']['name']
    poses = json.loads((folder/(name+'_poses.json')).read_text())['frames']
    with av.open(str(folder/(name+'_video.mp4'))) as video:
        video.streams.video[0].thread_count = 1
        frames = [f.to_ndarray(format='rgb24') for f in video.decode(video=0)]
    n = len(frames)
    if n != row['metadata']['total_frame_count']:
        raise ValueError('Frame count mismatch')
    h, w = frames[0].shape[:2]
    cleaned = [clean_pose(poses.get(str(i+1), {})) for i in range(n)]
    boxes, valid = short_boxes(cleaned, w, h)
    images, positions, sides = [], [], []
    for hand in range(2):
        for i, frame in enumerate(frames):
            box = boxes[i][hand]
            if box is not None:
                images.append(np.asarray(Image.fromarray(frame).crop(box).resize(
                    (224, 224), Image.Resampling.BILINEAR)).copy())
                positions.append((hand, i))
                sides.append(box[2]-box[0])
    return np.stack(images) if images else None, positions, valid, n, sides


def cache(info):
    if (OUT/'CACHE_READY').exists():
        return
    parent = np.load(PARENT/'detail_features.npy', mmap_mode='r')
    count = len(info['views'])
    def array(name, dtype, shape):
        path = OUT/name
        return np.lib.format.open_memmap(path, mode='r+' if path.exists() else 'w+', dtype=dtype, shape=shape)
    values = array('detail_features.npy', np.float16, parent.shape)
    status = array('status.npy', np.int8, (count,))
    lengths = array('lengths.npy', np.int16, (count,))
    model = encoder().cuda()
    mean = torch.tensor([.485, .456, .406], device='cuda')[None,:,None,None]
    std = torch.tensor([.229, .224, .225], device='cuda')[None,:,None,None]
    todo = iter(np.flatnonzero(status == 0).tolist())
    started = time.monotonic()
    training_sides = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = deque()
        for _ in range(6):
            slot = next(todo, None)
            if slot is not None:
                pending.append((slot, pool.submit(prepare, info['views'][slot])))
        complete = 0
        while pending:
            slot, future = pending.popleft()
            images, positions, valid, n, sides = future.result()
            values[slot] = 0
            values[slot, :384] = parent[slot, :384]
            if images is not None:
                tensor = torch.from_numpy(images.transpose(0,3,1,2)).cuda()
                chunks = []
                with torch.inference_mode(), torch.autocast('cuda', dtype=torch.float16):
                    for batch in tensor.split(32):
                        chunks.append(model((batch.float()/255-mean)/std).float().cpu().numpy())
                features = np.concatenate(chunks)
                if not np.isfinite(features).all():
                    raise ValueError('Nonfinite DINO features')
                for feature, (hand, frame) in zip(features, positions):
                    values[slot, 384+hand*384:768+hand*384, frame] = feature
            values[slot, 1152:, :n] = valid
            lengths[slot] = n
            values.flush()
            status[slot] = 1
            status.flush()
            lengths.flush()
            if info['views'][slot]['pilot_role'] == 'train':
                training_sides.extend(sides)
            complete += 1
            if complete % 64 == 0:
                print(f'V8 cached {int(status.sum())}/{count} in {time.monotonic()-started:.1f}s', flush=True)
            slot = next(todo, None)
            if slot is not None:
                pending.append((slot, pool.submit(prepare, info['views'][slot])))
    (OUT/'geometry.json').write_text(json.dumps({
        'median_short_hand_side_training_px': float(np.median(training_sides)) if training_sides else None,
        'scope': 'Training crop geometry for views processed in this invocation; not object visibility.'}, indent=2)+'\n')
    (OUT/'CACHE_READY').write_text('Complete\n')
    del model
    torch.cuda.empty_cache()


def evaluate(info):
    info, ids, values, y, n = trainer.dataset()
    sel = np.array([i for i, slot in enumerate(ids) if info['views'][int(slot)]['pilot_role'] == 'selection'])
    check = np.array([i for i, slot in enumerate(ids) if info['views'][int(slot)]['pilot_role'] == 'check'])
    old = json.loads((PARENT/'pilot_result.json').read_text())
    artifacts = [torch.load(OUT/f'detail_{seed}.pt', weights_only=True, map_location='cpu') for seed in [43,44]]
    chosen = 'short_hand' if np.mean([a['selection_frame_macro_ap'] for a in artifacts]) > old['selection_macro_ap_mean']['detail'] else 'original_hand'
    (OUT/'selection_before_check.json').write_text(json.dumps({
        'selected': chosen, 'old_selection_ap': old['selection_macro_ap_mean']['detail'],
        'new_selection_ap': float(np.mean([a['selection_frame_macro_ap'] for a in artifacts]))}, indent=2)+'\n')
    metrics, predictions = [], []
    for a in artifacts:
        model = trainer.head_for('detail').cuda()
        model.load_state_dict(a['head_state'])
        def inputs(pick):
            x, _, _, _ = normalize(torch.from_numpy(values[pick]), n[pick], a['mean'], a['std'])
            return x.cuda()
        sp, cp = trainer.predict(model, inputs(sel)), trainer.predict(model, inputs(check))
        thresholds = calibrate(y[sel], sp, n[sel])
        metrics.append({'seed': a['seed'], 'selection': frame_metrics(y[sel], sp, n[sel]),
                        'check': frame_metrics(y[check], cp, n[check]),
                        'check_events': event_metrics(y[check], cp, n[check], thresholds)})
        predictions.append(cp)
    new = np.mean(predictions, 0)
    saved = np.load(PARENT/'check_predictions.npz')
    if not np.array_equal(saved['slots'], ids[check]) or not np.array_equal(saved['truth'], y[check]):
        raise ValueError('Parent check examples differ')
    keys = [(info['views'][int(ids[i])]['split'], info['views'][int(ids[i])]['sample_id']) for i in check]
    groups = sorted(set(keys))
    chunks = [np.array([i for i,k in enumerate(keys) if k == g]) for g in groups]
    rng = np.random.default_rng(8008)
    diffs = []
    for _ in range(500):
        draw = np.concatenate([chunks[i] for i in rng.integers(0, len(chunks), len(chunks))])
        diffs.append(frame_metrics(y[check][draw], new[draw], n[check][draw])['macro_ap']-
                     frame_metrics(y[check][draw], saved['detail'][draw], n[check][draw])['macro_ap'])
    report = {'selected_by_selection': chosen, 'new': metrics,
              'old_selection_ap': old['selection_macro_ap_mean']['detail'],
              'old_check_ap_mean': float(np.mean([m['check_frame']['macro_ap'] for m in old['results']['detail']])),
              'new_check_ap_mean': float(np.mean([m['check']['macro_ap'] for m in metrics])),
              'new_minus_old_ensemble_check_ap_95pct': np.percentile(diffs,[2.5,97.5]).tolist(),
              'scope': 'Adaptive development on reused V6 roles; not a new test. Same labels/body/context/head family; hand crops and per-frame validity changed.'}
    (OUT/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    np.savez_compressed(OUT/'check_predictions.npz', slots=ids[check], truth=y[check], lengths=n[check], detail=new)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    torch.set_num_threads(2)
    torch.cuda.set_per_process_memory_fraction(.7)
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT/'result.json').exists():
        raise RuntimeError('Completed run locked')
    info = json.loads((PARENT/'index.json').read_text())
    info['signature'] = {**info['signature'], 'version': 8,
        'parent_index_sha256': hashlib.sha256((PARENT/'index.json').read_bytes()).hexdigest(),
        'short_hand_script_sha256': hashlib.sha256((ROOT/'scripts/short_hand_v8.py').read_bytes()).hexdigest(),
        'crop_version': 'per-frame +/-2 sparse frames, q05/q95, margin max(image/25,.65 forearm), min .16 image'}
    target = OUT/'index.json'
    if target.exists() and json.loads(target.read_text()) != info:
        raise RuntimeError('Protocol changed; use new run directory')
    target.write_text(json.dumps(info, indent=2)+'\n')
    cache(info)
    trainer.OUT = OUT
    trainer.plan = lambda: info
    trainer.HEADS = ['detail']
    if not (OUT/'learnability.json').exists():
        trainer.learnability()
    trainer.train()
    evaluate(info)
