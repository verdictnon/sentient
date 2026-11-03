"""Motion-selected clips cannot silently become continuous store measurements."""
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient

from app.p1.catalog import Catalog
from app.p1.demo import make_video
from app.p1.media import register
from app.p1.geometry import load_geometry, save_geometry
from app.p1.server import create_app
from app.p1.source_profile import source_profile, interpret_summary
from app.p2.config import TrackingConfig
from app.p2.jobs import JobManager
from helpers import FixtureFactory, wait_job


class SourceSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.catalog=Catalog(self.folder/'workspace')
        self.path=make_video(self.folder/'sparse.mp4',times=[round(i*1000/6) for i in range(32)],width=160,height=100)
        self.video=register(self.catalog,self.path,store_id='fixture',camera_id='camera',stream_session_id='sparse',
            collection_session='retailaction/train/fixture')

    def test_all_sparse_frames_are_detected_and_physical_metrics_are_unavailable(self):
        manager=JobManager(self.catalog,FixtureFactory())
        try:
            result=wait_job(manager,manager.start(self.video['id'])['id'])
            self.assertEqual(result['status'],'completed',result.get('error'))
            self.assertEqual(result['config']['source_kind'],'sparse_clip')
            self.assertEqual(result['inference_frames'],32)
            self.assertEqual(result['observed_records'],32)
            self.assertEqual(result['predicted_records'],0)
            summary=result['summary'];self.assertFalse(summary['physical_analytics_supported'])
            self.assertIsNone(summary['inward_crossings']);self.assertIsNone(summary['complete_observed_dwell_ms'])
            self.assertIsInstance(summary['raw_geometry_diagnostics']['inward_crossings'],int)
            replay=manager.replay(result['id'])
            self.assertIsNone(replay['inward_crossings']);self.assertEqual(replay['records_inserted'],0)
        finally:manager.shutdown()

    def test_known_sparse_source_rejects_continuous_tracking_and_action_requests(self):
        app=create_app(self.catalog.root,detector_factory=FixtureFactory())
        with TestClient(app) as client:
            for path,body in [('/api/tracking/videos/',{'config':{'source_kind':'continuous'}}),
                ('/api/action-analysis/videos/',{'source_kind':'continuous'})]:
                response=client.post(path+self.video['id'],json=body)
                self.assertEqual(response.status_code,422,response.text)
                self.assertIn('motion-sampled',response.text)

    def test_profile_explains_sparse_timing_and_does_not_call_resolution_accuracy(self):
        profile=self.video['source_profile']
        self.assertEqual(profile['source_kind'],'sparse_clip');self.assertFalse(profile['physical_time_known'])
        self.assertFalse(profile['physical_analytics_supported']);self.assertFalse(profile['source_is_hd'])
        self.assertEqual(profile['recommended_detector_sampling'],'every_source_frame')
        carr=source_profile(self.video|{'collection_session':'carr/research/V0001','frame_dims_px':[1280,720]})
        self.assertTrue(carr['source_is_hd']);self.assertIn('publisher',carr['timing_basis'])
        self.assertIn('no automatic take/put', ' '.join(carr['warnings']))

    def test_unknown_physical_metrics_remain_null_when_read_repeatedly(self):
        raw={'inward_crossings':3,'complete_observed_dwell_ms':1000,'confirmed_physical_actions':0}
        first=interpret_summary(raw,'sparse_clip');again=interpret_summary(first,'sparse_clip')
        self.assertEqual(first,again);self.assertEqual(first['raw_geometry_diagnostics']['inward_crossings'],3)
        self.assertEqual(raw['inward_crossings'],3);self.assertIsNone(first['inward_crossings'])
        self.assertEqual(interpret_summary(raw,'continuous')['inward_crossings'],3)

    def test_composed_replay_uses_tracking_configuration_without_inventing_queue_service(self):
        video=register(self.catalog,self.path,store_id='fixture',camera_id='continuous',stream_session_id='continuous')
        geometry=load_geometry(self.catalog,video['id'])['geometry']
        geometry['shapes'] += [
            {'id':'q','name':'Queue','kind':'queue','points':[[20,0],[60,0],[60,100],[20,100]]},
            {'id':'c','name':'Checkout','kind':'checkout','points':[[60,0],[150,0],[150,100],[60,100]]}]
        geometry['active'].update(queue='q',checkout='c')
        saved=save_geometry(self.catalog,video['id'],geometry)
        app=create_app(self.catalog.root,detector_factory=FixtureFactory())
        with TestClient(app) as client:
            started=client.post('/api/tracking/videos/'+video['id'],json={'geometry_id':saved['id']}).json()
            job=wait_job(app.state.tracking,started['id'])
            self.assertEqual(job['status'],'completed',job.get('error'))
            self.assertNotIn('queue',job['p0_config'])
            data={'video_id':video['id'],'geometry_id':saved['id'],'tracking_job_id':job['id'],'isolated_run':'true'}
            response=client.post('/api/replays',data=data,files={'file':('tracks.jsonl',Path(job['output_path']).read_bytes())})
            self.assertEqual(response.status_code,200,response.text)
            combined=response.json();self.assertNotIn('queue',combined['config'])
            for key in ['inward_crossings','outward_crossings','queue_completed_visits','queue_completed_waiting_ms']:
                self.assertEqual(combined['summary'][key],job['summary'][key])
            data['geometry_id']='another-geometry'
            invalid=client.post('/api/replays',data=data,files={'file':('tracks.jsonl',Path(job['output_path']).read_bytes())})
            self.assertEqual(invalid.status_code,422)

    def test_larger_official_detector_configuration_is_explicit(self):
        self.assertEqual(TrackingConfig(detector='rtdetrv2_r34').detector,'rtdetrv2_r34')


if __name__=='__main__':unittest.main()
