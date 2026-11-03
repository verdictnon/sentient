"""Launched browser workflows: explicit engineering boxes and real model execution."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from playwright.sync_api import sync_playwright,expect

from app.contracts import ContractValidator
from app.p1.demo import make_video

ROOT=Path(__file__).resolve().parents[2]


@contextmanager
def server(folder,fixture):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    if fixture:
        factory='from helpers import FixtureFactory;app=create_app(root,detector_factory=FixtureFactory(delay=.01))'
    else:
        factory='app=create_app(root)'
    # Graceful shutdown matters on Windows: forcibly terminating a venv launcher
    # can leave the actual GPU worker process holding its inherited log handle.
    launcher=('import sys,threading;sys.path.insert(0,"tests/p2");'
              'from app.p1.server import create_app;from pathlib import Path;import uvicorn;'
              f'root=Path({str(folder / "workspace")!r});'+factory+';'
              f'instance=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port={port}));'
              'threading.Thread(target=lambda:(sys.stdin.readline(),setattr(instance,"should_exit",True)),daemon=True).start();'
              'instance.run()')
    command=[sys.executable,'-c',launcher]
    with (folder/'server.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.PIPE,stdout=log,stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+25
            while True:
                try:
                    with urllib.request.urlopen(url+'/api/status',timeout=1):pass
                    break
                except OSError:
                    if time.monotonic()>deadline or process.poll() is not None:
                        raise AssertionError('Server failed to launch: '+(folder/'server.log').read_text())
                    time.sleep(.1)
            yield url
        except BaseException:
            print((folder/'server.log').read_text(encoding='utf-8',errors='replace'))
            raise
        finally:
            process.communicate(input=b'\n',timeout=30)


def import_video(page,url,path):
    page.goto(url,wait_until='networkidle')
    page.locator('#import-button').click();page.locator('#video-file').set_input_files(str(path))
    page.locator('#import-submit').click()
    expect(page.locator('#notice')).to_contain_text('Recording indexed',timeout=30000)


class TrackingBrowserTests(unittest.TestCase):
    def test_engineering_fixture_overlay_selection_and_source_frame_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);path=make_video(folder/'engineering-fixture.mp4',times=list(range(0,4100,100)),width=320,height=180)
            with server(folder,True) as url,sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1536,'height':1100})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                import_video(page,url,path)
                page.locator('#track-people').click()
                expect(page.locator('#tracking-status')).to_have_text('Tracking complete',timeout=45000)
                expect(page.locator('#tracking-detail')).to_contain_text('ENGINEERING TEST ADAPTER')
                expect(page.locator('#metric-crossings')).to_contain_text('1 inward')
                page.locator('#seek').evaluate("e=>{e.value=.2;e.dispatchEvent(new Event('change',{bubbles:true}))}")
                expect(page.locator('#people-overlay')).to_have_attribute('data-frame-index','2')
                observed=page.locator('#people-overlay [data-person]')
                expect(observed).to_have_attribute('data-position-status','observed')
                box=page.locator('#people-overlay rect').evaluate('e=>({x:Number(e.getAttribute("x")),width:Number(e.getAttribute("width"))})')
                self.assertAlmostEqual(box['x'],12.8);self.assertAlmostEqual(box['width'],64)
                page.locator('#next-frame').click()
                expect(page.locator('#people-overlay')).to_have_attribute('data-frame-index','3')
                expect(page.locator('#people-overlay [data-person]')).to_have_attribute('data-position-status','predicted')
                expect(page.locator('#people-overlay rect')).to_have_attribute('stroke-dasharray','8 5')
                page.locator('#play').click()
                # Capture a complete immutable snapshot in the same browser task
                # that observes readiness; a later frame may clear live attrs.
                alignment=page.wait_for_function("""()=>{
                    const e=document.getElementById('people-overlay');
                    const s=JSON.parse(e.dataset.snapshot??'{}');
                    return s.valid&&Number.isFinite(s.presented_media_ms)&&Number.isFinite(s.media_ms)
                        ? {presented:s.presented_media_ms,indexed:s.media_ms,generation:s.generation}:false;
                }""").json_value()
                self.assertLess(abs(alignment['presented']-alignment['indexed']),.01)
                page.locator('#play').click()
                page.locator('#inspector-tracks').click()
                page.locator('[data-inspect-track]').first.click()
                expect(page.locator('#track-inspection')).to_be_visible()
                page.locator('.track-observation').first.click()
                expect(page.locator('#people-overlay')).to_have_attribute('data-frame-index','0')
                page.locator('#seek').evaluate("e=>{e.value=2.4;e.dispatchEvent(new Event('change',{bubbles:true}))}")
                expect(page.locator('#people-overlay')).to_have_attribute('data-frame-index','24')
                artifacts=ROOT/'docs/app/demos';artifacts.mkdir(exist_ok=True)
                page.screenshot(path=str(artifacts/'p2-engineering-overlay.png'),full_page=True)
                page.locator('#track-people').click()
                expect(page.locator('#notice')).to_contain_text('existing completed run')
                self.assertEqual(errors,[])
                browser.close()

    def test_late_overlay_response_cannot_replace_a_newer_source_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);path=make_video(folder/'fixture.mp4',times=list(range(0,2100,100)),width=320,height=180)
            with server(folder,True) as url,sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True);page=browser.new_page()
                import_video(page,url,path);page.locator('#track-people').click()
                expect(page.locator('#tracking-status')).to_have_text('Tracking complete',timeout=45000)
                pending=[]
                page.route('**/observations?frame_index=1',lambda route:pending.append((route,route.fetch())))
                page.locator('#next-frame').click()
                deadline=time.monotonic()+5
                while not pending and time.monotonic()<deadline:page.wait_for_timeout(10)
                self.assertEqual(len(pending),1)
                expect(page.locator('#people-overlay rect')).to_have_count(0)
                page.locator('#next-frame').click()
                expect(page.locator('#people-overlay')).to_have_attribute('data-frame-index','2')
                route,response=pending[0];route.fulfill(response=response)
                page.wait_for_timeout(100)
                snapshot=page.locator('#people-overlay').evaluate("e=>JSON.parse(e.dataset.snapshot)")
                self.assertTrue(snapshot['valid']);self.assertEqual(snapshot['frame_index'],2)
                self.assertEqual(snapshot['media_ms'],200)
                expect(page.locator('#people-overlay')).to_have_attribute('data-frame-index','2')
                page.unroute_all(behavior='ignoreErrors');browser.close()

    def test_real_original_detector_job_through_track_people_button(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);path=make_video(folder/'synthetic-empty-scene.mp4',times=[0,100,350,700],width=320,height=180)
            with server(folder,False) as url,sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1536,'height':1050})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                import_video(page,url,path);page.locator('#track-people').click()
                expect(page.locator('#tracking-status')).to_have_text('Tracking complete',timeout=90000)
                video=page.request.get(url+'/api/videos').json()[0]
                job=page.request.get(url+f"/api/tracking/videos/{video['id']}").json()[0]
                self.assertEqual(job['status'],'completed',job.get('error'))
                self.assertFalse(job['detector'].get('engineering_test',False))
                self.assertIn('original RT-DETRv2',job['detector']['implementation'])
                output=page.request.get(url+f"/api/tracking/jobs/{job['id']}/output").text()
                records=[json.loads(line) for line in output.splitlines()]
                for record in records:ContractValidator().validate(record)
                replay=page.request.post(url+f"/api/tracking/jobs/{job['id']}/replay").json()
                self.assertEqual(replay['events_added'],0);self.assertEqual(replay['records_inserted'],0)
                self.assertEqual(errors,[])
                artifacts=ROOT/'docs/app/demos';artifacts.mkdir(exist_ok=True)
                report={k:job[k] for k in ('status','frames_processed','inference_frames','observed_records','predicted_records','detector','metrics','summary')}
                report['validation_scope']='Actual pretrained inference on a synthetic scene; NOT continuous real-person accuracy validation'
                (artifacts/'p2-pretrained-browser-run.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
                page.screenshot(path=str(artifacts/'p2-pretrained-empty-scene.png'),full_page=True)
                browser.close()
