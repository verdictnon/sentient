"""Browser regressions for optional scene guides and asynchronous clip/frame loads."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from playwright.sync_api import sync_playwright, expect
from app.p1.demo import make_video

ROOT = Path(__file__).resolve().parents[2]


class PlaybackRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cls.url = f'http://127.0.0.1:{port}'
        cls.log = (folder/'server.log').open('w')
        cls.process = subprocess.Popen([sys.executable, '-m', 'app.p1.server', '--port', str(port),
            '--data-dir', str(folder/'workspace')], cwd=ROOT, stdout=cls.log, stderr=cls.log)
        cls.addClassCleanup(cls.cleanup_server)
        deadline = time.monotonic()+20
        while True:
            try:
                urllib.request.urlopen(cls.url+'/api/status', timeout=1).close()
                break
            except OSError:
                if time.monotonic()>deadline:
                    raise RuntimeError('App failed to start')
                time.sleep(.1)
        cls.videos = []
        for i in range(2):
            path = make_video(folder/f'clip-{i}.mp4', times=list(range(0, 6001, 100)), width=320, height=180)
            request = urllib.request.Request(cls.url+'/api/videos/import',
                data=json.dumps({'path':str(path), 'store_id':'test', 'camera_id':f'camera-{i}',
                                 'stream_session_id':f'session-{i}'}).encode(),
                headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request) as response:
                cls.videos.append(json.load(response))

    @classmethod
    def cleanup_server(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.log.close()
        cls.temp.cleanup()

    def setUp(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.page = self.browser.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error:self.errors.append(str(error)))
        self.page.goto(self.url, wait_until='networkidle')
        self.select(1)

    def tearDown(self):
        self.page.unroute_all(behavior='ignoreErrors')
        self.browser.close()
        self.pw.stop()
        self.assertEqual(self.errors, [])

    def select(self, index):
        video = self.videos[index]
        self.page.locator(f'[data-video="{video["id"]}"]').click()
        expect(self.page.locator('#notice')).to_contain_text('Recording indexed')
        self.page.wait_for_load_state('networkidle')
        return video

    def hold(self, url):
        pending = []
        self.page.route(url, lambda route:pending.append((route, route.fetch())))
        return pending

    def wait_pending(self, pending):
        deadline = time.monotonic()+5
        while not pending and time.monotonic()<deadline:
            self.page.wait_for_timeout(10)
        self.assertEqual(len(pending), 1)

    def release(self, pending):
        self.wait_pending(pending)
        route, response = pending[0]
        with self.page.expect_response(response.url):
            route.fulfill(response=response)
        # Let the delivered response's asynchronous UI continuation run.
        self.page.wait_for_timeout(200)

    def test_scene_guides_are_optional_and_explained(self):
        p = self.page
        expect(p.locator('#playback-panel')).to_be_visible()
        expect(p.locator('#overlay')).to_be_hidden()
        expect(p.locator('#playback-panel')).to_contain_text('Analyze video runs person tracking')
        p.locator('#zones-toggle').click()
        expect(p.locator('#overlay')).to_be_visible()
        expect(p.locator('#stage-label')).to_contain_text('FIXED SCENE GUIDES')
        self.assertEqual(p.locator('#overlay').evaluate('e=>getComputedStyle(e).pointerEvents'), 'none')
        p.locator('#inspector-geometry').click()
        self.assertEqual(p.locator('#overlay').evaluate('e=>getComputedStyle(e).pointerEvents'), 'auto')
        expect(p.locator('#overlay circle')).to_have_count(2)
        p.locator('#zones-toggle').click()
        expect(p.locator('#overlay')).to_be_hidden()
        expect(p.locator('#playback-panel')).to_be_visible()
        self.select(0)
        expect(p.locator('#overlay')).to_be_hidden()

    def test_late_frame_response_cannot_freeze_playback(self):
        p = self.page
        video = self.videos[1]
        pending = self.hold(f'**/api/videos/{video["id"]}/frames?offset=1&limit=1')
        p.locator('#next-frame').click()
        self.wait_pending(pending)
        p.locator('#play').click()
        p.wait_for_function("document.getElementById('player').currentTime>0.25")
        self.release(pending)
        self.assertFalse(p.locator('#player').evaluate('e=>e.paused'))
        expect(p.locator('#frame-image')).to_be_hidden()
        start = p.locator('#player').evaluate('e=>e.currentTime')
        p.wait_for_function("t=>document.getElementById('player').currentTime>t+.2", arg=start)

    def test_latest_clip_wins_over_late_metadata(self):
        p = self.page
        old, latest = self.videos
        pending = self.hold(f'**/api/videos/{old["id"]}')
        p.locator(f'[data-video="{old["id"]}"]').click()
        self.wait_pending(pending)
        p.locator(f'[data-video="{latest["id"]}"]').click()
        expect(p.locator('#notice')).to_contain_text('Recording indexed')
        self.release(pending)
        expect(p.locator('#video-name')).to_have_text(latest['display_name'])
        expect(p.locator('#player')).to_have_attribute('src', f'/api/videos/{latest["id"]}/content')
        expect(p.locator('#frame-image')).to_have_attribute('src', f'/api/videos/{latest["id"]}/frame/0')
        expect(p.locator('#playback-panel')).to_be_visible()

    def test_late_geometry_cannot_replace_selected_clip(self):
        p = self.page
        old, latest = self.videos
        pending = self.hold(f'**/api/videos/{old["id"]}/geometry')
        p.locator(f'[data-video="{old["id"]}"]').click()
        self.wait_pending(pending)
        p.locator(f'[data-video="{latest["id"]}"]').click()
        expect(p.locator('#notice')).to_contain_text('Recording indexed')
        p.locator('#inspector-review').click()
        self.release(pending)
        expect(p.locator('#video-name')).to_have_text(latest['display_name'])
        expect(p.locator('#review-panel')).to_be_visible()
        expect(p.locator('#overlay')).to_be_hidden()
        expect(p.locator('#play')).to_be_enabled()
