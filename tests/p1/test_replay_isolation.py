"""Distinct offline detector/model snapshots must not reopen a retired run."""
import json
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from app.p1.server import create_app
from app.storage import stable_id

ROOT=Path(__file__).resolve().parents[2]


class ReplayIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.client=TestClient(create_app(Path(self.temp.name)))
        self.client.__enter__()
        self.rows=[json.loads(s) for s in (ROOT/'contracts/v1/examples.jsonl').read_text().splitlines()]

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def replay(self,rows,isolated=None):
        data={} if isolated is None else {'isolated_run':str(isolated).lower()}
        response=self.client.post('/api/replays',data=data,
            files={'file':('records.jsonl','\n'.join(json.dumps(r) for r in rows))})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_distinct_snapshots_of_same_retired_session_get_distinct_analyses(self):
        first=self.replay(self.rows,True)
        changed=[{**r,'record_id':'second-model:'+r['record_id']} for r in self.rows]
        second=self.replay(changed,True)
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(first['summary']['inward_crossings'],second['summary']['inward_crossings'])
        original=self.client.get('/api/analyses/'+first['id']+'/events').json()
        self.assertEqual(original['analysis']['summary']['database_records'],9)

    def test_exact_snapshot_retry_is_idempotent(self):
        first=self.replay(self.rows,True);again=self.replay(self.rows,True)
        self.assertEqual(first['id'],again['id'])
        self.assertEqual(again['summary']['records_inserted'],0)
        self.assertEqual(again['summary']['events_added'],0)
        self.assertEqual(again['summary']['records_duplicate'],9)

    def test_legacy_append_identity_and_behavior_remain_available(self):
        first=self.replay(self.rows);again=self.replay(self.rows,False)
        config=json.loads((ROOT/'configs/app.example.json').read_text())
        self.assertEqual(first['id'],stable_id([None,None,config]))
        self.assertEqual(first['id'],again['id'])
        self.assertEqual(again['summary']['records_inserted'],0)
