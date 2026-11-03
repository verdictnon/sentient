from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from app.contracts import ContractValidator
from app.p1.catalog import Catalog
from app.p1.demo import make_video,fixture_geometry
from app.p1.geometry import load_geometry,save_geometry
from app.p1.media import register
from app.p1.server import create_app
from app.p1.workspace import evidence,save_review
from app.p2.config import TrackingConfig
from app.p2.jobs import JobManager
from app.p2.persistence import read_database
from app.storage import SQLiteStore
from helpers import FixtureFactory,wait_job


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media=tempfile.TemporaryDirectory();cls.path=make_video(Path(cls.media.name)/'engineering.mp4',times=list(range(0,4100,100)),width=100,height=100)
    @classmethod
    def tearDownClass(cls):cls.media.cleanup()
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.catalog=Catalog(Path(self.directory.name)/'workspace')
        self.video=register(self.catalog,self.path,store_id='test-store',camera_id='camera',stream_session_id='session')
        save_geometry(self.catalog,self.video['id'],fixture_geometry(self.video['frame_dims_px']))
        self.factory=FixtureFactory();self.manager=JobManager(self.catalog,self.factory)
        self.addCleanup(self.manager.shutdown)
    def run_job(self,**parameters):
        return wait_job(self.manager,self.manager.start(self.video['id'],parameters=parameters)['id'])
    def records(self,job):return [json.loads(t) for t in Path(job['output_path']).read_text().splitlines()]

    def test_generated_v1_records_preserve_frames_pts_identity_and_health(self):
        job=self.run_job();self.assertEqual(job['status'],'completed',job.get('error'))
        rows=self.records(job)
        for row in rows:ContractValidator().validate(row)
        samples=[r for r in rows if r['type']=='track.observed']
        self.assertTrue(samples)
        self.assertEqual({r['stream_session_id'] for r in samples},{'session'})
        self.assertEqual({r['local_track_id'] for r in samples},{job['tracking_epoch']+':1'})
        self.assertTrue(all(r['media_time_ms']==r['frame_index']*100 for r in samples))
        self.assertTrue(all(r['capture_time_utc'] is None and r['global_visit_id'] is None for r in rows if 'global_visit_id' in r))
        self.assertEqual([r['state'] for r in rows if r['type']=='stream.health'][-1],'ended')
        self.assertTrue(all(r['bbox_xyxy_px'][0]<r['bbox_xyxy_px'][2] for r in samples))
    def test_predictions_are_stored_but_do_not_become_observed_positions(self):
        job=self.run_job();rows=self.records(job)
        frames={r['frame_index']:r for r in rows if r['type']=='track.observed'}
        self.assertEqual(frames[0]['position_status'],'observed')
        self.assertEqual(frames[1]['position_status'],'predicted')
        self.assertEqual(frames[2]['position_status'],'observed')
        self.assertEqual(job['inference_frames'],21)
        self.assertEqual(job['predicted_records'],20)
    def test_tracks_drive_existing_line_dwell_and_review_workflow(self):
        job=self.run_job();summary=job['summary']
        self.assertEqual(summary['inward_crossings'],1)
        self.assertEqual(summary['complete_observed_dwell_ms'],1400)
        table=evidence(self.catalog,job['analysis_id'])
        target=next(e for e in table['events'] if e['kind']=='entrance.inward')
        annotation=save_review(self.catalog,self.video['id'],{'analysis_id':job['analysis_id'],'target_id':target['target_id'],
            'request_id':'p2-review','expected_revision':0,'reviewer':'Engineering test','decision':'accepted','label':'inward',
            'visibility':'clear','uncertainty':'low','notes':'Fixture only','start_media_ms':2400.,'end_media_ms':2400.})
        self.assertEqual(annotation['stream_session_id'],'session')
        self.assertEqual(annotation['local_track_id'],job['tracking_epoch']+':1')
        self.assertEqual(annotation['annotation_schema_version'],'sentient.review/1')
    def test_replay_and_repeat_request_are_idempotent(self):
        job=self.run_job()
        with SQLiteStore(job['database_path']) as store:before=store.events(),store.inputs()
        repeated=self.manager.start(self.video['id'])
        self.assertEqual(repeated['id'],job['id']);self.assertEqual(len(self.factory.created),1)
        summary=self.manager.replay(job['id'])
        self.assertEqual(summary['events_added'],0);self.assertEqual(summary['records_inserted'],0)
        with SQLiteStore(job['database_path']) as store:self.assertEqual((store.events(),store.inputs()),before)
    def test_model_config_geometry_and_sessions_scope_separate_runs(self):
        first=self.run_job()
        second=self.run_job(run_revision=2)
        self.assertNotEqual(first['id'],second['id']);self.assertNotEqual(first['tracking_epoch'],second['tracking_epoch'])
        geometry=load_geometry(self.catalog,self.video['id']);changed=deepcopy(geometry['geometry'])
        changed['shapes'][0]['name']='Other saved geometry'
        saved=save_geometry(self.catalog,self.video['id'],changed)
        third=wait_job(self.manager,self.manager.start(self.video['id'],saved['id'])['id'])
        self.assertNotEqual(first['id'],third['id'])
        self.factory.signature='d';fourth=self.run_job();self.assertNotEqual(third['id'],fourth['id'])
        other=register(self.catalog,self.path,store_id='test-store',camera_id='camera',stream_session_id='reconnected')
        fifth=wait_job(self.manager,self.manager.start(other['id'])['id'])
        self.assertNotEqual(fifth['tracking_epoch'],fourth['tracking_epoch'])
        self.assertEqual(self.records(fifth)[0]['stream_session_id'],'reconnected')
    def test_empty_scene_has_health_and_no_invented_people_or_zero_accuracy_claim(self):
        self.factory.mode='empty';job=self.run_job()
        self.assertEqual(job['status'],'completed');self.assertEqual(job['observed_records'],0)
        self.assertTrue(all(r['type']=='stream.health' for r in self.records(job)))
        self.assertEqual(job['summary']['events_total'],0)
    def test_failure_cleans_model_and_does_not_publish_successful_analysis(self):
        self.factory.mode='error';job=self.run_job()
        self.assertEqual(job['status'],'error');self.assertIn('inference failure',job['error'])
        self.assertIsNone(job['analysis_id']);self.assertFalse(job['artifacts_complete'])
        self.assertFalse(Path(job['output_path']).exists());self.assertTrue(self.factory.created[0].closed)
        partial=Path(job['output_path']).with_suffix('.partial.jsonl')
        records=[json.loads(line) for line in partial.read_text().splitlines()]
        self.assertEqual(records[-1]['state'],'disconnected')
        self.assertIsNotNone(job['metrics'])
    def test_failed_retry_uses_new_attempt_without_overwriting_diagnostics(self):
        self.factory.mode='error';failed=self.run_job();old=Path(failed['folder'])/'run-report.json'
        before=old.read_bytes();self.factory.mode='moving'
        success=wait_job(self.manager,self.manager.start(self.video['id'])['id'])
        self.assertEqual(success['status'],'completed');self.assertEqual(success['attempt'],2)
        self.assertEqual(old.read_bytes(),before)
    def test_cancellation_is_explicit_and_cleanup_is_safe(self):
        self.factory.delay=.05;started=self.manager.start(self.video['id'])
        self.manager.cancel(started['id']);cancelled=wait_job(self.manager,started['id'])
        self.assertEqual(cancelled['status'],'cancelled');self.assertFalse(cancelled['artifacts_complete'])
        self.assertIsNone(cancelled['analysis_id'])
    def test_cleanup_failure_does_not_publish_and_can_retry_same_run(self):
        self.factory.mode='cleanup_error';failed=self.run_job()
        self.assertEqual(failed['status'],'error');self.assertIsNone(failed['analysis_id'])
        with self.catalog.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0],0)
        self.factory.mode='moving';success=self.run_job()
        self.assertEqual(success['status'],'completed');self.assertEqual(success['attempt'],2)
        with self.catalog.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0],1)
    def test_queue_checkout_geometry_does_not_fabricate_service_from_person_boxes(self):
        geometry=load_geometry(self.catalog,self.video['id'])['geometry']
        geometry['shapes'] += [{'id':'q','name':'Queue','kind':'queue','points':[[20,0],[50,0],[50,100],[20,100]]},
                               {'id':'c','name':'Checkout','kind':'checkout','points':[[50,0],[85,0],[85,100],[50,100]]}]
        geometry['active'].update(queue='q',checkout='c');save_geometry(self.catalog,self.video['id'],geometry)
        job=self.run_job();self.assertNotIn('queue',job['p0_config'])
        with SQLiteStore(job['database_path']) as store:self.assertFalse(any(e['kind'].startswith('queue.') for e in store.events()))
    def test_processing_report_contains_actual_duration_and_memory_scope(self):
        job=self.run_job();metrics=job['metrics']
        self.assertGreater(metrics['processing_seconds'],0);self.assertGreater(metrics['process_rss_peak_bytes'],0)
        self.assertEqual(metrics['frames_decoded'],41);self.assertIn('process',metrics['memory_scope'])
    def test_vfr_and_nonzero_pts_keep_original_frame_alignment(self):
        path=make_video(Path(self.directory.name)/'vfr.mp4',times=[0,100,350,700],width=100,height=100,start_ms=5000)
        video=register(self.catalog,path,store_id='test-store',camera_id='camera',stream_session_id='vfr')
        job=wait_job(self.manager,self.manager.start(video['id'])['id'])
        self.assertEqual(job['status'],'completed',job.get('error'))
        rows=[r for r in self.records(job) if r['type']=='track.observed']
        self.assertEqual([r['media_time_ms'] for r in rows],[5000,5100,5350,5700])
        self.assertEqual(job['inference_frames'],3)


class TrackingApiTests(unittest.TestCase):
    def test_api_track_inspection_exact_frames_and_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video_path=make_video(root/'source.mp4',times=list(range(0,4100,100)),width=100,height=100)
            with TestClient(create_app(root/'workspace',detector_factory=FixtureFactory())) as client:
                video=client.post('/api/videos/import',json={'path':str(video_path)}).json()
                start=client.post(f"/api/tracking/videos/{video['id']}",json={})
                self.assertEqual(start.status_code,200,start.text)
                job=wait_job(client.app.state.tracking,start.json()['id'])
                self.assertEqual(job['status'],'completed',job['error'])
                data=client.get(f"/api/tracking/jobs/{job['id']}/observations?frame_index=1").json()
                self.assertEqual(data['frame_index'],1);self.assertEqual(data['observations'][0]['position_status'],'predicted')
                self.assertFalse(data['detector_ran']);self.assertIsNone(data['zone_observed_count'])
                precise=client.get(f"/api/tracking/jobs/{job['id']}/observations?media_ms=200").json()
                self.assertEqual(precise['frame_index'],2)
                self.assertTrue(precise['detector_ran']);self.assertEqual(precise['zone_observed_count'],0)
                missing=client.get(f"/api/tracking/jobs/{job['id']}/observations?media_ms=250").json()
                self.assertIsNone(missing['frame_index']);self.assertEqual(missing['observations'],[])
                tracks=client.get(f"/api/tracking/jobs/{job['id']}/tracks").json()
                self.assertEqual(len(tracks),1)
                self.assertEqual(len(client.get(f"/api/tracking/jobs/{job['id']}/tracks/{tracks[0]['local_track_id']}?limit=2").json()),2)
                replay=client.post(f"/api/tracking/jobs/{job['id']}/replay").json();self.assertEqual(replay['events_added'],0)
                output=client.get(f"/api/tracking/jobs/{job['id']}/output");self.assertEqual(output.status_code,200)
    def test_missing_model_weights_are_an_explicit_error_without_fabricated_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=make_video(root/'v.mp4',times=[0,100],width=100,height=100)
            with TestClient(create_app(root/'workspace',model_dir=root/'missing-model')) as client:
                video=client.post('/api/videos/import',json={'path':str(path)}).json()
                response=client.post(f"/api/tracking/videos/{video['id']}",json={})
                self.assertEqual(response.status_code,404);self.assertIn('setup',response.text)
                self.assertEqual(client.get('/api/analyses').json(),[])
