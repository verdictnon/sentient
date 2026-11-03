"""Compare the portable worker with the original V5 on actual source clips."""
import json
import os
from pathlib import Path
import sys
import argparse
import av
import numpy as np
import torch

from worker.model import ROOT, Runtime, sha256
from worker.pack import pose_tracks
from worker.pipeline import Policy, propose, crop_tensor
import cache_expanded
from pytorchvideo.models.hub.x3d import x3d_s
from improve_v5 import CorrectedHead


def run(model_dir, output):
    source=torch.load(ROOT/'runs/x3d_s_v5/selected_full.pt',map_location='cpu',weights_only=True)
    backbone=x3d_s(pretrained=False)
    backbone.blocks[-1].proj=torch.nn.Identity();backbone.blocks[-1].activation=torch.nn.Identity()
    backbone.load_state_dict(source['backbone_state']);backbone.eval()
    head=CorrectedHead(source['head_spec'],source['bank_size']).eval();head.load_state_dict(source['head_state'])
    runtime=Runtime(model_dir,'cpu')
    entries=json.loads((ROOT/'runs/review_pack_v2/pack.json').read_text())['entries']
    checked=[]
    for e in entries:
        split,sample=e['clip_group'].split('/')
        path=cache_expanded.DATA/split/sample/(e['camera_view']+'_video.mp4')
        poses=path.with_name(e['camera_view']+'_poses.json')
        if not path.exists() or not poses.exists() or sha256(path)!=e['media_sha256'] or sha256(poses)!=e['pose_sha256']:
            continue
        records=pose_tracks(e)
        plans=propose(records,e['media_index'],e['media_sha256'],Policy(source_kind='sparse_clip'))
        plan=plans[0]
        left,right=min(plan['model_input_indices']),max(plan['model_input_indices'])
        # Original training preprocessor; neither path receives action labels.
        reference=cache_expanded.prepare({'split':split,'sample':sample,'view':e['camera_view'],
            'nframes':e['media_index']['frame_count'],'indices':[0]},[{'left':left,'right':right}])[0][1]
        if reference is None:continue
        with av.open(str(path)) as video:
            frames={i:f.to_ndarray(format='rgb24') for i,f in enumerate(video.decode(video=0))}
        actual=crop_tensor(frames,plan['crop'],plan['model_input_indices'])
        input_error=float(np.abs(reference.numpy()-actual).max())
        if input_error>1e-6:raise ValueError('Source crop/normalization differs from the original training preprocessing')
        with torch.inference_mode():
            original=(head(backbone(reference.unsqueeze(0)))+torch.tensor(source['bias'])).softmax(1).numpy()
        portable=runtime.predict(actual[None])
        error=float(np.abs(original-portable).max())
        if error>1e-5:raise ValueError('Portable worker differs from original V5 on source frames')
        checked.append({'clip_group':e['clip_group'],'view':e['camera_view'],
            'media_sha256':e['media_sha256'],'max_input_abs_error':input_error,'max_score_abs_error':error})
        if len(checked)==3:break
    if len(checked)!=3:raise ValueError('Need three matching actual source examples for parity verification')
    result={'actual_source_windows_checked':3,'device':'cpu','source_checkpoint_sha256':sha256(ROOT/'runs/x3d_s_v5/selected_full.pt'),
        'export_sha256':runtime.metadata['model_sha256'],'examples':checked,
        'labels_or_contact_locations_used_for_inputs':False,'accuracy_evaluation':False}
    Path(output).write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',type=Path,default=ROOT/'runs/action_worker_v1/model')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(run(a.model,a.output),indent=2))
