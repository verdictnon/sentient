"""Real MP4/SQLite/HTTP integration, including variable and nonzero source PTS."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import av
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from PIL import Image

from app.contracts import ContractValidator
from app.p1.demo import TIMES, make_demo, make_video, fixture_geometry
from app.p1.server import create_app
from app.storage import SQLiteStore

ROOT=Path(__file__).resolve().parents[2]


class P1IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_dir=tempfile.TemporaryDirectory()
        cls.video_path,cls.records_path=make_demo(Path(cls.media_dir.name))

    @classmethod
    def tearDownClass(cls):
        cls.media_dir.cleanup()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.app=create_app(self.root/'workspace')
        self.client=TestClient(self.app)
        self.addCleanup(self.client.close)

    def imported(self,**extra):
        response=self.client.post('/api/videos/import',json={'path':str(self.video_path),**extra})
        self.assertEqual(response.status_code,200,response.text)
        video=response.json()
        self.client.put(f"/api/videos/{video['id']}/geometry",json=fixture_geometry(video['frame_dims_px']))
        return video

    def geometry(self,video):
        return self.client.get(f"/api/videos/{video['id']}/geometry").json()

    def analyzed(self,video=None,geometry=None,path=None):
        with (path or self.records_path).open('rb') as source:
            data={'video_id':video['id']} if video else {}
            if geometry:data['geometry_id']=geometry['id']
            response=self.client.post('/api/replays',data=data,files={'file':('records.jsonl',source)})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def target(self,analysis):
        data=self.client.get(f"/api/analyses/{analysis['id']}/events").json()
        return next(row for row in data['events'] if row['kind']=='action.scored')

    def review_body(self,analysis):
        return {'analysis_id':analysis['id'],'target_id':self.target(analysis)['target_id'],
                'request_id':'review-1','expected_revision':0,'reviewer':'Test reviewer',
                'decision':'corrected','label':'background','visibility':'clear','uncertainty':'high',
                'notes':'Synthetic test only','start_media_ms':1000,'end_media_ms':3000}

    def test_local_registration_hash_manifest_and_unknown_utc(self):
        video=self.imported(collection_session='session-day-1',collection_day='2026-11-02')
        self.assertEqual(video['media_sha256'],hashlib.sha256(self.video_path.read_bytes()).hexdigest())
        self.assertIsNone(video['capture_time_utc'])
        self.assertEqual(video['clock_status'],'playback_only')
        self.assertEqual(Path(video['path']),self.video_path)
        manifest=self.client.get(f"/api/videos/{video['id']}/manifest").json()
        self.assertEqual(manifest['media_sha256'],video['media_sha256'])
        self.assertEqual(manifest['collection_session'],'session-day-1')
        self.assertNotIn('path',manifest)

    def test_vfr_frames_preserve_exact_rational_pts_without_assumed_rate(self):
        video=self.imported()
        frames=self.client.get(f"/api/videos/{video['id']}/frames").json()
        self.assertTrue(video['variable_frame_rate'])
        self.assertEqual(len(frames),len(TIMES))
        self.assertEqual([f['media_ms'] for f in frames],TIMES)
        for frame,time in zip(frames,TIMES):
            self.assertEqual(frame['pts']*Fraction(*frame['time_base'])*1000,time)
            self.assertIsNone(frame['capture_time_utc'])

    def test_seek_uses_presentation_time_and_exact_frame_decode(self):
        video=self.imported()
        seek=self.client.get(f"/api/videos/{video['id']}/seek?media_ms=2050").json()
        self.assertEqual(seek['frame_index'],TIMES.index(2000))
        response=self.client.get(f"/api/videos/{video['id']}/frame/{seek['frame_index']}")
        self.assertEqual(response.status_code,200,response.text[:200] if response.status_code!=200 else '')
        self.assertEqual(Image.open(io.BytesIO(response.content)).size,(960,540))
        with av.open(str(self.video_path)) as container:
            actual=next(frame for frame in container.decode(video=0) if frame.pts*frame.time_base==Fraction(2))
            expected=actual.to_image()
            self.assertEqual(Image.open(io.BytesIO(response.content)).tobytes(),expected.tobytes())

    def test_nonzero_media_origin_is_preserved_separately_from_browser_time(self):
        path=make_video(self.root/'offset.mp4',times=[0,100,350,700],width=160,height=90,start_ms=5000)
        response=self.client.post('/api/videos/import',json={'path':str(path)})
        self.assertEqual(response.status_code,200,response.text)
        video=response.json()
        self.assertEqual(video['first_media_time_ms'],5000)
        frames=self.client.get(f"/api/videos/{video['id']}/frames").json()
        self.assertEqual(frames[1]['media_ms'],5100)
        self.assertEqual(frames[1]['playback_time_ms'],5100)
        self.assertEqual(frames[1]['relative_time_ms'],100)
        self.assertEqual(self.client.get(f"/api/videos/{video['id']}/frame/2").status_code,200)

    def test_constant_rate_is_measured_from_actual_pts_not_assumed_30_fps(self):
        path=make_video(self.root/'constant.mp4',times=[0,100,200,300],width=160,height=90)
        response=self.client.post('/api/videos/import',json={'path':str(path)})
        self.assertEqual(response.status_code,200,response.text)
        video=response.json()
        self.assertFalse(video['variable_frame_rate'])
        frames=self.client.get(f"/api/videos/{video['id']}/frames").json()
        self.assertEqual([f['media_ms'] for f in frames],[0,100,200,300])

    def test_import_is_deterministic_and_does_not_duplicate_frame_index(self):
        first=self.imported();second=self.imported()
        self.assertEqual(first['id'],second['id'])
        with self.app.state.catalog.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frames').fetchone()[0],len(TIMES))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM videos').fetchone()[0],1)

    def test_upload_streams_to_managed_asset_and_supports_range_requests(self):
        with self.video_path.open('rb') as stream:
            response=self.client.post('/api/videos/upload',files={'file':('recording.mp4',stream)},
                                      data={'collection_session':'upload-test'})
        self.assertEqual(response.status_code,200,response.text)
        video=response.json()
        self.assertTrue(Path(video['path']).is_relative_to(self.app.state.catalog.root/'assets'))
        response=self.client.get(f"/api/videos/{video['id']}/content",headers={'Range':'bytes=0-127'})
        self.assertEqual(response.status_code,206)
        self.assertEqual(len(response.content),128)
        self.assertEqual(response.content,self.video_path.read_bytes()[:128])
        self.assertTrue(response.headers['content-range'].startswith('bytes 0-127/'))

    def test_missing_and_corrupt_video_fail_gracefully_without_catalog_entries(self):
        response=self.client.post('/api/videos/import',json={'path':str(self.root/'missing.mp4')})
        self.assertEqual(response.status_code,404)
        bad=self.root/'corrupt.mp4';bad.write_bytes(b'not a video')
        response=self.client.post('/api/videos/import',json={'path':str(bad)})
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.client.get('/api/videos').json(),[])
        with self.app.state.catalog.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frames').fetchone()[0],0)

    def test_moved_or_changed_source_is_reported_instead_of_playing_stale_index(self):
        path=self.root/'change.mp4';path.write_bytes(self.video_path.read_bytes())
        video=self.client.post('/api/videos/import',json={'path':str(path)}).json()
        path.write_bytes(b'changed')
        self.assertEqual(self.client.get(f"/api/videos/{video['id']}/content").status_code,422)
        self.assertEqual(self.client.get('/api/videos').json()[0]['availability'],'missing_or_changed')
        path.unlink()
        self.assertEqual(self.client.get(f"/api/videos/{video['id']}/frame/0").status_code,404)

    def test_paged_frames_invalid_frames_and_nonfinite_seek(self):
        video=self.imported()
        frames=self.client.get(f"/api/videos/{video['id']}/frames?offset=3&limit=2").json()
        self.assertEqual([f['frame_index'] for f in frames],[3,4])
        self.assertEqual(self.client.get(f"/api/videos/{video['id']}/frame/99999").status_code,404)
        self.assertEqual(self.client.get(f"/api/videos/{video['id']}/seek?media_ms=nan").status_code,422)

    def test_geometry_save_load_names_identifiers_direction_and_p0_mapping(self):
        video=self.imported();base=self.geometry(video)
        geometry=deepcopy(base['geometry'])
        geometry['shapes'][0]['name']='Main entrance'
        geometry['shapes'][0]['points'].reverse()
        geometry['shapes'] += [
            {'id':'wait','name':'Queue A','kind':'queue','points':[[100,100],[300,100],[300,450],[100,450]]},
            {'id':'serve','name':'Checkout A','kind':'checkout','points':[[400,100],[700,100],[700,450],[400,450]]},
        ]
        geometry['active'].update(queue='wait',checkout='serve')
        response=self.client.put(f"/api/videos/{video['id']}/geometry",json=geometry)
        self.assertEqual(response.status_code,200,response.text);saved=response.json()
        loaded=self.geometry(video)
        self.assertEqual(loaded['geometry'],geometry)
        self.assertEqual(saved['id'],loaded['id'])
        self.assertEqual(saved['revision'],2)
        self.assertEqual(saved['p0_config']['line']['start'],geometry['shapes'][0]['points'][0])
        self.assertEqual(saved['p0_config']['queue']['service_zone'],geometry['shapes'][-1]['points'])
        self.assertEqual(self.client.put(f"/api/videos/{video['id']}/geometry",json=geometry).json()['revision'],2)
        self.assertEqual(self.client.get(f"/api/videos/{video['id']}/geometry?geometry_id={base['id']}").json()['revision'],1)
        exported=self.client.get(f"/api/videos/{video['id']}/geometry/export")
        self.assertEqual(exported.json(),geometry)
        self.assertEqual(self.client.get(f"/api/videos/{video['id']}/geometry/p0").json(),saved['p0_config'])

    def test_geometry_rejects_wrong_dimensions_bounds_degenerate_shapes_and_invalid_pairs(self):
        video=self.imported();base=self.geometry(video)['geometry']
        invalid=[]
        g=deepcopy(base);g['frame_dims_px']=[100,100];invalid.append(g)
        g=deepcopy(base);g['shapes'][1]['points'][0]=[-1,0];invalid.append(g)
        g=deepcopy(base);g['shapes'][0]['points']=[[1,1],[1,1]];invalid.append(g)
        g=deepcopy(base);g['shapes'][1]['points']=[[0,0],[300,300],[0,300],[300,0]];invalid.append(g)
        g=deepcopy(base);g['active']['checkout']='unknown';invalid.append(g)
        g=deepcopy(base);g['shapes'][1]['id']='entrance';invalid.append(g)
        g=deepcopy(base);g['shapes']=[None];invalid.append(g)
        g=deepcopy(base);g['shapes'][1]['points']=[1,2,3];invalid.append(g)
        g=deepcopy(base);g['shapes'][0]['hysteresis_px']='five';invalid.append(g)
        g=deepcopy(base);g['active']['line']=[];invalid.append(g)
        for payload in invalid:
            with self.subTest(payload=payload):
                response=self.client.put(f"/api/videos/{video['id']}/geometry",json=payload)
                self.assertEqual(response.status_code,422,response.text)

    def test_video_and_original_jsonl_work_independently(self):
        self.assertEqual(self.client.get('/api/videos').json(),[])
        analysis=self.analyzed(path=ROOT/'contracts/v1/examples.jsonl')
        self.assertIsNone(analysis['video_id'])
        self.assertEqual(analysis['summary']['inward_crossings'],1)
        video=self.imported()
        self.assertEqual(self.client.get(f"/api/videos/{video['id']}/frame/0").status_code,200)
        self.assertEqual(self.client.get(f"/api/analyses?video_id={video['id']}").json(),[])

    def test_same_video_geometry_and_tracks_produce_deterministic_idempotent_results(self):
        video=self.imported();geometry=self.geometry(video)
        first=self.analyzed(video,geometry)
        with SQLiteStore(first['path']) as store:before=store.events(),store.inputs()
        second=self.analyzed(video,geometry)
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(second['summary']['events_added'],0)
        self.assertEqual(second['summary']['records_duplicate'],9)
        self.assertEqual(second['summary']['complete_observed_dwell_seconds'],2.0)
        with SQLiteStore(second['path']) as store:self.assertEqual((store.events(),store.inputs()),before)

    def test_unmatched_dimensions_or_pts_rejected_without_fabricated_alignment(self):
        video=self.imported();geometry=self.geometry(video)
        with (ROOT/'contracts/v1/examples.jsonl').open('rb') as source:
            response=self.client.post('/api/replays',data={'video_id':video['id'],'geometry_id':geometry['id']},files={'file':('x.jsonl',source)})
        self.assertEqual(response.status_code,422)
        rows=[json.loads(line) for line in self.records_path.read_text().splitlines()]
        rows[1]['media_time_ms']=999
        response=self.client.post('/api/replays',data={'video_id':video['id'],'geometry_id':geometry['id']},files={'file':('bad.jsonl','\n'.join(json.dumps(r) for r in rows))})
        self.assertEqual(response.status_code,422)
        self.assertIn('PTS',response.text)

    def test_event_evidence_filters_sessions_and_reports_health_and_timing(self):
        video=self.imported();analysis=self.analyzed(video)
        data=self.client.get(f"/api/analyses/{analysis['id']}/events").json()
        self.assertTrue(all(row['track_key'][2]=='session-a' for row in data['events']))
        target=self.target(analysis)
        self.assertEqual((target['evidence_start_ms'],target['evidence_end_ms']),(0,4000))
        self.assertEqual(data['health'][0]['state'],'ended')
        self.assertFalse(data['capture_utc_known'])

    def test_review_revisions_export_provenance_and_original_model_independence(self):
        video=self.imported(collection_session='capture-set-1');analysis=self.analyzed(video)
        with SQLiteStore(analysis['path']) as store:before=store.events(),store.inputs()
        body=self.review_body(analysis)
        first=self.client.post(f"/api/videos/{video['id']}/reviews",json=body)
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['revision'],1)
        self.assertEqual(self.client.post(f"/api/videos/{video['id']}/reviews",json=body).json(),first.json())
        body.update(request_id='review-2',expected_revision=1,decision='accepted',notes='Second review')
        self.assertEqual(self.client.post(f"/api/videos/{video['id']}/reviews",json=body).json()['revision'],2)
        export=self.client.get(f"/api/annotations/export?video_id={video['id']}")
        records=[json.loads(line) for line in export.text.splitlines()]
        self.assertEqual([row['revision'] for row in records],[1,2])
        schema=json.loads((ROOT/'app/p1/annotation.schema.json').read_text())
        for annotation in records:
            Draft202012Validator(schema).validate(annotation)
            for source in annotation['source_records']:ContractValidator().validate(source)
            self.assertEqual(annotation['media_sha256'],video['media_sha256'])
            self.assertEqual(annotation['subject_track_key'],['demo-store','camera-a','session-a','7'])
            self.assertEqual(annotation['human']['label'],'background')
            self.assertEqual(annotation['model_outputs'][0]['action'],'unknown')
            self.assertEqual(annotation['model_outputs'][0]['scores']['take'],.4)
            self.assertIsNone(annotation['capture_time_utc'])
            self.assertEqual(annotation['original_media_times_ms'],[1000,1400,1800,2000,2400,3000])
            self.assertEqual(annotation['collection_session'],'capture-set-1')
        self.assertEqual(len(self.client.get('/api/annotations/export?latest_only=true').text.splitlines()),1)
        with SQLiteStore(analysis['path']) as store:self.assertEqual((store.events(),store.inputs()),before)

    def test_stale_review_and_conflicting_request_are_rejected(self):
        video=self.imported();analysis=self.analyzed(video);body=self.review_body(analysis)
        self.assertEqual(self.client.post(f"/api/videos/{video['id']}/reviews",json=body).status_code,200)
        changed=body|{'notes':'conflicting retry'}
        self.assertEqual(self.client.post(f"/api/videos/{video['id']}/reviews",json=changed).status_code,409)
        stale=body|{'request_id':'different-review'}
        self.assertEqual(self.client.post(f"/api/videos/{video['id']}/reviews",json=stale).status_code,409)

    def test_invalid_review_span_unknown_target_and_empty_reviewer_are_rejected(self):
        video=self.imported();analysis=self.analyzed(video);body=self.review_body(analysis)
        for changes in ({'start_media_ms':-1},{'end_media_ms':999999},{'start_media_ms':1100,'end_media_ms':1300},
                        {'reviewer':' '},{'label':'unsupported'}):
            response=self.client.post(f"/api/videos/{video['id']}/reviews",json=body|changes)
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.client.post(f"/api/videos/{video['id']}/reviews",json=body|{'target_id':'missing'}).status_code,404)

    def test_catalog_survives_restart_with_geometry_and_reviews(self):
        video=self.imported();analysis=self.analyzed(video);body=self.review_body(analysis)
        self.client.post(f"/api/videos/{video['id']}/reviews",json=body).raise_for_status()
        with TestClient(create_app(self.app.state.catalog.root)) as restarted:
            self.assertEqual(len(restarted.get('/api/videos').json()),1)
            self.assertEqual(restarted.get(f"/api/videos/{video['id']}/geometry").json()['id'],analysis['geometry_id'])
            self.assertEqual(len(restarted.get('/api/annotations/export').text.splitlines()),1)

    def test_missing_analytics_database_does_not_get_silently_recreated(self):
        analysis=self.analyzed(path=ROOT/'contracts/v1/examples.jsonl')
        path=Path(analysis['path'])
        path.unlink()
        response=self.client.get(f"/api/analyses/{analysis['id']}/events")
        self.assertEqual(response.status_code,404)
        self.assertFalse(path.exists())
