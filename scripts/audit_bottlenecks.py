"""Read-only training-data diagnostics. No fitting or test-set model selection."""
import json
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
from features_v3 import ROOT,DATA,clean_pose,crop_box


def quantiles(values):
    return {str(q):float(np.percentile(values,q)) for q in [5,25,50,75,95]} if values else {}


def contains(box,point):return box is not None and box[0]<=point[0]<=box[2] and box[1]<=point[1]<=box[3]


def main():
    rows=json.loads((DATA/'manifest.json').read_text())['views'];classes=['take','put','touch']
    totals=defaultdict(Counter);stats={c:defaultdict(list) for c in classes};ratios=[];durations=[]
    source=json.loads((ROOT/'data/retailaction_inventory/mapped_samples.json').read_text())
    source_counts={s:{c:len({r['sample_id'] for r in source if r['split']==s and c in r['action_classes']}) for c in classes} for s in ['train','validation','test']}
    for row in rows:
        if row['role']!='train':continue
        file=DATA/row['split']/row['sample_id']/(row['group']['name']+'_poses.json')
        if not file.exists():continue
        records=json.loads(file.read_text())['frames'];n=int(row['metadata']['total_frame_count']);w=int(row['metadata']['frame_width']);h=int(row['metadata']['frame_height'])
        decoded=[clean_pose(records.get(str(i+1),{})) for i in range(n)]
        bodies=[crop_box(p,c,w,h) for p,c in decoded];valid=[i for i,b in enumerate(bodies) if b is not None]
        if not valid:continue
        bodies=[box or bodies[min(valid,key=lambda j:abs(i-j))] for i,box in enumerate(bodies)]
        hands=[crop_box(p,c,w,h,True) or b for (p,c),b in zip(decoded,bodies)]
        for b,hand in zip(bodies,hands):ratios.append((hand[2]-hand[0])/(b[2]-b[0]))
        durations.append(float(row['segment_duration_s']))
        for event in (row.get('ground_truth') or {}).get('detections',[]):
            label=event['label']
            if label not in classes:continue
            left,right=map(int,event['support']);chosen=list(range(max(0,left-1),min(n,right)))
            if not chosen:continue
            point=(float(event['x'])*w,float(event['y'])*h);out=stats[label];out['support_frames'].append(len(chosen))
            out['body_contains_contact_any_frame'].append(any(contains(bodies[i],point) for i in chosen))
            out['hand_region_contains_contact_any_frame'].append(any(contains(hands[i],point) for i in chosen))
            out['body_contains_contact_fraction_frames'].append(np.mean([contains(bodies[i],point) for i in chosen]))
            out['hand_contains_contact_fraction_frames'].append(np.mean([contains(hands[i],point) for i in chosen]))
            out['hand_to_body_side_ratio'].extend([(hands[i][2]-hands[i][0])/(bodies[i][2]-bodies[i][0]) for i in chosen])
            out['hypothetical_20px_detail_after_body_resize'].extend([20*160/(bodies[i][2]-bodies[i][0]) for i in chosen])
            out['hypothetical_20px_detail_after_hand_resize'].extend([20*160/(hands[i][2]-hands[i][0]) for i in chosen])
    geometry={}
    for label,out in stats.items():
        geometry[label]={'annotated_view_events_checked':len(out['support_frames'])}
        for key,values in out.items():
            geometry[label][key]=float(np.mean(values)) if 'contains_contact' in key else quantiles(values)
    # Time structure is measured only where original per-frame timestamps exist.
    from datetime import datetime
    gaps=[];spans=[];timestamp_views=0;missing=0
    base=ROOT/'data/retailaction/train'
    for path in sorted(base.glob('*/metadata.json')):
        value=json.loads(path.read_text());content=value.get('content',value)
        for camera in content.get('action_cam',{}).values():
            times=camera.get('frame_timestamps')
            if not times:missing+=1;continue
            try:times=[datetime.fromisoformat(t) for t in times]
            except (TypeError,ValueError):continue
            timestamp_views+=1;gaps.extend([(b-a).total_seconds() for a,b in zip(times,times[1:])])
            spans.extend([(times[i+12]-times[i]).total_seconds() for i in range(max(0,len(times)-12))])
    # Exact temporal convolutional receptive field through the frozen V3 prefix.
    import torch
    from pytorchvideo.models.hub.x3d import x3d_s
    model=x3d_s(pretrained=False);temporal_kernels=[]
    for stage in list(model.blocks)[:5]:
        for module in stage.modules():
            if isinstance(module,torch.nn.Conv3d) and module.kernel_size[0]>1:temporal_kernels.append(int(module.kernel_size[0]))
    # All X3D temporal strides in these blocks are one. Projection branches are temporal 1x1.
    strides=[int(m.stride[0]) for stage in list(model.blocks)[:5] for m in stage.modules() if isinstance(m,torch.nn.Conv3d)]
    if any(s!=1 for s in strides):raise ValueError("Receptive-field calculation assumes temporal stride one")
    receptive=1+sum(k-1 for k in temporal_kernels)
    result={'scope':'V3 training-role geometry only; original training pilot timing only. Ground-truth contact points used for diagnosis, never as model crop inputs.',
        'source_positive_clip_groups':source_counts,'crop_diagnostics':geometry,
        'all_training_hand_to_body_side_ratio':quantiles(ratios),'fraction_hand_crop_not_tighter_than_body':float(np.mean(np.array(ratios)>=1)),
        'original_segment_duration_s':quantiles(durations),'original_timestamp_views_checked':timestamp_views,
        'original_timestamp_views_missing':missing,'original_interframe_gap_s':quantiles(gaps),'original_13_frame_span_s':quantiles(spans),
        'v3_frozen_prefix_convolution_only_receptive_field_frames':receptive,'temporal_convolution_kernel_sizes':temporal_kernels,
        'cautions':['Contact-point inclusion is only a crop-coverage proxy, not measured item visibility or bounding-box recall.',
            '20px projected detail is an illustration, not measured item size.',
            'Convolution-only receptive field; squeeze-excitation additionally pools across the whole clip. Neither measures the learned effective receptive field or proves a cause of error.',
            'Paired camera view events are correlated; counts are not unique independent shoppers.']}
    target=ROOT/'research/model_bottleneck_audit_2026-11-02.json';target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'source_counts':source_counts,'hand_crop_not_tighter_fraction':result['fraction_hand_crop_not_tighter_than_body'],
        'crop_summary':{k:{'view_events':v['annotated_view_events_checked'],'body_contact_covered':v['body_contains_contact_any_frame'],
            'hand_contact_covered':v['hand_region_contains_contact_any_frame'],'median_support_frames':v['support_frames']['50']} for k,v in geometry.items()},
        'temporal_receptive_field':receptive,'timestamp_views':timestamp_views,'13_frame_span':result['original_13_frame_span_s']},indent=2))


if __name__=='__main__':main()
