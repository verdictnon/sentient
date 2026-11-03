import json
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from features_v6 import fixed_boxes,OUT


class HandDetailTests(unittest.TestCase):
    def poses(self,left=True,right=True):
        p=np.zeros((24,2),np.float32);c=np.zeros(24,np.float32)
        for enabled,ids,x in [(left,[8,9,10],.2),(right,[4,5,6],.8)]:
            if enabled:
                p[ids]=np.array([[x,.4],[x,.5],[x,.52]]);c[ids]=1
        return [(p.copy(),c.copy()) for _ in range(4)]
    def test_separate_hands_use_correct_original_image_coordinates(self):
        boxes,valid=fixed_boxes(self.poses(),600,600)
        self.assertEqual(valid,[1.,1.]);left,right=boxes[1:]
        self.assertLess(left[2],right[0]);self.assertAlmostEqual((left[0]+left[2])/2,120,delta=1)
        self.assertAlmostEqual((right[0]+right[2])/2,480,delta=1)
    def test_missing_hand_has_explicit_validity_and_body_fallback(self):
        boxes,valid=fixed_boxes(self.poses(right=False),600,600)
        self.assertEqual(valid,[1.,0.]);self.assertEqual(boxes[2],boxes[0])
    def test_crop_is_fixed_over_the_clip_not_dependent_on_frame_order(self):
        poses=self.poses();self.assertEqual(fixed_boxes(poses,600,600),fixed_boxes(list(reversed(poses)),600,600))
    def test_pilot_has_no_test_clips_or_split_camera_pairs(self):
        path=OUT/'index.json'
        if not path.exists():self.skipTest('Pilot not prepared')
        info=json.loads(path.read_text());groups={}
        for row in info['views']:
            self.assertNotEqual(row['split'],'test');key=(row['split'],row['sample_id'])
            self.assertEqual(groups.setdefault(key,row['pilot_role']),row['pilot_role'])
        self.assertEqual(len(info['tiny_examples']),16)


if __name__=='__main__':unittest.main()
