"""Launch the real app and verify playback, visual editing and review in Chromium."""

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

from playwright.sync_api import sync_playwright, expect

from app.contracts import ContractValidator
from app.p1.demo import make_demo, make_video

ROOT=Path(__file__).resolve().parents[2]


def browser_workflow(url,video_path,records_path,output_dir):
    output_dir.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1536,'height':1050},device_scale_factor=1)
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url,wait_until='networkidle')
        page.locator('#import-button').click()
        page.locator('#video-file').set_input_files(str(video_path))
        page.locator('#collection-session').fill('synthetic-engineering-demo')
        page.locator('#import-submit').click()
        expect(page.locator('#notice')).to_contain_text('Recording indexed',timeout=30000)
        expect(page.locator('#vfr-badge')).to_contain_text('VFR')
        expect(page.locator('#capture-label')).to_have_text('Unknown')
        page.wait_for_function("document.getElementById('player').readyState >= 2")
        page.locator('#play').click()
        page.wait_for_function("document.getElementById('player').currentTime > 0.2")
        page.locator('#play').click()
        page.locator('#seek').evaluate("e=>{e.value=2;e.dispatchEvent(new Event('change',{bubbles:true}))}")
        expect(page.locator('#frame-label')).to_contain_text('FRAME 007')
        page.locator('#next-frame').click()
        expect(page.locator('#frame-label')).to_contain_text('FRAME 008')
        expect(page.locator('#pts-label')).to_contain_text('2400.000 ms')
        page.locator('#previous-frame').click()
        expect(page.locator('#frame-label')).to_contain_text('FRAME 007')
        page.wait_for_function("document.getElementById('frame-image').naturalWidth === 960")

        def draw(kind,points):
            page.locator(f'[data-tool="{kind}"]').click()
            box=page.locator('#overlay').bounding_box()
            for x,y in points:
                page.mouse.click(box['x']+box['width']*x,box['y']+box['height']*y)
            if kind!='line':page.locator('#finish-shape').click()

        page.locator('#inspector-geometry').click()
        draw('line',[(.4,.9),(.4,.1)])
        draw('zone',[(.1,.25),(.3,.25),(.3,.55),(.1,.55)])
        page.locator('#shape-name').fill('Review shelf')
        # Drag an actual rendered SVG vertex in original image coordinates.
        box=page.locator('#overlay').bounding_box()
        vertex=page.locator('#overlay circle[data-vertex="0"]').bounding_box()
        page.mouse.move(vertex['x']+vertex['width']/2,vertex['y']+vertex['height']/2)
        page.mouse.down()
        page.mouse.move(box['x']+box['width']*.12,box['y']+box['height']*.27,steps=5)
        page.mouse.up()
        draw('queue',[(.2,.65),(.45,.65),(.45,.95),(.2,.95)])
        draw('checkout',[(.5,.65),(.8,.65),(.8,.95),(.5,.95)])
        page.locator('#save-geometry').click()
        expect(page.locator('#notice')).to_contain_text('Geometry saved as revision 2')
        page.locator('#reload-geometry').click()
        expect(page.locator('#geometry-revision')).to_contain_text('revision 2')
        expect(page.locator('#shape-count')).to_have_text('6')
        expect(page.locator('#shape-list')).to_contain_text('Review shelf')
        video_id=page.request.get(url+'/api/videos').json()[0]['id']
        saved=page.request.get(url+f'/api/videos/{video_id}/geometry').json()
        shelf=next(s for s in saved['geometry']['shapes'] if s['name']=='Review shelf')
        assert abs(shelf['points'][0][0]-115.2)<2, shelf
        assert abs(shelf['points'][0][1]-145.8)<2, shelf
        with page.expect_download() as downloaded_geometry:
            page.locator('#geometry-export').click()
        geometry_path=output_dir/'p1-saved-geometry.json'
        downloaded_geometry.value.save_as(str(geometry_path))
        assert json.loads(geometry_path.read_text())==saved['geometry']
        page.locator('#geometry-file').set_input_files(str(geometry_path))
        expect(page.locator('#notice')).to_contain_text('Geometry JSON loaded as revision 2')
        page.screenshot(path=str(output_dir/'p1-geometry.png'),full_page=True)

        page.locator('#records-file').set_input_files(str(records_path))
        expect(page.locator('#notice')).to_contain_text('9 records added',timeout=30000)
        expect(page.locator('#health-label')).to_contain_text('ended')
        page.locator('#records-file').set_input_files(str(records_path))
        expect(page.locator('#notice')).to_contain_text('0 records added · 9 exact retries · 0 events added',timeout=30000)
        page.locator('#events-body tr').filter(has_text='action.scored / unknown').click()
        expect(page.locator('#review-form')).to_be_visible()
        page.locator('#evidence-before').click()
        expect(page.locator('#frame-label')).to_contain_text('FRAME 000')
        page.locator('#evidence-during').click()
        expect(page.locator('#frame-label')).to_contain_text('FRAME 007')
        page.locator('#evidence-after').click()
        expect(page.locator('#frame-label')).to_contain_text('FRAME 011')
        page.locator('#reviewer').fill('Engineering demo')
        page.locator('#decision').select_option('corrected')
        page.locator('#review-label').select_option('background')
        page.locator('#uncertainty').select_option('high')
        page.locator('#review-notes').fill('Synthetic timing demo only. Original mock scores remain unchanged.')
        page.get_by_role('button',name='Save revision',exact=True).click()
        expect(page.locator('#notice')).to_contain_text('saved as revision 1')
        page.locator('#decision').select_option('accepted')
        page.locator('#review-notes').fill('Second human revision. No accuracy claim from this synthetic demo.')
        page.get_by_role('button',name='Save revision',exact=True).click()
        expect(page.locator('#notice')).to_contain_text('saved as revision 2')
        expect(page.locator('#revision-history .revision')).to_have_count(2)
        with page.expect_download() as downloaded:
            page.locator('#export').click()
        export_path=output_dir/'p1-review-export.jsonl'
        downloaded.value.save_as(str(export_path))
        annotations=[json.loads(line) for line in export_path.read_text().splitlines()]
        assert [a['revision'] for a in annotations]==[1,2]
        for annotation in annotations:
            assert annotation['capture_time_utc'] is None
            assert annotation['human']['label']=='background'
            assert annotation['model_outputs'][0]['action']=='unknown'
            for record in annotation['source_records']:ContractValidator().validate(record)
        page.locator('#evidence-during').click()
        expect(page.locator('#frame-label')).to_contain_text('FRAME 007')
        page.screenshot(path=str(output_dir/'p1-review.png'),full_page=True)
        assert not errors, errors
        proof={'browser':'Chromium','launched_url':url,'playback_advanced':True,
               'vfr_frame_navigation_verified':True,'capture_utc':None,
               'geometry_shapes':6,'geometry_revision':2,'vertex_drag_verified':True,
               'event_seek_before_during_after_verified':True,'replay_retry_events_added':0,
               'annotation_revisions':[a['revision'] for a in annotations],
               'source_records_validated_against_original_contract':True,'page_errors':errors}
        (output_dir/'p1-browser-verification.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
        context.close();browser.close()
        return proof


class P1BrowserAcceptanceTests(unittest.TestCase):
    def test_nonzero_pts_seeking_in_the_launched_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            video=make_video(folder/'offset.mp4',times=[0,100,350,700],width=160,height=90,start_ms=5000)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            url=f'http://127.0.0.1:{port}'
            with (folder/'server.log').open('w',encoding='utf-8') as log:
                process=subprocess.Popen([sys.executable,'-m','app.p1.server','--port',str(port),
                                          '--data-dir',str(folder/'workspace')],cwd=ROOT,stdout=log,stderr=log,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                try:
                    deadline=time.monotonic()+20
                    while True:
                        try:
                            with urllib.request.urlopen(url+'/api/status',timeout=1):pass
                            break
                        except OSError:
                            if time.monotonic()>deadline:self.fail('Server did not start')
                            time.sleep(.1)
                    with sync_playwright() as pw:
                        browser=pw.chromium.launch(headless=True)
                        page=browser.new_page()
                        page.goto(url)
                        page.locator('#import-button').click()
                        page.locator('#video-file').set_input_files(str(video))
                        page.locator('#import-submit').click()
                        expect(page.locator('#notice')).to_contain_text('Recording indexed',timeout=30000)
                        expect(page.locator('#pts-label')).to_contain_text('5000.000 ms')
                        page.wait_for_function("document.getElementById('player').readyState>=2")
                        page.locator('#next-frame').click()
                        expect(page.locator('#pts-label')).to_contain_text('5100.000 ms')
                        page.wait_for_function("Math.abs(document.getElementById('player').currentTime-5.1)<0.01")
                        expect(page.locator('#time-label')).to_contain_text('0.100 / 0.700 s')
                        page.locator('#play').click()
                        page.wait_for_function("document.getElementById('player').currentTime>5.2")
                        expect(page.locator('#capture-label')).to_have_text('Unknown')
                        browser.close()
                finally:
                    process.terminate();process.wait(timeout=10)

    def test_launched_application_video_geometry_review_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            video,records=make_demo(folder/'demo')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            url=f'http://127.0.0.1:{port}'
            with (folder/'server.log').open('w',encoding='utf-8') as log:
                process=subprocess.Popen([sys.executable,'-m','app.p1.server','--port',str(port),
                                          '--data-dir',str(folder/'workspace')],cwd=ROOT,stdout=log,stderr=log,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                try:
                    deadline=time.monotonic()+20
                    while True:
                        try:
                            with urllib.request.urlopen(url+'/api/status',timeout=1) as response:
                                self.assertEqual(response.status,200)
                            break
                        except OSError:
                            if time.monotonic()>deadline or process.poll() is not None:
                                self.fail('Application did not launch: '+(folder/'server.log').read_text())
                            time.sleep(.1)
                    proof=browser_workflow(url,video,records,ROOT/'docs/app/demos')
                    self.assertTrue(proof['playback_advanced'])
                finally:
                    process.terminate()
                    process.wait(timeout=10)
