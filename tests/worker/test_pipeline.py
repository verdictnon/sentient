"""Meaningful transport, timing, identity, crop and failure-bound regressions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from worker.contracts import Validator, canonical, strict_json
from worker.pipeline import Policy, propose, segments, validate_binding, score_record, crop_tensor


def track(index, ms=None, local='7', session='session-a', status='observed'):
    return {'schema_version':'1.0','type':'track.observed','record_id':f'{session}/{local}/{index}',
        'store_id':'store','camera_id':'camera','stream_session_id':session,'local_track_id':local,
        'global_visit_id':None,'association_status':'local_only','frame_index':index,
        'media_time_ms':index*100 if ms is None else ms,'capture_time_utc':None,'clock_status':'playback_only',
        'bbox_xyxy_px':[5,10,50,80],'frame_dims_px':[100,100],'score':.9,'position_status':status,
        'world_xy_m':None,'calibration_version':None,'pose_ref':None,'model_id':'fixture-tracker'}


def media(times):
    return {'frame_dims_px':[100,100],'frame_count':len(times),
        'frames':[{'frame_index':i,'pts':int(t*1000),'time_base':[1,1000000],'media_ms':t} for i,t in enumerate(times)]}


class PipelineTests(unittest.TestCase):
    def setUp(self):self.validator=Validator()

    def test_original_fixture_schema_is_supported(self):
        for text in (ROOT/'contracts/v1/examples.jsonl').read_text().splitlines():
            self.validator.validate(json.loads(text))

    def test_track_retries_deduplicate_and_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tracks.jsonl';a=track(0)
            p.write_text(canonical(a)+'\n'+canonical(a)+'\n')
            self.assertEqual(len(self.validator.read_tracks(p)),1)
            b={**a,'score':.8};p.write_text(canonical(a)+'\n'+canonical(b)+'\n')
            with self.assertRaisesRegex(ValueError,'Conflicting'):self.validator.read_tracks(p)

    def test_identity_reuse_requires_stream_scope_and_monotonicity(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tracks.jsonl'
            rows=[track(0),track(1),track(0,session='session-b')]
            p.write_text(''.join(canonical(r)+'\n' for r in rows))
            self.assertEqual(len(self.validator.read_tracks(p)),3)
            p.write_text(''.join(canonical(r)+'\n' for r in [track(1),track(0)]))
            with self.assertRaisesRegex(ValueError,'increase'):self.validator.read_tracks(p)

    def test_pts_binding_rejects_assumed_rate_and_wrong_dimensions(self):
        index=media([0,70,220,810])
        rows=[track(i,t) for i,t in enumerate([0,70,220,810])]
        validate_binding(rows,index)
        rows[1]['media_time_ms']=100
        with self.assertRaisesRegex(ValueError,'PTS'):validate_binding(rows,index)
        rows[1]['media_time_ms']=70;rows[1]['frame_dims_px']=[200,100]
        with self.assertRaisesRegex(ValueError,'dimensions'):validate_binding(rows,index)

    def test_continuous_windows_sample_actual_elapsed_time(self):
        times=[0,30,90,400,450,790,810,1100]
        rows=[track(i,t) for i,t in enumerate(times)]
        plans=propose(rows,media(times),'sha',Policy(window_ms=600,stride_ms=300))
        self.assertGreater(len(plans),1)
        first=plans[0]['candidate']
        self.assertEqual(first['frame_media_times_ms'],[times[i] for i in first['frame_indices']])
        self.assertLessEqual(first['end_media_ms'],600)
        self.assertTrue(first['sampling_time_known'])
        self.assertEqual(first['capture_elapsed_ms'],first['frame_media_times_ms'])
        for p in plans:self.validator.validate(p['candidate'])

    def test_sparse_sampling_does_not_invent_physical_time(self):
        times=[i*100 for i in range(32)]
        rows=[track(i) for i in range(32)]
        plans=propose(rows,media(times),'sha',Policy(source_kind='sparse_clip'))
        self.assertEqual([p['model_input_indices'][0] for p in plans],[0,6,12,18,19])
        self.assertEqual(plans[-1]['model_input_indices'][-1],31)
        for p in plans:
            self.assertFalse(p['candidate']['sampling_time_known'])
            self.assertIsNone(p['candidate']['capture_elapsed_ms'])
            self.validator.validate(p['candidate'])

    def test_predictions_gaps_health_and_sessions_never_become_observed_motion(self):
        rows=[track(0,0),track(1,100),track(2,200,status='predicted'),track(3,2100),track(4,2200),
              track(0,0,session='session-b'),track(1,100,session='session-b')]
        chunks=list(segments(rows,Policy(max_gap_ms=500)))
        self.assertEqual([len(c) for _,c in chunks],[2,2,2])
        self.assertTrue(all(r['position_status']=='observed' for _,c in chunks for r in c))
        health={'type':'stream.health','state':'disconnected','store_id':'store','camera_id':'camera',
                'stream_session_id':'session-b','media_time_ms':50}
        chunks=list(segments(rows+[health],Policy(max_gap_ms=500)))
        self.assertEqual([len(c) for _,c in chunks],[2,2,1,1])

    def test_healthy_frame_heartbeats_do_not_break_a_continuous_track(self):
        rows=[];times=[i*100 for i in range(20)]
        for i,t in enumerate(times):
            rows.append(track(i,t))
            rows.append({'type':'stream.health','state':'healthy','store_id':'store','camera_id':'camera',
                         'stream_session_id':'session-a','media_time_ms':t})
        plans=propose(rows,media(times),'sha',Policy())
        self.assertGreater(len(plans),0)
        self.assertEqual(plans[0]['unique_frames'],13)

    def test_ids_are_stable_but_change_with_media_subject_or_policy(self):
        times=[i*100 for i in range(20)];rows=[track(i) for i in range(20)]
        base=propose(rows,media(times),'a',Policy(source_kind='sparse_clip'))
        retry=propose(copy.deepcopy(rows),media(times),'a',Policy(source_kind='sparse_clip'))
        self.assertEqual(base,retry)
        changed=propose(rows,media(times),'b',Policy(source_kind='sparse_clip'))
        self.assertNotEqual(base[0]['candidate']['candidate_id'],changed[0]['candidate']['candidate_id'])
        changed=propose(rows,media(times),'a',Policy(source_kind='sparse_clip',stride_frames=5))
        self.assertNotEqual(base[0]['candidate']['candidate_id'],changed[0]['candidate']['candidate_id'])

    def test_no_evidence_is_not_background_and_scores_are_review_only(self):
        rows=[track(0),track(1)];plan=propose(rows,media([0,100]),'a',Policy(source_kind='sparse_clip'))[0]
        missing=score_record(plan,'model',None,'insufficient_visibility','missing')
        self.validator.validate(missing)
        self.assertIsNone(missing['scores']);self.assertTrue(missing['abstained'])
        real=score_record(plan,'model',dict(zip(['take','put','touch','background'],[.9,.05,.03,.02])),'time_unknown','review only')
        self.validator.validate(real)
        self.assertEqual(real['action'],'unknown');self.assertTrue(real['abstained'])

    def test_pil_padding_and_rgb_normalization_match_training_contract(self):
        frames={i:np.full((10,10,3),255,dtype=np.uint8) for i in range(4)}
        value=crop_tensor(frames,[-10,-10,10,10],[0,1,2,3]*3+[0])
        self.assertEqual(value.shape,(3,13,160,160))
        self.assertAlmostEqual(float(value[0,0,0,0]),-2.,places=5)
        self.assertAlmostEqual(float(value[0,0,-1,-1]),(1-.45)/.225,places=5)

    def test_invalid_and_unbounded_policy_is_rejected(self):
        for options in [{'source_kind':'automatic'},{'stride_frames':14},{'stride_ms':3000},
                        {'window_ms':float('nan')},{'batch_size':100},{'max_candidates':0}]:
            with self.subTest(options=options),self.assertRaises(ValueError):Policy(**options)
        times=[i*100 for i in range(32)];rows=[track(i) for i in range(32)]
        with self.assertRaisesRegex(ValueError,'bound'):
            propose(rows,media(times),'sha',Policy(source_kind='sparse_clip',max_candidates=1))

    def test_nonfinite_and_duplicate_json_is_not_accepted(self):
        for text in ['{"x":NaN}','{"x":1,"x":2}']:
            with self.assertRaises(ValueError):strict_json(text)
        a=track(0);a['bbox_xyxy_px'][0]=float('nan')
        with self.assertRaises(ValueError):self.validator.validate(a)


if __name__=='__main__':unittest.main()
