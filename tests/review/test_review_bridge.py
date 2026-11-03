"""Bridge regressions. All declared-human cases here are synthetic temp fixtures."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import av
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import review_bridge as b


class ReviewBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'contracts/v1').mkdir(parents=True)
        shutil.copyfile(b.ROOT/'contracts/v1/messages.schema.json',self.root/'contracts/v1/messages.schema.json')
        folder = self.root/'data/train/unit'
        folder.mkdir(parents=True)
        views = []
        for camera,color in [('rank0','red'),('rank1','blue')]:
            video = folder/(camera+'.mp4')
            with av.open(str(video),'w') as output:
                stream = output.add_stream('libx264',rate=10)
                stream.width,stream.height = 160,120
                stream.pix_fmt = 'yuv420p'
                stream.time_base = Fraction(1,1000)
                stream.codec_context.time_base = Fraction(1,1000)
                stream.codec_context.max_b_frames = 0
                for time in [5000,5100,5350,5700,6100,6600,7200,7900]:
                    frame = av.VideoFrame.from_image(Image.new('RGB',(160,120),color))
                    frame.pts,frame.time_base = time,Fraction(1,1000)
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)
            pose = folder/(camera+'.json')
            b.write_json(pose,{'frames':{str(i+1):{'points':[[.2,.3],[.4,.6]],'confidence':[1,1]} for i in range(8)}})
            views.append({'camera_view':camera,'video_ref':str(video.relative_to(self.root)),
                          'sha256':b.sha256(video),'pose_ref':str(pose.relative_to(self.root)),
                          'source_annotations_not_human_reverified':[
                              {'action':'touch','start_frame':2,'end_frame_exclusive':4},
                              {'action':'put','start_frame':5,'end_frame_exclusive':7}],
                          'frame_index_base':0,'source_kind':'sparse_clip','capture_time_utc':None})
        self.queue = self.root/'queue.jsonl'
        b.write_jsonl(self.queue,[{'clip_group':'train/unit','role':'train','views':views,
                                 'review_questions':['Visible?'],'review_reasons':['Unverified fixture']}])
        b.write_json(self.root/'runs/hand_detail_v6_scale512/index.json',{'views':[
            {'split':'train','sample_id':'unit','group':{'name':v['camera_view']},
             'pilot_role':'train','remote_sha256':v['sha256']} for v in views]})
        self.pack_path = b.prepare_pack(self.queue,self.root/'pack',root=self.root)
        self.pack = b.read_json(self.pack_path)
        self.entry = self.pack['entries'][0]
        self.receipt_path = self.root/'receipt.json'
        self.receipt = {'receipt_version':'sentient.review-import/1','pack_id':self.pack['pack_id'],
                        'pack_sha256':b.sha256(self.pack_path),'app_url':'http://127.0.0.1:1','entries':[
                            {'entry_id':e['entry_id'],'video_id':b.identity(['video',e['entry_id']]),
                             'bindings':[{'analysis_id':b.identity(['analysis',e['entry_id']]),'geometry_id':'geometry'}]}
                            for e in self.pack['entries']]}
        b.write_json(self.receipt_path,self.receipt)
        self.reviews = self.root/'reviews.jsonl'
        self.proofs = self.root/'attestations.jsonl'
        self.output = self.root/'dataset.json'
        self.reader = b.ReviewReader(self.pack_path,self.receipt_path,self.root)

    def review(self,revision=1,entry_index=0,target_index=0,label='touch',decision='corrected',indices=None):
        entry = self.pack['entries'][entry_index]
        imported = self.receipt['entries'][entry_index]
        binding = imported['bindings'][0]
        target = entry['targets'][target_index]
        wire = [r for r in entry['wire_records'] if r['candidate_id']==target['candidate_id']]
        score = wire[-1]
        frames = entry['media_index']['frames']
        selected = [f for f in frames if score['start_media_ms']<=f['media_ms']<=score['end_media_ms']] if indices is None else [frames[i] for i in indices]
        key = entry['subject_track_key']
        return {'annotation_schema_version':'sentient.review/1',
                'annotation_id':b.identity([imported['video_id'],binding['analysis_id'],target['target_id'],revision]),
                'revision':revision,'created_at_utc':'2026-11-02T10:00:00Z',
                'video_id':imported['video_id'],'media_sha256':entry['media_sha256'],
                'store_id':key[0],'camera_id':key[1],'stream_session_id':key[2],'local_track_id':key[3],
                'global_visit_id':None,'subject_track_key':key,'collection_session':entry['collection_session'],
                'collection_day':None,'capture_time_utc':None,'clock_status':'playback_only',
                'start_media_ms':selected[0]['media_ms'],'end_media_ms':selected[-1]['media_ms'],
                'frame_indices':[f['frame_index'] for f in selected],
                'original_media_times_ms':[f['media_ms'] for f in selected],
                'source_frame_pts':[{k:f[k] for k in ['frame_index','pts','time_base']} for f in selected],
                'human':{'reviewer':'Synthetic unit-test reviewer','decision':decision,'label':label,
                         'visibility':'clear','uncertainty':'low','notes':'Synthetic regression fixture'},
                'model_outputs':[score],'source_contract_version':'1.0','source_records':wire,
                'provenance':{**binding,'target_id':target['target_id'],'source_kind':'human_review',
                              'media_manifest':imported['video_id']+'.json'}}

    def build(self,rows,kind='human',fixture=False):
        b.write_jsonl(self.reviews,rows)
        b.attest(self.reviews,self.proofs,list(dict.fromkeys(r['annotation_id'] for r in rows)),kind,'Synthetic test attestor')
        return b.build_dataset(self.pack_path,self.receipt_path,[self.reviews],self.output,self.proofs,self.root,
                               fixture_output=self.root/'fixture.json' if fixture else None)

    def test_pack_is_repeatable_and_preserves_pairing_without_fake_detections(self):
        before = b.sha256(self.pack_path)
        b.prepare_pack(self.queue,self.root/'pack',root=self.root)
        self.assertEqual(before,b.sha256(self.pack_path))
        self.assertEqual(self.pack['groups'],1)
        self.assertEqual(self.pack['views'],2)
        for entry in self.pack['entries']:
            self.assertEqual(entry['clip_group'],'train/unit')
            self.assertFalse(entry['sampling_time_known'])
            for row in entry['wire_records']:
                self.assertIn(row['type'],['action.candidate','action.scored'])
                if row['type']=='action.scored':
                    self.assertIsNone(row['scores'])
                    self.assertEqual(row['action'],'unknown')

    def test_strict_json_rejects_duplicates_and_nonfinite_values(self):
        for text in ['{"a":1,"a":2}','{"v":NaN}','{"v":1e999}']:
            with self.subTest(text=text),self.assertRaises(b.BridgeError):
                b.parse(text)

    def test_source_training_roles_and_annotation_bounds_are_enforced(self):
        original = b.read_jsonl(self.queue)[0]
        for mode in ['role','bounds']:
            row = copy.deepcopy(original)
            if mode=='role':
                row['role']='check'
            else:
                row['views'][0]['source_annotations_not_human_reverified'][0]['end_frame_exclusive']=100
            queue = self.root/(mode+'.jsonl')
            b.write_jsonl(queue,[row])
            with self.subTest(mode=mode),self.assertRaises(b.BridgeError):
                b.prepare_pack(queue,self.root/(mode+'-pack'),root=self.root)
            self.assertFalse((self.root/(mode+'-pack')).exists())

    def test_escaping_media_paths_are_rejected(self):
        with self.assertRaises(b.BridgeError):
            b.local_path(self.root,'../outside.mp4')
        with self.assertRaises(b.BridgeError):
            b.local_path(self.root,'C:\\video.mp4')

    def test_a_source_training_filename_cannot_promote_a_held_development_role(self):
        index_path = self.root/'runs/hand_detail_v6_scale512/index.json'
        index = b.read_json(index_path)
        index['views'][0]['pilot_role']='check'
        b.write_json(index_path,index)
        with self.assertRaises(b.BridgeError):
            b.prepare_pack(self.queue,self.root/'leaky-pack',root=self.root)

    def test_media_and_pose_changes_invalidate_the_pack(self):
        pose = self.root/self.entry['pose_ref']
        pose.write_text('{}')
        with self.assertRaises(b.BridgeError):
            b.load_pack(self.pack_path,self.root)

    def test_reader_rejects_broken_media_subject_time_and_source_bindings(self):
        original = self.review()
        mutations = {
            'media':lambda r:r.update(media_sha256='0'*64),
            'subject':lambda r:r.update(local_track_id='wrong'),
            'utc':lambda r:r.update(capture_time_utc='2026-11-02T00:00:00Z'),
            'frames':lambda r:r['frame_indices'].reverse(),
            'playback_origin':lambda r:r['original_media_times_ms'].__setitem__(0,0),
            'pts':lambda r:r['source_frame_pts'][0].update(pts=0),
            'span':lambda r:r.update(start_media_ms=0),
            'source':lambda r:r['source_records'][-1].update(reason='changed'),
            'outputs':lambda r:r.update(model_outputs=[]),
            'target':lambda r:r['provenance'].update(target_id='record:forged'),
            'geometry':lambda r:r['provenance'].update(geometry_id='wrong'),
            'label':lambda r:r['human'].update(label='not-an-app-label'),
            'reviewer':lambda r:r['human'].update(reviewer=' '),
            'revision':lambda r:r.update(revision=9),
        }
        self.reader.validate(original)
        for name,mutate in mutations.items():
            row = copy.deepcopy(original)
            mutate(row)
            with self.subTest(name=name),self.assertRaises(b.BridgeError):
                self.reader.validate(row)

    def test_latest_pending_review_retracts_older_accepted_supervision(self):
        old,new = self.review(),self.review(2,decision='pending',label='unknown')
        result = self.build([new,old,old])
        self.assertEqual(result['latest_targets'],1)
        self.assertEqual(result['eligible_records'],0)
        reasons = b.read_json(self.output)['excluded_latest_reviews'][0]['reasons']
        self.assertIn('decision_pending',reasons)

    def test_batch_attestation_never_resurrects_an_older_reviewers_label(self):
        old,new = self.review(),self.review(2,decision='pending',label='unknown')
        new['human']['reviewer']='Another synthetic reviewer'
        b.write_jsonl(self.reviews,[new,old])
        self.assertEqual(b.latest_ids_for_reviewer(self.reviews,old['human']['reviewer']),[])
        self.assertEqual(b.latest_ids_for_reviewer(self.reviews,new['human']['reviewer']),[new['annotation_id']])

    def test_duplicate_identical_exports_do_not_duplicate_training(self):
        r = self.review()
        self.assertEqual(self.build([r,r])['eligible_records'],1)
        self.assertEqual(len(b.load_dataset(self.output,self.pack_path,self.receipt_path,self.root)),1)

    def test_conflicting_same_revision_fails_without_replacing_output(self):
        r = self.review()
        self.build([r])
        before = self.output.read_bytes()
        other = copy.deepcopy(r)
        other['human']['notes']='conflicting payload'
        b.write_jsonl(self.reviews,[r,other])
        with self.assertRaises(b.BridgeError):
            b.build_dataset(self.pack_path,self.receipt_path,[self.reviews],self.output,self.proofs,self.root)
        self.assertEqual(before,self.output.read_bytes())

    def test_action_supervision_leaves_other_frames_classes_unknown(self):
        r = self.review()
        self.build([r])
        examples = b.load_dataset(self.output,self.pack_path,self.receipt_path,self.root)
        self.assertEqual(len(examples),1)  # No projection into its unreviewed camera pair.
        x = examples[0]
        self.assertEqual(x['clip_group'],'train/unit')
        self.assertEqual(x['role'],'train')
        self.assertEqual(x['frame_media_times_ms'][0],5000)
        self.assertEqual(x['label_mask'][2],[False,False,True,True,False,False,False,False])
        self.assertFalse(any(x['label_mask'][0]))
        self.assertFalse(any(x['label_mask'][1]))

    def test_only_explicit_reviewed_background_is_negative_supervision(self):
        self.build([self.review(label='background')])
        x = b.load_dataset(self.output,self.pack_path,self.receipt_path,self.root)[0]
        self.assertTrue(all(mask[2] and mask[3] and sum(mask)==2 for mask in x['label_mask']))
        self.assertTrue(all(sum(values)==0 for values in x['targets']))

    def test_quality_and_origin_are_not_inferred_from_model_or_source_labels(self):
        cases = [('visibility','occluded'),('visibility','partial'),('uncertainty','high'),('decision','rejected'),('label','unknown')]
        for key,value in cases:
            r = self.review()
            r['human'][key]=value
            self.assertTrue(b.eligibility(r,None))
        r = self.review()
        b.write_jsonl(self.reviews,[r])
        result = b.build_dataset(self.pack_path,self.receipt_path,[self.reviews],self.output,root=self.root)
        self.assertEqual(result['eligible_records'],0)
        self.assertIn('unattested_review_origin',b.read_json(self.output)['excluded_latest_reviews'][0]['reasons'])

    def test_automated_fixtures_are_separate_and_refused_by_default_loader(self):
        self.assertEqual(self.build([self.review()],kind='automated_fixture',fixture=True)['eligible_records'],0)
        self.assertEqual(b.load_dataset(self.output,self.pack_path,self.receipt_path,self.root),[])
        path = self.root/'fixture.json'
        with self.assertRaises(b.BridgeError):
            b.load_dataset(path,self.pack_path,self.receipt_path,self.root)
        self.assertEqual(len(b.load_dataset(path,self.pack_path,self.receipt_path,self.root,allow_fixtures=True)),1)

    def test_attestation_cannot_survive_an_edited_review(self):
        r = self.review()
        self.build([r])
        r['human']['label']='background'
        b.write_jsonl(self.reviews,[r])
        with self.assertRaises(b.BridgeError):
            b.build_dataset(self.pack_path,self.receipt_path,[self.reviews],self.output,self.proofs,self.root)

    def test_overlapping_background_and_positive_labels_require_adjudication(self):
        positive = self.review()
        negative = self.review(target_index=1,label='background',indices=[2,3])
        with self.assertRaises(b.BridgeError):
            self.build([positive,negative])
        self.assertFalse(self.output.exists())

    def test_manifest_tampering_is_detected_even_with_recomputed_content_hash(self):
        self.build([self.review()])
        manifest = b.read_json(self.output)
        manifest['records'][0]['role']='test'
        manifest.pop('content_sha256')
        manifest['content_sha256']=b.identity(manifest)
        b.write_json(self.output,manifest)
        with self.assertRaises(b.BridgeError):
            b.load_dataset(self.output,self.pack_path,self.receipt_path,self.root)

    def test_conflicting_latest_labels_across_geometry_analyses_are_not_arbitrarily_selected(self):
        r = self.review()
        second = copy.deepcopy(r)
        second['human']['label']='background'
        second['provenance']['analysis_id']='another-analysis'
        second['annotation_id']=b.identity([second['video_id'],'another-analysis',second['provenance']['target_id'],1])
        self.receipt['entries'][0]['bindings'].append({'analysis_id':'another-analysis','geometry_id':'geometry'})
        b.write_json(self.receipt_path,self.receipt)
        reader = b.ReviewReader(self.pack_path,self.receipt_path,self.root)
        with self.assertRaises(b.BridgeError):
            reader.latest([r,second])


if __name__ == '__main__':
    unittest.main()
