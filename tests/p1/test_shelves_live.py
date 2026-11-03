"""Geometry gaps, physical dwell and pickup association must not invent visits."""
import unittest
from app.p1.geometry import default_geometry, validate_geometry
from app.shelves import ShelfStats


def geometry():
    g=default_geometry([100,100]);g['shapes']=[{'id':'shelf','name':'Drinks','kind':'shelf','points':[[20,20],[80,20],[80,90],[20,90]]}];return g

def track(x=50,status='observed',identity='1'):
    return {'local_track_id':identity,'box':[x-5,20,x+5,80],'position_status':status}

class AreaTests(unittest.TestCase):
    def test_new_camera_has_no_invented_entrance_or_shelf(self):
        g=default_geometry([100,100]);self.assertEqual(g['shapes'],[]);validate_geometry(g,[100,100])
    def test_predictions_and_gaps_do_not_credit_dwell(self):
        s=ShelfStats(geometry());s.update(0,[track()]);s.update(200,[track()]);s.update(400,[track(status='predicted')]);s.update(1800,[track()]);r=s.snapshot()['areas'][0]
        self.assertEqual(r['observed_dwell_seconds'],.2);self.assertEqual(r['unique_tracks'],1);self.assertEqual(r['visits'],2)
    def test_sparse_time_unavailable_and_pickup_counted_once(self):
        s=ShelfStats(geometry(),False);s.update(0,[track()]);s.update(200,[track()]);e={'id':'alert'};s.pickup(e,track());s.pickup(e,track());r=s.snapshot()['areas'][0]
        self.assertIsNone(r['observed_dwell_seconds']);self.assertEqual(r['pickup_alerts'],1);self.assertEqual(e['shelf_ids'],['shelf'])
    def test_self_intersection_rejected(self):
        g=geometry();g['shapes'][0]['points']=[[10,10],[90,90],[10,90],[90,10]]
        with self.assertRaises(ValueError):validate_geometry(g,[100,100])
    def test_line_crossing_requires_observed_path_and_correct_direction(self):
        g=geometry();g['shapes'].append({'id':'exit','name':'Exit','kind':'line','points':[[50,100],[50,0]],'hysteresis_px':2})
        s=ShelfStats(g);s.update(0,[track(20)]);s.update(200,[track(80)]);s.update(400,[track(20,'predicted')]);r=s.snapshot()['crossings'][0]
        self.assertEqual((r['positive'],r['negative']),(1,0))

class PersistedLiveTests(unittest.TestCase):
    def test_completed_session_export_survives_manager_restart(self):
        import json,tempfile
        from pathlib import Path
        from app.p1.catalog import Catalog
        from app.live import LiveManager
        with tempfile.TemporaryDirectory() as directory:
            catalog=Catalog(Path(directory));key='a'*32;folder=catalog.root/'live'/key;folder.mkdir(parents=True)
            payload={'id':key,'video_id':'video','geometry_id':'geometry','status':'completed','processed_frames':40}
            (folder/'summary.json').write_text(json.dumps(payload));manager=LiveManager(catalog,None,None)
            try:
                saved=manager.get(key);self.assertTrue(saved['done'].is_set());self.assertEqual(manager.public(saved)['latest'],payload)
                with self.assertRaises(KeyError):manager.get('../outside')
            finally:manager.shutdown()
