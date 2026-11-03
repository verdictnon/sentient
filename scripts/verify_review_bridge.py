"""Real-clip/P1/browser round trip; generated decisions are AUTOMATED fixtures.

Uses an isolated app workspace and never certifies a real action label. Refuses
to reuse an output directory so old proof cannot masquerade as a fresh run.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright, expect

import review_bridge as b


def run(output, app_root, app_python, pack_path):
    output = Path(output).resolve()
    if output.exists():
        raise b.BridgeError('Use a fresh verification output directory')
    output.mkdir(parents=True)
    pack = b.load_pack(pack_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    with (output/'server.log').open('w') as log:
        process = subprocess.Popen([str(app_python),'-m','app.p1.server','--port',str(port),
            '--data-dir',str(output/'workspace')], cwd=app_root, stdout=log, stderr=log)
        try:
            deadline = time.monotonic()+20
            while True:
                try:
                    with b.client(url) as api:
                        b.response_json(api.get('/api/status'))
                    break
                except Exception:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise b.BridgeError('Verification app failed to launch: '+(output/'server.log').read_text())
                    time.sleep(.1)
            receipt_path = output/'import-receipt.json'
            receipt = b.import_pack(pack_path,receipt_path,url,limit_groups=1)
            receipt_before = b.sha256(receipt_path)
            # Import retry must preserve targets and leave every action unscored.
            retry = b.import_pack(pack_path,receipt_path,url,limit_groups=1)
            assert all(s['records_inserted']==0 and s['events_added']==0
                       for s in retry['last_replay_summaries'].values())
            assert b.sha256(receipt_path)==receipt_before
            imported = retry['entries'][0]
            entry = next(e for e in pack['entries'] if e['entry_id']==imported['entry_id'])
            target = next(t for t in entry['targets'] if t['suggested_label_unverified']=='touch')
            binding = imported['bindings'][0]
            with b.client(url) as api:
                evidence = b.response_json(api.get(f'/api/analyses/{binding["analysis_id"]}/events'))
            event_index = next(i for i,e in enumerate(evidence['events']) if e['target_id']==target['target_id'])
            errors = []
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width':1450,'height':1000})
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(url,wait_until='networkidle')
                page.locator(f'[data-video="{imported["video_id"]}"]').click()
                page.wait_for_load_state('networkidle')
                expect(page.locator('#camera-label')).to_contain_text(entry['subject_track_key'][1])
                expect(page.locator('#events-body tr')).to_have_count(len(evidence['events']))
                page.locator('#events-body tr').nth(event_index).click()
                expect(page.locator('#review-form')).to_be_visible()
                expect(page.locator('#review-label')).to_have_value('unknown')
                page.wait_for_function("document.getElementById('frame-image').naturalWidth>0")
                # The decision is a test fixture, not an AI/human adjudication.
                page.locator('#reviewer').fill('AUTOMATED BRIDGE INTEGRATION FIXTURE')
                page.locator('#decision').select_option('corrected')
                page.locator('#review-label').select_option('touch')
                page.locator('#visibility').select_option('clear')
                page.locator('#uncertainty').select_option('low')
                page.locator('#review-notes').fill('AUTOMATED FIXTURE ONLY. Exercises integration; does not establish this action or visibility. Excluded from training.')
                page.get_by_role('button',name='Save revision',exact=True).click()
                expect(page.locator('#notice')).to_contain_text('saved as revision 1')
                page.screenshot(path=str(output/'real-clip-review-fixture.png'),full_page=True)
                browser.close()
            if errors:
                raise b.BridgeError('Browser errors: '+str(errors))
            export = output/'reviews.jsonl'
            assert b.export_reviews(receipt_path,export)==1
            reviews = b.read_jsonl(export)
            reader = b.ReviewReader(pack_path,receipt_path)
            reader.validate(reviews[0])
            # Create a later pending revision via the same supported API and
            # check that it retracts the older usable-looking fixture.
            pending_body = {'analysis_id':binding['analysis_id'],'target_id':target['target_id'],
                'request_id':'bridge-pending-fixture-v1','expected_revision':1,
                **reviews[0]['human'],'decision':'pending','label':'unknown',
                'start_media_ms':reviews[0]['start_media_ms'],'end_media_ms':reviews[0]['end_media_ms']}
            with b.client(url) as api:
                pending = b.response_json(api.post(f'/api/videos/{imported["video_id"]}/reviews',json=pending_body))
            b.export_reviews(receipt_path,export)
            assertions = output/'fixture-attestations.jsonl'
            b.attest(export,assertions,[r['annotation_id'] for r in b.read_jsonl(export)],'automated_fixture','Automated bridge verifier')
            pending_summary = b.build_dataset(pack_path,receipt_path,[export],output/'pending-dataset.json',assertions)
            assert pending_summary['eligible_records']==0
            pending_rows = b.read_json(output/'pending-dataset.json')['excluded_latest_reviews']
            assert len(pending_rows)==1 and pending_rows[0]['annotation_id']==pending['annotation_id']
            assert 'decision_pending' in pending_rows[0]['reasons']
            corrected_body = {**pending_body,'request_id':'bridge-final-fixture-v1','expected_revision':2,
                              'decision':'corrected','label':'touch'}
            with b.client(url) as api:
                corrected = b.response_json(api.post(f'/api/videos/{imported["video_id"]}/reviews',json=corrected_body))
                retried = b.response_json(api.post(f'/api/videos/{imported["video_id"]}/reviews',json=corrected_body))
                assert corrected==retried
            b.export_reviews(receipt_path,export)
            b.attest(export,assertions,[corrected['annotation_id']],'automated_fixture','Automated bridge verifier')
            summary = b.build_dataset(pack_path,receipt_path,[export],output/'training-dataset.json',assertions,
                                      fixture_output=output/'integration-fixture-dataset.json')
            assert b.load_dataset(output/'training-dataset.json',pack_path,receipt_path)==[]
            try:
                b.load_dataset(output/'integration-fixture-dataset.json',pack_path,receipt_path)
                raise AssertionError('Fixture was accepted as training data')
            except b.BridgeError:
                pass
            examples = b.load_dataset(output/'integration-fixture-dataset.json',pack_path,receipt_path,allow_fixtures=True)
            assert len(examples)==1 and examples[0]['role']=='train'
            assert examples[0]['clip_group']==entry['clip_group']
            assert examples[0]['label_mask'][2].count(True)==len(corrected['frame_indices'])
            assert not any(examples[0]['label_mask'][0]) and not any(examples[0]['label_mask'][1])
            proof = {'verification_kind':'automated_integration_fixture_on_real_source_media',
                'bridge_sha256':b.sha256(b.ROOT/'scripts/review_bridge.py'),
                'verifier_sha256':b.sha256(b.ROOT/'scripts/verify_review_bridge.py'),
                'pack_sha256':b.sha256(pack_path),
                'human_adjudications_performed':0,'training_eligible_records':0,
                'pack_groups':pack['groups'],'pack_views':pack['views'],
                'imported_views':len(receipt['entries']),'source_group':entry['clip_group'],
                'media_sha256':entry['media_sha256'],'source_kind':'sparse_clip',
                'capture_utc_invented':False,'paired_labels_propagated':False,
                'browser_saved_review':True,'browser_errors':errors,'review_revisions':[1,2,3],
                'import_retry_adds_records_or_events':False,'review_save_retry_adds_revision':False,
                'latest_pending_retracts_older_label':True,'fixture_loader_requires_explicit_opt_in':True,
                'masked_supervision_verified':True,'dataset_summary':summary,
                'app_root':str(app_root),'app_code_modified':False}
            b.write_json(output/'verification.json',proof)
            return proof
        finally:
            process.terminate()
            process.wait(timeout=10)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--app-root',type=Path,default=b.APP/'.local/review-copy')
    p.add_argument('--app-python',type=Path,default=b.APP/'.venv-app/bin/python')
    p.add_argument('--pack',type=Path,default=b.ROOT/'runs/review_pack_v2/pack.json')
    a = p.parse_args()
    # Resolving the venv executable symlink would bypass its environment.
    proof = run(a.output,a.app_root.resolve(),a.app_python.absolute(),a.pack.resolve())
    print(json.dumps(proof,indent=2))


if __name__=='__main__':
    main()
