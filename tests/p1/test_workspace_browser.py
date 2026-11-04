"""The simplified UI must actually import, persist editable geometry and play media."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from playwright.sync_api import sync_playwright,expect
from app.p1.demo import make_video
ROOT=Path(__file__).resolve().parents[2]

@contextmanager
def server(folder):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    with (folder/'server.log').open('w') as log:
        p=subprocess.Popen([sys.executable,'-m','app.p1.server','--port',str(port),'--data-dir',str(folder/'workspace')],cwd=ROOT,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+20
            while True:
                try:urllib.request.urlopen(url+'/api/status',timeout=1).close();break
                except OSError:
                    if p.poll() is not None or time.monotonic()>deadline:raise RuntimeError((folder/'server.log').read_text())
                    time.sleep(.1)
            yield url
        finally:p.terminate();p.wait(timeout=15)

def demo_workspace(folder):
    """A small simulated store, built in this process before the server starts."""
    from datetime import date
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app import simulation as sim
    from app.demo_store import seed_store
    from app.p1.server import create_app
    application=create_app(folder/'workspace')
    with TestClient(application),patch.object(sim,'CLIP_SECONDS',12):
        return seed_store(application.state.operations,days=2,today=date(2026,11,4))

class StoreViewTests(unittest.TestCase):
    def test_empty_workspace_offers_the_demo_store(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            with server(folder) as url,sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1280,'height':800});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url);expect(page.locator('#demo-build')).to_be_visible();expect(page.locator('#store-empty')).to_contain_text('simulated')
                # A first real recording gets the steps it still needs, never a row of zeros.
                video=make_video(folder/'first.mp4',times=list(range(0,1000,100)),width=320,height=180)
                self.assertTrue(page.request.post(url+'/api/videos/import',data={'path':str(video),'store_id':'corner-shop'}).ok)
                page.goto(url);page.wait_for_function('store.data?.mode==="collection"')
                expect(page.locator('#store-narrative')).to_contain_text('Run the analysis')
                self.assertEqual(page.locator('#store-actions .action h3').all_text_contents(),['Map this camera','Analyse a recording','Say when it was filmed'])
                expect(page.locator('#sim-tape')).to_be_hidden()
                page.locator('#store-actions .action button').first.click();expect(page.locator('#page-label')).to_have_text('Camera setup')
                self.assertEqual(errors,[]);browser.close()

    def test_store_day_themes_language_and_mobile(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);seeded=demo_workspace(folder)
            with server(folder) as url,sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True);context=browser.new_context(viewport={'width':1440,'height':900},locale='en-GB',color_scheme='light');page=context.new_page();page.set_default_timeout(10000)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url);page.wait_for_function('store.data?.basis==="simulated"')
                expect(page.locator('#page-label')).to_have_text('Store')
                expect(page.locator('#sim-tape')).to_be_visible()
                expect(page.locator('#store-narrative')).to_contain_text('people came in')
                expect(page.locator('#receipt')).to_contain_text('Floor Z-report')
                expect(page.locator('#receipt')).to_contain_text('Simulated',ignore_case=True)
                self.assertGreater(page.locator('#store-actions .action').count(),0)
                self.assertGreater(page.locator('.busy-cell').count(),10)
                # Layers redraw the map from the same period.
                page.wait_for_function("document.getElementById('floor-heat').getContext('2d').getImageData(0,0,1280,720).data.some(v=>v>0)")
                page.locator('[data-layer=flows]').click();self.assertGreater(page.locator('#floor-overlay .map-flow').count(),0);expect(page.locator('#floor-heat')).to_be_hidden()
                page.locator('[data-layer=zones]').click();self.assertGreater(page.locator('#floor-overlay .map-value').count(),0)
                page.locator('[data-span="7"]').click();page.wait_for_function('store.data.span_days===7');expect(page.locator('#store-narrative')).to_contain_text('came in')
                page.locator('[data-span="1"]').click();page.wait_for_function('store.data.span_days===1')
                pdf=page.request.get(url+page.locator('#z-report-pdf').get_attribute('href'));self.assertTrue(pdf.body().startswith(b'%PDF'))
                # Themes: light is white, dark is not, and the choice survives a reload.
                self.assertEqual(page.evaluate('getComputedStyle(document.body).backgroundColor'),'rgb(255, 255, 255)')
                page.locator('#theme-toggle').click();self.assertEqual(page.evaluate('document.documentElement.dataset.theme'),'dark')
                self.assertNotEqual(page.evaluate('getComputedStyle(document.body).backgroundColor'),'rgb(255, 255, 255)')
                page.reload();page.wait_for_function('store.data');self.assertEqual(page.evaluate('document.documentElement.dataset.theme'),'dark')
                page.locator('#theme-toggle').click()
                # Analytics draws the store half and the recording half.
                page.locator('[data-nav=analytics]').click();page.wait_for_function("document.querySelectorAll('#footfall-chart .column').length>5")
                self.assertGreater(page.locator('#zone-table tbody tr').count(),3);self.assertGreater(page.locator('.funnel-row').count(),1)
                # Cases show the cross-check, and the clip plays in the dialog.
                page.goto(url+'/?view=incidents&video='+seeded['video_id']);page.wait_for_function('ops.data?.incidents.length>=0')
                expect(page.locator('#page-label')).to_have_text('Investigations');expect(page.locator('#case-scope')).to_contain_text('one saved run')
                page.locator('[data-nav=overview]').click();expect(page.locator('#timeline-plot')).to_be_visible()
                page.locator('#play-toggle').click();page.wait_for_function("!document.getElementById('player').paused");page.locator('#play-toggle').click()
                # Russian replaces the static text and the dynamic text after a reload.
                page.locator('[data-nav=store]').click();page.locator('#lang-toggle').click();page.wait_for_function("document.documentElement.lang==='ru'");page.wait_for_function('store.data')
                expect(page.locator('#page-label')).to_have_text('Магазин');expect(page.locator('#store-narrative')).to_contain_text(re.compile('заш(ёл|ли|ло)'));expect(page.locator('#sim-tape')).to_contain_text('Симуляция')
                expect(page.locator('#receipt')).to_contain_text('Z-отчёт зала',ignore_case=True);expect(page.locator('[data-nav=incidents]')).to_contain_text('Проверки')
                page.locator('[data-nav=help]').click();expect(page.locator('#help-panel')).to_contain_text('Сверка подборов с кассой')
                page.set_viewport_size({'width':390,'height':844})
                for view in ('store','overview','analytics','incidents','reports'):
                    page.goto(url+'/?view='+view);page.wait_for_function('store.data');self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'),view)
                self.assertEqual(errors,[]);browser.close()

class WorkspaceBrowserTests(unittest.TestCase):
    def test_operations_track_reviews_reports_and_schedules(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);path=make_video(folder/'aisle.mp4',times=list(range(0,4200,100)),width=640,height=360)
            with server(folder) as url,sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1000});page.set_default_timeout(10000)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                video=page.request.post(url+'/api/videos/import',data={'path':str(path)}).json()
                g=page.request.get(url+f'/api/videos/{video["id"]}/geometry').json()['geometry']
                g['shapes']=[{'id':'shelf','kind':'shelf','name':'Drinks','points':[[100,100],[400,100],[400,300],[100,300]]}]
                saved=page.request.put(url+f'/api/videos/{video["id"]}/geometry',data=g).json()
                key='e'*32;folder_live=folder/'workspace/live'/key;folder_live.mkdir(parents=True)
                event={'id':'pickup:demo','local_track_id':'local:1','start_media_ms':600,'end_media_ms':3000,'detected_media_ms':3000,'score':.99}
                summary={'id':key,'video_id':video['id'],'geometry_id':saved['id'],'status':'completed','processed_frames':20,'scored_windows':1,'pickups':[event]}
                (folder_live/'summary.json').write_text(json.dumps(summary))
                rows=[{'frame_index':ms//100,'media_ms':ms,'tracks':[{'local_track_id':'local:1','box':[200,80,300,240],'position_status':'observed'}]} for ms in range(0,4000,200)]
                (folder_live/'observations.jsonl').write_text('\n'.join(map(json.dumps,rows)))
                page.goto(url+'/?video='+video['id']);page.wait_for_function('ops.data?.tracks.length===1')
                page.locator('[data-nav="tracking"]').click();page.locator('[data-focus-track]').click()
                expect(page.locator('#track-detail')).to_contain_text('Area visit ledger')
                page.wait_for_function("document.querySelector('#track-detail img').naturalWidth>0")
                with page.expect_download() as download:page.locator('#trajectory-export').click()
                self.assertIn('media_ms',Path(download.value.path()).read_text())
                page.locator('[data-nav="incidents"]').click();page.locator('[data-signal]').click()
                page.locator('#incident-decision').select_option('rejected');page.locator('#incident-notes').fill('No visible item interaction in this fixture.')
                page.locator('#incident-form button[type=submit]').click();expect(page.locator('#incident-dialog')).not_to_be_visible()
                expect(page.locator('.review-badge')).to_have_text('Dismissed')
                page.reload();page.wait_for_function('ops.data?.summary.rejected===1');expect(page.locator('#page-label')).to_have_text('Investigations')
                page.locator('[data-nav="reports"]').click();page.locator('#report-select-all').click();page.locator('#report-preview').click()
                expect(page.locator('#report-message')).to_contain_text('Preview ready')
                page.locator('#report-generate').click();expect(page.locator('#report-message')).to_contain_text('Report saved')
                with page.expect_download() as download:page.locator('#report-library a').filter(has_text='PDF').first.click()
                self.assertTrue(Path(download.value.path()).read_bytes().startswith(b'%PDF'))
                with page.expect_download() as download:page.locator('#report-library a').filter(has_text='DOCX').first.click()
                self.assertTrue(Path(download.value.path()).read_bytes().startswith(b'PK'))
                page.locator('#schedule-name').fill('Monthly briefing');page.locator('#schedule-cadence').select_option('monthly');page.locator('#schedule-form button').click()
                expect(page.locator('#schedule-list')).to_contain_text('Monthly briefing');page.locator('[data-delete-schedule]').click();expect(page.locator('#schedule-list')).to_contain_text('No schedules yet')
                # Undated recordings cannot masquerade as a calendar month's activity.
                page.locator('#report-scope').select_option('month');page.locator('#report-preview').click()
                expect(page.locator('#report-preview-content')).to_contain_text('No analysed recordings match')
                page.set_viewport_size({'width':390,'height':844})
                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                page.locator('#menu-toggle').click();page.locator('[data-nav="system"]').click();page.wait_for_function('ops.health!==null')
                expect(page.locator('#model-readiness')).to_contain_text('Continuous store-footage precision is unmeasured')
                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                self.assertEqual(errors,[]);browser.close()


    def test_saved_live_dashboard_heatmap_exports_and_mobile_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            path=make_video(folder/'camera.mp4',times=list(range(0,1200,100)),width=640,height=360)
            with server(folder) as url,sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url);page.locator('#import-open').click()
                page.locator('#import-form button[type=submit]').click()
                expect(page.locator('#import-error')).to_contain_text('Choose an MP4')
                page.locator('#import-close').click()
                video=page.request.post(url+'/api/videos/import',data={'path':str(path)}).json()
                geometry=page.request.get(url+f'/api/videos/{video["id"]}/geometry').json()['geometry']
                geometry['shapes']=[{'id':'shelf','kind':'shelf','name':'Drinks','points':[[100,100],[400,100],[400,300],[100,300]]}]
                saved=page.request.put(url+f'/api/videos/{video["id"]}/geometry',data=geometry).json()
                key='b'*32;live=folder/'workspace/live'/key;live.mkdir(parents=True)
                event={'id':'candidate','local_track_id':'local:1','start_media_ms':0,'end_media_ms':800,'detected_media_ms':200,'score':.99}
                summary={'id':key,'video_id':video['id'],'geometry_id':saved['id'],'status':'completed','processed_frames':6,'scored_windows':1,'pickups':[event],'threshold':.97,'unique_tracks':1}
                (live/'summary.json').write_text(json.dumps(summary))
                rows=[{'frame_index':ms//100,'media_ms':ms,'tracks':[{'local_track_id':'local:1','position_status':'observed','box':[200,80,300,240]}]} for ms in range(0,1200,200)]
                (live/'observations.jsonl').write_text('\n'.join(map(json.dumps,rows)))
                page.goto(url+'/?video='+video['id'])
                expect(page.locator('#run-status')).to_have_text('Saved live results')
                expect(page.locator('#metric-tracks')).to_have_text('1')
                expect(page.locator('#metric-pickups')).to_have_text('1')
                expect(page.locator('#metric-dwell')).to_have_text('1.0s')
                expect(page.locator('#shelves')).to_contain_text('Drinks')
                self.assertEqual(page.locator('#traffic-chart rect').count(),12)
                page.locator('#show-heatmap').check()
                page.locator('#player').evaluate('e=>{e.currentTime=.8}')
                expect(page.locator('#heatmap')).to_be_visible()
                page.wait_for_function("document.getElementById('heatmap').getContext('2d').getImageData(250,240,1,1).data[3]>0")
                with page.expect_download() as download:page.locator('#shelf-csv').click()
                self.assertEqual(download.value.suggested_filename,'sentient-area-activity.csv')
                with page.expect_download() as download:page.locator('#export').click()
                self.assertEqual(json.loads(Path(download.value.path()).read_text())['id'],key)
                page.locator('[data-pickup]').click()
                page.wait_for_function('document.getElementById("player").currentTime<.1')
                # Recalculate saved observations against a changed area, without inference.
                geometry['shapes'][0]['points']=[[10,10],[60,10],[60,60],[10,60]]
                page.request.put(url+f'/api/videos/{video["id"]}/geometry',data=geometry)
                report=page.request.get(url+f'/api/live/sessions/{key}/shelves').json()
                self.assertEqual(report['areas'][0]['unique_tracks'],0)
                self.assertEqual(report['areas'][0]['pickup_alerts'],0)
                self.assertEqual(page.request.get(url+'/api/live/videos/missing').status,404)
                page.set_viewport_size({'width':390,'height':844})
                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                page.locator('#menu-toggle').click();page.locator('[data-nav="analytics"]').click()
                expect(page.locator('#page-label')).to_have_text('Analytics')
                self.assertEqual(errors,[]);browser.close()

    def test_import_geometry_vertex_drag_reload_help_and_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);path=make_video(folder/'recording.mp4',times=list(range(0,4100,100)),width=640,height=360)
            with server(folder) as url,sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1280,'height':960});page.set_default_timeout(7000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url);page.locator('#import-open').click();page.locator('#video-path').fill(str(path));page.locator('#import-form button[type=submit]').click();expect(page.locator('#video-name')).to_contain_text('recording.mp4')
                page.locator('#geometry-tab').click();expect(page.locator('#shape-list')).to_contain_text('No areas labelled');page.locator('#draw').click();box=page.locator('#overlay').bounding_box()
                for x,y in [(0.2,.2),(.8,.2),(.8,.8),(.2,.8)]:page.mouse.click(box['x']+box['width']*x,box['y']+box['height']*y)
                page.locator('#finish').click();page.locator('#shape-name').fill('Drinks');vertex=page.locator('[data-vertex="0"]').bounding_box();page.mouse.move(vertex['x']+vertex['width']/2,vertex['y']+vertex['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']*.25,box['y']+box['height']*.25);page.mouse.up()
                page.locator('#save-geometry').click();expect(page.locator('#notice')).to_contain_text('Geometry saved');page.reload();page.locator('#geometry-tab').click();expect(page.locator('#shape-list')).to_contain_text('Drinks')
                page.locator('#help-tab').click();expect(page.locator('#help-panel')).to_contain_text('feet must enter');page.locator('#results-tab').click();page.locator('#player').evaluate('e=>e.play()');page.wait_for_function("document.getElementById('player').currentTime>.2");self.assertFalse(errors,errors);browser.close()
