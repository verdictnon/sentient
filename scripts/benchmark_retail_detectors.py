"""Paired real-checkpoint evaluation on a frozen, publisher-annotated CARR slice.

Native CS1 training videos select an inference configuration; native test subjects
are checked only after the decision is written. This is a small diagnostic, not
a replacement for the publisher's full benchmark or target-camera acceptance.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.p2.config import TrackingConfig
from app.p2.detector import PretrainedDetector, model_metadata
from app.p2.tracker import iou
from PIL import Image
import numpy as np


def save(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def summarize(rows):
    single=[r for r in rows if not r['other_people_unannotated']]
    tp=sum(r['matched_high'] for r in single)
    fp=sum(r['high_boxes']-int(r['matched_high']) for r in single)
    fn=len(single)-tp
    precision=tp/(tp+fp) if tp+fp else 0.
    recall=tp/(tp+fn) if tp+fn else 0.
    return {'images':len(rows),'publisher_single_person_images':len(single),
        'target_matches_high':sum(r['matched_high'] for r in rows),
        'target_matches_low':sum(r['matched_low'] for r in rows),
        'target_recall_high':sum(r['matched_high'] for r in rows)/len(rows) if rows else 0.,
        'target_recall_low':sum(r['matched_low'] for r in rows)/len(rows) if rows else 0.,
        'single_person_reference':{'tp':tp,'fp':fp,'fn':fn,'precision':precision,'recall':recall,
            'f1':2*precision*recall/(precision+recall) if precision+recall else 0.},
        'mean_forward_seconds':float(np.mean([r['forward_seconds'] for r in rows])) if rows else 0.,
        'scope':'Reference boxes identify the designated participant; precision excludes publisher-flagged multi-person frames.'}


def evaluate(folder,protocol,role,backend,cache):
    config=TrackingConfig(detector=backend,device='auto')
    detector=PretrainedDetector(config,cache)
    rows=[];started=time.monotonic()
    try:
        # Exclude a fixed warm-up from the per-frame latency measurement.
        detector.detect(Image.new('RGB',(1280,720),'black'),0)
        for video in protocol['videos']:
            if video['native_role']!=role:continue
            for sample in video['diagnostic_samples']:
                path=folder/'images'/(sample['file_name']+'.jpg')
                values=(folder/'labels'/(sample['file_name']+'.txt')).read_text().split()
                if len(values)!=5:raise ValueError('Expected exactly one designated-person annotation')
                _,cx,cy,w,h=map(float,values)
                gt=[(cx-w/2)*1280,(cy-h/2)*720,(cx+w/2)*1280,(cy+h/2)*720]
                with Image.open(path) as im:
                    if im.size!=(1280,720):raise ValueError('Source image dimensions changed')
                    image=im.convert('RGB')
                begin=time.monotonic();detections=detector.detect(image,int(sample['frame_index']))
                elapsed=time.monotonic()-begin
                high=[d for d in detections if d.score>=.5]
                rows.append({'video_id':video['video_id'],'participant_code':video['participant_code'],
                    'location_name':video['location_name'],'native_class_id':int(video['class_id']),
                    'file_name':sample['file_name']+'.jpg','source_frame_index':int(sample['frame_index']),
                    'other_people_unannotated':sample['from_multi_person_frame']=='1',
                    'target_bbox_xyxy_px':gt,'matched_high':any(iou(gt,d.box)>=.5 for d in high),
                    'matched_low':any(iou(gt,d.box)>=.5 for d in detections),'high_boxes':len(high),
                    'best_iou_low':max([iou(gt,d.box) for d in detections],default=0.),
                    'forward_seconds':elapsed,
                    'detections':[{'box':list(d.box),'score':d.score} for d in detections]})
        result={'backend':backend,'role':role,'config':config.to_dict(),'runtime':detector.metadata,
            'metrics':summarize(rows),'by_class':{str(c):summarize([r for r in rows if r['native_class_id']==c]) for c in range(6)},
            'performance':{'elapsed_seconds_including_load_excluded':time.monotonic()-started,**detector.memory()},
            'rows':rows}
    finally:detector.close()
    return result


def run(folder,output,cache):
    folder=Path(folder);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if (output/'summary.json').exists():raise ValueError('Completed benchmark is immutable; use another output directory')
    protocol=json.loads((folder/'protocol.json').read_text())
    plan={'format':'sentient.detector-diagnostic/1','dataset_doi':protocol['doi'],
        'protocol_sha256':hashlib.sha256((folder/'protocol.json').read_bytes()).hexdigest(),
        'models':['rtdetrv2','rtdetrv2_r34'],'model_assets':{b:model_metadata(cache,b) for b in ['rtdetrv2','rtdetrv2_r34']},
        'selection_role':'native_CS1_train','check_role':'native_CS1_test',
        'matching_iou':.5,'high_score_threshold':.5,'low_score_threshold':.1,
        'selection_rule':'Larger model only if single-person reference F1 improves >=0.02 and target recall does not fall; otherwise retain S. No global default promotion from this small diagnostic.',
        'precision_scope':'Publisher-flagged single-person images only; other people are not annotated in CARR',
        'no_CARR_training_or_label_mapping':True}
    save(output/'plan-before-inference.json',plan)
    results={}
    for role in ['train','test']:
        results[role]={}
        for backend in plan['models']:
            result=evaluate(folder,protocol,role,backend,cache)
            results[role][backend]=result
            save(output/f'{role}-{backend}.json',result)
            print(role,backend,json.dumps(result['metrics']),flush=True)
        if role=='train':
            small=results[role]['rtdetrv2']['metrics'];large=results[role]['rtdetrv2_r34']['metrics']
            improved=(large['single_person_reference']['f1']-small['single_person_reference']['f1']>=.02 and
                large['target_recall_high']>=small['target_recall_high'])
            decision={'chosen_for_hd_diagnostic':'rtdetrv2_r34' if improved else 'rtdetrv2',
                'selection_results':{b:r['metrics'] for b,r in results[role].items()},
                'test_predictions_not_yet_generated':True,'global_default_changed':False}
            save(output/'selection-before-check.json',decision)
    groups=sorted({r['video_id'] for r in results['test']['rtdetrv2']['rows']})
    rng=np.random.default_rng(43);differences=[]
    for _ in range(1000):
        chosen=rng.choice(groups,len(groups),replace=True)
        stats=[]
        for b in plan['models']:
            by_group=defaultdict(list)
            for row in results['test'][b]['rows']:by_group[row['video_id']].append(row)
            stats.append(summarize([r for g in chosen for r in by_group[g]])['single_person_reference']['f1'])
        differences.append(stats[1]-stats[0])
    summary={'format':plan['format'],'decision':decision,
        'check_results':{b:r['metrics'] for b,r in results['test'].items()},
        'paired_video_bootstrap_f1_large_minus_small_95_interval':np.quantile(differences,[.025,.975]).tolist(),
        'limitations':['Small diagnostic slice, not full CARR or deployment accuracy.',
            'Single-person precision uses the publisher flag; multi-person precision is unavailable.',
            'Native labels are coarse activities and do not evaluate pickup/return event recognition.',
            'Some source videos are curated with frame gaps; only contiguous selected runs become demo MP4s.',
            'CS1 subject separation is preserved; stores may overlap between roles.']}
    save(output/'summary.json',summary)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder',type=Path,default=ROOT/'data/public_retail_hd/carr')
    p.add_argument('--output',type=Path,default=ROOT/'runs/full_audit_2026-11-03/detector_benchmark')
    p.add_argument('--cache',type=Path,default=ROOT/'runs/action_worker_v1/p2_models')
    a=p.parse_args();print(json.dumps(run(a.folder,a.output,a.cache),indent=2))
