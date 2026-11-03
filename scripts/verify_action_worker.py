"""Verify actual worker scores -> P1 browser review -> validated masked dataset.

All saved decisions are isolated AUTOMATED fixtures; never human adjudications.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright, expect
import review_bridge as b


def run(pack_path, output, app_root, app_python):
    pack_path,output,app_root=Path(pack_path).resolve(),Path(output).resolve(),Path(app_root).resolve()
    if output.exists():raise b.BridgeError('Use a fresh verification output directory')
    output.mkdir(parents=True)
    pack=b.load_pack(pack_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    with (output/'server.log').open('w') as log:
        proc=subprocess.Popen([str(Path(app_python).absolute()),'-m','app.p1.server','--port',str(port),
            '--data-dir',str(output/'workspace')],cwd=app_root,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+20
            while True:
                try:
                    with b.client(url) as api:b.response_json(api.get('/api/status'))
                    break
                except Exception:
                    if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Verification server failed')
                    time.sleep(.1)
            receipt_path=output/'receipt.json'
            receipt=b.import_pack(pack_path,receipt_path,url,limit_groups=1)
            retry=b.import_pack(pack_path,receipt_path,url,limit_groups=1)
            assert all(v['records_inserted']==0 and v['events_added']==0 for v in retry['last_replay_summaries'].values())
            imported=receipt['entries'][0]
            entry=next(e for e in pack['entries'] if e['entry_id']==imported['entry_id'])
            target=entry['targets'][0]
            original=[r for r in entry['wire_records'] if r['type']=='action.scored' and r['candidate_id']==target['candidate_id']][0]
            assert original['scores'] and original['quality']!='mock' and original['action']=='unknown' and original['abstained']
            with b.client(url) as api:
                events=b.response_json(api.get(f'/api/analyses/{imported["bindings"][0]["analysis_id"]}/events'))
                assert all(r['kind']=='action.scored' for r in events['events'])
                assert events['analysis']['summary']['inward_crossings']==0
                alternate=[]
                for record in entry['wire_records']:
                    record=dict(record)
                    if record['type']=='action.scored':
                        record['record_id']='alternate-fixture:'+record['record_id']
                        record['model_id']='ALTERNATE INTEGRATION FIXTURE ONLY'
                    alternate.append(record)
                other=b.response_json(api.post('/api/replays',data={'video_id':imported['video_id'],'isolated_run':'true'},
                    files={'file':('alternate.jsonl',''.join(b.canonical(r)+'\n' for r in alternate).encode(),'application/x-ndjson')}))
                assert other['id']!=imported['bindings'][0]['analysis_id']

            errors=[]
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1450,'height':1000})
                page.on('pageerror',lambda e:errors.append(str(e)))
                from urllib.parse import urlencode
                page.goto(url+'/?'+urlencode({'video':imported['video_id'],'analysis':imported['bindings'][0]['analysis_id'],'target':target['target_id']}),wait_until='networkidle')
                expect(page.locator('#review-form')).to_be_visible()
                expect(page.locator('#review-label')).to_have_value('unknown')
                expect(page.locator('#overlay')).to_be_hidden()
                expect(page.locator('#model-scores')).to_be_visible()
                expect(page.locator('#model-scores')).to_contain_text('uncalibrated')
                page.locator('#reviewer').fill('AUTOMATED REAL-WORKER ROUNDTRIP FIXTURE')
                page.locator('#decision').select_option('corrected')
                page.locator('#review-label').select_option('touch')
                page.locator('#visibility').select_option('clear')
                page.locator('#uncertainty').select_option('low')
                page.locator('#review-notes').fill('AUTOMATED FIXTURE ONLY. Tests real-score provenance; does not assert a visible touch action.')
                page.get_by_role('button',name='Save revision',exact=True).click()
                expect(page.locator('#notice')).to_contain_text('saved as revision 1')
                page.screenshot(path=str(output/'real-worker-review-fixture.png'),full_page=True)
                page.locator('#analysis-select').select_option(other['id'])
                expect(page.locator('#review-form')).to_be_hidden()
                expect(page.locator('#model-scores')).to_be_hidden()
                browser.close()
            assert not errors,errors
            export=output/'reviews.jsonl'
            assert b.export_reviews(receipt_path,export)==1
            row=b.read_jsonl(export)[0]
            b.ReviewReader(pack_path,receipt_path).validate(row)
            assert row['model_outputs']==[original]
            assertions=output/'attestations.jsonl'
            b.attest(export,assertions,[row['annotation_id']],'automated_fixture','Automated worker verifier')
            summary=b.build_dataset(pack_path,receipt_path,[export],output/'dataset.json',assertions,
                fixture_output=output/'fixture-dataset.json')
            assert summary['eligible_records']==0
            assert b.load_dataset(output/'dataset.json',pack_path,receipt_path)==[]
            try:b.load_dataset(output/'fixture-dataset.json',pack_path,receipt_path)
            except b.BridgeError:pass
            else:raise AssertionError('Fixture dataset must be blocked by default')
            fixtures=b.load_dataset(output/'fixture-dataset.json',pack_path,receipt_path,allow_fixtures=True)
            assert len(fixtures)==1
            proof={'real_model_scores_preserved':True,'source_action':'unknown','source_abstained':True,
                'source_quality':original['quality'],'wire_schema_unchanged':True,
                'direct_recording_and_target_link_verified':True,'analysis_switch_clears_stale_review':True,'uncalibrated_score_display_verified':True,'browser_errors':errors,
                'replay_retry_duplicate_events':0,'browser_saved_review':True,
                'automated_fixture_excluded_from_training':True,'masked_dataset_loader_roundtrip':True,
                'human_adjudications_performed':0,'training_eligible_labels_created':0,
                'model_id':original['model_id'],'scores':original['scores'],
                'pack_sha256':b.sha256(pack_path),'app_js_sha256':b.sha256(app_root/'web/app.js')}
            b.write_json(output/'verification.json',proof)
            return proof
        finally:
            proc.terminate();proc.wait(timeout=10)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pack',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--app-root',type=Path,default=b.APP)
    p.add_argument('--app-python',type=Path,default=b.ROOT/'.venv-app/bin/python')
    a=p.parse_args()
    print(json.dumps(run(a.pack,a.output,a.app_root,a.app_python),indent=2))


if __name__=='__main__':main()
