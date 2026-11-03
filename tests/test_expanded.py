import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cache_expanded import frame_windows


class ExpandedTests(unittest.TestCase):
    def sample(self,events):return {'metadata':{'total_frame_count':32},'ground_truth':{'detections':events}}
    def test_conflicting_actions_are_not_single_class_windows(self):
        values=frame_windows(self.sample([{'label':'take','support':[10,15]},{'label':'put','support':[13,18]}]))
        self.assertFalse(any(row['label'] in ['take','put'] for row in values))
    def test_background_does_not_overlap_annotations(self):
        values=frame_windows(self.sample([{'label':'touch','support':[20,24]}]))
        self.assertTrue(any(row['label']=='touch' for row in values))
        for row in values:
            if row['label']=='background':self.assertTrue(row['right']<18 or row['left']>24)
    def test_fresh_holdout_and_paired_views(self):
        root=Path(__file__).resolve().parents[1]
        path=root/'data/retailaction_v2/manifest.json'
        if not path.exists():self.skipTest('Expanded data not prepared')
        plan=json.loads(path.read_text());pilot=json.loads((root/'data/retailaction/validation/manifest.json').read_text())
        groups={}
        for row in plan['views']:
            key=(row['split'],row['sample_id'])
            self.assertEqual(groups.setdefault(key,row['role']),row['role'])
            if row['role']=='holdout':
                self.assertEqual(row['split'],'validation');self.assertNotIn(row['sample_id'],pilot['samples'])


if __name__=='__main__':unittest.main()
