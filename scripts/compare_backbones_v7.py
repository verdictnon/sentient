"""Matched pretrained video-backbone development comparison; no official test.

Uses V6's fixed 512/64/64 group roles. Both video encoders receive exactly the
same 16 frame-index sampled, fixed-body 224px crops. Exposes temporal features,
maps token-center indices to the original frame grid, and trains matched TCNs.
This is an adaptive development experiment, not an independent test benchmark.
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
from torchvision.models.video import mvit_v2_s

from features_v6 import ROOT, fixed_boxes
from features_v3 import DATA, clean_pose
from pytorchvideo.models.hub.x3d import x3d_s
from temporal_v3 import Residual, normalize, frame_metrics, calibrate, event_metrics

OUT = ROOT / 'runs/backbone_v7'
SOURCE = ROOT / 'runs/hand_detail_v6_scale512'
CONTEXT = ROOT / 'runs/temporal_v3'
WEIGHT = ROOT / 'models/mvit_v2_s-ae3be167.pth'
NAMES = ['pose', 'x3d', 'mvit']
WIDTH = {'pose': 122, 'x3d': 314, 'mvit': 890}
SEEDS = [43, 44]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protocol():
    OUT.mkdir(parents=True, exist_ok=True)
    if not WEIGHT.exists():
        torch.hub.download_url_to_file(
            'https://download.pytorch.org/models/mvit_v2_s-ae3be167.pth',
            str(WEIGHT.with_suffix('.partial')), hash_prefix='ae3be167', progress=False)
        WEIGHT.with_suffix('.partial').replace(WEIGHT)
    index = json.loads((SOURCE / 'index.json').read_text())
    p = {
        'version': 7, 'index_sha256': digest(SOURCE / 'index.json'),
        'script_sha256': digest(ROOT / 'scripts/compare_backbones_v7.py'),
        'x3d_sha256': digest(ROOT / 'models/X3D_S.pyth'), 'mvit_sha256': digest(WEIGHT),
        'models': NAMES, 'seeds': SEEDS, 'epochs': 30, 'patience': 6,
        'inputs': 'Same fixed body box, 224px, 16 rounded linspace frame indices over each supplied clip; no annotated event boundaries/contact points used for inputs.',
        'normalization': 'Both Kinetics encoders use RGB mean .45 and std .225.',
        'temporal_mapping': 'X3D nodes at sampled frame indices; MViTv2-S initial stride-2 conv nodes at sampled indices 0,2,...14. Mean over space, linear interpolation to original frame indices with endpoint clamping. Not original elapsed time.',
        'head': '96 hidden channels; 3 residual TCN blocks, dilations 1/2/4; varying input projection parameter counts; same loss and optimization.',
        'selection': 'Best epoch and model by mean selection frame macro AP across two seeds; record before check predictions.',
        'scope': 'Reused V6 512 training/64 selection/64 check source groups; paired views stay grouped. Adaptive development only. No official test.',
        'license': 'RetailAction custom terms and pretrained asset provenance remain applicable.',
    }
    target = OUT / 'protocol.json'
    if target.exists() and json.loads(target.read_text()) != p:
        raise RuntimeError('Protocol changed; use a new output directory')
    target.write_text(json.dumps(p, indent=2) + '\n')
    return index, p


def prepare(row):
    folder = DATA / row['split'] / row['sample_id']
    name = row['group']['name']
    poses = json.loads((folder / (name + '_poses.json')).read_text())['frames']
    with av.open(str(folder / (name + '_video.mp4'))) as video:
        video.streams.video[0].thread_count = 1
        frames = [f.to_ndarray(format='rgb24') for f in video.decode(video=0)]
    n = len(frames)
    if not 4 <= n <= 32 or n != row['metadata']['total_frame_count']:
        raise ValueError('Invalid media frame count')
    cleaned = [clean_pose(poses.get(str(i + 1), {})) for i in range(n)]
    h, w = frames[0].shape[:2]
    box = fixed_boxes(cleaned, w, h)[0][0]
    indices = np.linspace(0, n - 1, 16).round().astype(int)
    unique = {i: np.asarray(Image.fromarray(frames[i]).crop(box).resize(
        (224, 224), Image.Resampling.BILINEAR)).copy() for i in set(indices)}
    array = np.stack([unique[i] for i in indices]).transpose(3, 0, 1, 2)
    return torch.from_numpy(array), indices, n


def temporal_mvit(model, value):
    value = model.conv_proj(value).flatten(2).transpose(1, 2)
    value = model.pos_encoding(value)
    shape = (model.pos_encoding.temporal_size,) + model.pos_encoding.spatial_size
    for block in model.blocks:
        value, shape = block(value, shape)
    value = model.norm(value)[:, 1:]
    t, h, w = shape
    return value.reshape(len(value), t, h, w, -1).mean((2, 3)).transpose(1, 2)


def map_nodes(value, positions, n):
    unique = np.unique(positions)
    value = np.stack([value[:, positions == p].mean(1) for p in unique], 1)
    return np.stack([np.interp(np.arange(n), unique, channel) for channel in value])


def cache(index):
    rows = index['views']
    if (OUT / 'CACHE_READY').exists():
        return
    nrows = len(rows)
    arrays = {}
    for name, width in [('x3d', 192), ('mvit', 768)]:
        target = OUT / (name + '.npy')
        arrays[name] = np.lib.format.open_memmap(target, mode='r+' if target.exists() else 'w+',
                                                dtype=np.float16, shape=(nrows, width, 32))
    status_path = OUT / 'status.npy'
    status = np.lib.format.open_memmap(status_path, mode='r+' if status_path.exists() else 'w+',
                                     dtype=np.int8, shape=(nrows,))
    x = x3d_s(pretrained=False)
    x.load_state_dict(torch.load(ROOT / 'models/X3D_S.pyth', weights_only=True, map_location='cpu')['model_state'])
    x = torch.nn.Sequential(*list(x.blocks)[:5]).eval().requires_grad_(False).cuda()
    m = mvit_v2_s(weights=None)
    m.load_state_dict(torch.load(WEIGHT, weights_only=True, map_location='cpu'))
    m = m.eval().requires_grad_(False).cuda()
    todo = iter(np.flatnonzero(status == 0).tolist())
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = deque()
        for _ in range(6):
            slot = next(todo, None)
            if slot is not None:
                pending.append((slot, pool.submit(prepare, rows[slot])))
        completed = 0
        while pending:
            slot, future = pending.popleft()
            tensor, indices, n = future.result()
            tensor = (tensor[None].cuda().float() / 255 - .45) / .225
            with torch.inference_mode(), torch.autocast('cuda', dtype=torch.float16):
                features = {'x3d': x(tensor).mean((-1, -2))[0].float().cpu().numpy(),
                            'mvit': temporal_mvit(m, tensor)[0].float().cpu().numpy()}
            for name in features:
                positions = indices if name == 'x3d' else indices[::2]
                if features[name].shape[1] != len(positions):
                    raise ValueError('Unexpected temporal token grid')
                result = map_nodes(features[name], positions, n)
                if not np.isfinite(result).all():
                    raise ValueError('Nonfinite features')
                arrays[name][slot] = 0
                arrays[name][slot, :, :n] = result
                arrays[name].flush()
            status[slot] = 1
            status.flush()
            completed += 1
            if completed % 64 == 0:
                print(f'V7 cached {int(status.sum())}/{nrows} views in {time.monotonic()-start:.1f}s', flush=True)
            slot = next(todo, None)
            if slot is not None:
                pending.append((slot, pool.submit(prepare, rows[slot])))
    (OUT / 'CACHE_READY').write_text('Complete\n')
    del x, m
    torch.cuda.empty_cache()


def head(name):
    return torch.nn.Sequential(torch.nn.Conv1d(WIDTH[name], 96, 1), torch.nn.GELU(),
                               *[Residual(96, d) for d in [1, 2, 4]],
                               torch.nn.Dropout(.2), torch.nn.Conv1d(96, 3, 1))


def predict(model, value):
    model.eval()
    with torch.inference_mode():
        return np.concatenate([model(x).sigmoid().cpu().numpy() for x in value.split(64)])


def train_and_evaluate(index, spec):
    slots = np.array([r['source_slot'] for r in index['views']])
    source = np.load(CONTEXT / 'features.npy', mmap_mode='r')
    pose = source[slots, 384:506].astype(np.float32)
    y = np.load(CONTEXT / 'labels.npy', mmap_mode='r')[slots].copy()
    lengths = np.load(CONTEXT / 'lengths.npy', mmap_mode='r')[slots].copy()
    roles = {role: np.array([i for i, r in enumerate(index['views']) if r['pilot_role'] == role])
             for role in ['train', 'selection', 'check']}
    tr, sel, check = (roles[r] for r in ['train', 'selection', 'check'])
    scores = {}
    for name in NAMES:
        values = pose if name == 'pose' else np.concatenate([
            np.load(OUT / (name + '.npy')).astype(np.float32), pose], 1)
        tx, mean, std, mask = normalize(torch.from_numpy(values[tr]), lengths[tr])
        vx, _, _, _ = normalize(torch.from_numpy(values[sel]), lengths[sel], mean, std)
        tx, vx, mask = tx.cuda(), vx.cuda(), mask.cuda()
        target = torch.from_numpy(y[tr]).cuda()
        pos = (target * mask).sum((0, 2))
        weight = ((mask.sum() - pos) / pos.clamp_min(1)).sqrt().clamp(1, 10)
        loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=weight[None, :, None], reduction='none')
        scores[name] = []
        for seed in SEEDS:
            path = OUT / f'{name}_{seed}.pt'
            if not path.exists():
                torch.manual_seed(seed)
                model = head(name).cuda()
                opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=.03)
                best, bad, history = -1, 0, []
                start = time.monotonic()
                for epoch in range(1, 31):
                    torch.manual_seed(seed * 100 + epoch)
                    model.train()
                    for batch in torch.randperm(len(tr), device='cuda').split(32):
                        opt.zero_grad(set_to_none=True)
                        logits = model(tx[batch])
                        loss = (loss_fn(logits, target[batch]) * mask[batch]).sum() / (mask[batch].sum() * 3)
                        loss.backward()
                        torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
                        opt.step()
                    metric = frame_metrics(y[sel], predict(model, vx), lengths[sel])
                    history.append({'epoch': epoch, 'selection': metric})
                    if metric['macro_ap'] > best:
                        best, bad, best_epoch = metric['macro_ap'], 0, epoch
                        state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                    else:
                        bad += 1
                    if bad >= 6:
                        break
                torch.save({'state': state, 'mean': mean, 'std': std, 'selection_ap': best,
                            'epoch': best_epoch, 'history': history, 'seconds': time.monotonic()-start,
                            'protocol': spec}, path)
            artifact = torch.load(path, weights_only=True, map_location='cpu')
            scores[name].append(artifact['selection_ap'])
        print(f'V7 {name} selection AP {np.mean(scores[name]):.4f}', flush=True)
    selected = max(NAMES, key=lambda name: np.mean(scores[name]))
    (OUT / 'selection_before_check.json').write_text(json.dumps(
        {'selected': selected, 'selection_scores': scores}, indent=2) + '\n')
    report = {'protocol': spec, 'selected': selected, 'results': {},
              'support_groups': {role: len({(index['views'][i]['split'], index['views'][i]['sample_id'])
                                         for i in ids}) for role, ids in roles.items()}}
    predictions = {}
    for name in NAMES:
        values = pose if name == 'pose' else np.concatenate([
            np.load(OUT / (name + '.npy')).astype(np.float32), pose], 1)
        model_results, check_predictions = [], []
        for seed in SEEDS:
            artifact = torch.load(OUT / f'{name}_{seed}.pt', weights_only=True, map_location='cpu')
            model = head(name).cuda()
            model.load_state_dict(artifact['state'])
            def inputs(ids):
                x, _, _, _ = normalize(torch.from_numpy(values[ids]), lengths[ids], artifact['mean'], artifact['std'])
                return x.cuda()
            sp = predict(model, inputs(sel))
            cp = predict(model, inputs(check))
            thresholds = calibrate(y[sel], sp, lengths[sel])
            model_results.append({'seed': seed, 'epoch': artifact['epoch'],
                                  'selection': frame_metrics(y[sel], sp, lengths[sel]),
                                  'check': frame_metrics(y[check], cp, lengths[check]),
                                  'check_events': event_metrics(y[check], cp, lengths[check], thresholds),
                                  'thresholds_from_selection': thresholds})
            check_predictions.append(cp)
        predictions[name] = np.mean(check_predictions, 0)
        report['results'][name] = model_results
    keys = [(index['views'][i]['split'], index['views'][i]['sample_id']) for i in check]
    groups = sorted(set(keys))
    group_ids = [np.array([i for i, key in enumerate(keys) if key == group]) for group in groups]
    rng = np.random.default_rng(7007)
    differences = []
    for _ in range(500):
        draw = np.concatenate([group_ids[i] for i in rng.integers(0, len(groups), len(groups))])
        a = frame_metrics(y[check][draw], predictions['mvit'][draw], lengths[check][draw])['macro_ap']
        b = frame_metrics(y[check][draw], predictions['x3d'][draw], lengths[check][draw])['macro_ap']
        differences.append(a-b)
    report['mvit_minus_x3d_ensemble_check_ap_95pct'] = np.percentile(differences, [2.5, 97.5]).tolist()
    (OUT / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    np.savez_compressed(OUT / 'check_predictions.npz', indices=check, truth=y[check],
                        lengths=lengths[check], **predictions)
    print(json.dumps({'selected': selected, 'mean_check_ap': {
        name: np.mean([m['check']['macro_ap'] for m in report['results'][name]]) for name in NAMES},
        'mvit_minus_x3d_ci': report['mvit_minus_x3d_ensemble_check_ap_95pct']}, indent=2), flush=True)


if __name__ == '__main__':
    torch.set_num_threads(2)
    torch.cuda.set_per_process_memory_fraction(.7)
    index, spec = protocol()
    if (OUT / 'result.json').exists():
        raise RuntimeError('Completed comparison locked; inspect existing result')
    cache(index)
    train_and_evaluate(index, spec)
