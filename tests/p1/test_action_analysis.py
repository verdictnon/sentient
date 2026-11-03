"""Combined jobs must fail/cancel honestly and remain retryable across restarts."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app.action_analysis import ActionAnalysisManager
from app.p1.catalog import Catalog
from app.p1.demo import make_video
from app.p1.media import register
from app.storage import canonical


class TrackingFixture:
    def __init__(self):self.mode='error';self.cancelled=False
    def start(self,*args):
        if self.mode=='error':raise RuntimeError('Deliberate detector failure')
        return {'id':'tracking-fixture','status':'running','progress':0.1}
    def get(self,*args):return {'id':'tracking-fixture','status':'cancelled' if self.cancelled else 'running','progress':0.1}
    def cancel(self,*args):self.cancelled=True


class ActionAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name);root=folder/'worker'
        for name in ['.venv/bin/python','runs/action_worker_v1/model/model.pt','scripts/worker/pipeline.py']:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
        (root/'runs/action_worker_v1/model/manifest.json').write_text(json.dumps({'model_id':'fixture','model_sha256':'a'*64}))
        self.patch=patch('app.action_analysis.worker_root',return_value=root);self.patch.start();self.addCleanup(self.patch.stop)
        self.catalog=Catalog(folder/'workspace');self.tracking=TrackingFixture()
        self.video=register(self.catalog,make_video(folder/'fixture.mp4',times=[0,100,200],width=100,height=100),
            store_id='test',camera_id='camera',stream_session_id='session')
        self.manager=ActionAnalysisManager(self.catalog,self.tracking);self.addCleanup(self.manager.shutdown)

    def terminal(self,identity):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            job=self.manager.get(identity)
            if job['status'] in ('error','cancelled','completed'):return job
            time.sleep(.01)
        self.fail('Combined job failed to reach a terminal state')

    def test_tracking_failure_does_not_publish_success_and_retry_keeps_diagnostics(self):
        failed=self.terminal(self.manager.start(self.video['id'])['id'])
        self.assertEqual(failed['status'],'error');self.assertIsNone(failed['analysis_id'])
        self.assertIn('Deliberate detector failure',failed['error'])
        retry=self.terminal(self.manager.start(self.video['id'])['id'])
        self.assertEqual(retry['id'],failed['id']);self.assertEqual(retry['attempt'],2)
        self.assertNotEqual(retry['folder'],failed['folder']);self.assertTrue(Path(failed['folder']).is_dir())
        with self.catalog.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0],0)

    def test_cancel_waiting_tracking_reaches_terminal_state_without_scoring(self):
        self.tracking.mode='waiting';job=self.manager.start(self.video['id'])
        deadline=time.monotonic()+5
        while not self.manager.get(job['id']).get('tracking_job_id'):
            if time.monotonic()>deadline:self.fail('Tracking did not start')
            time.sleep(.01)
        self.manager.cancel(job['id']);done=self.terminal(job['id'])
        self.assertEqual(done['status'],'cancelled');self.assertTrue(self.tracking.cancelled)
        self.assertIsNone(done['analysis_id']);self.assertFalse((Path(done['folder'])/'worker.log').exists())
        self.assertEqual(self.manager.cancel(job['id'])['status'],'cancelled')

    def test_restart_marks_interrupted_job_retryable_instead_of_complete(self):
        failed=self.terminal(self.manager.start(self.video['id'])['id'])
        self.manager.update(failed['id'],status='running',stage='scoring_actions')
        restarted=ActionAnalysisManager(self.catalog,self.tracking)
        try:
            job=restarted.get(failed['id']);self.assertEqual(job['status'],'error')
            self.assertEqual(job['stage'],'interrupted');self.assertIsNone(job['analysis_id'])
            retry=restarted.start(self.video['id']);self.assertEqual(retry['attempt'],2)
        finally:restarted.shutdown()
