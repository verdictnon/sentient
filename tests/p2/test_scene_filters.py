"""Optional scene filters remove evidence only; source references remain intact."""
import unittest
from app.p2.detector import filter_people
from app.p2.tracker import Detection
from app.p2.config import TrackingConfig


class SceneFilterTests(unittest.TestCase):
    def test_disabled_filter_preserves_all_original_detections(self):
        rows=[Detection((0,0,2,2),.9,0,17),Detection((20,20,80,80),.8,0,17)]
        self.assertEqual(filter_people(rows,(100,100)),rows)

    def test_area_and_overlap_remove_tiny_duplicate_but_keep_distinct_people(self):
        a=Detection((10,10,60,90),.9,0,17)
        duplicate=Detection((11,11,59,89),.8,0,17)
        small=Detection((90,90,91,91),.95,0,17)
        other=Detection((65,10,95,85),.85,0,17)
        result=filter_people([a,duplicate,small,other],(100,100),.002,.4)
        self.assertEqual(result,[a,other])
        self.assertTrue(all(d.frame_index==17 for d in result))

    def test_filter_config_is_bounded_and_changes_run_scope(self):
        base=TrackingConfig().to_dict()
        edited=TrackingConfig(min_person_area_ratio=.002,person_nms_iou=.4).to_dict()
        self.assertNotEqual(base,edited)
        for config in [{'min_person_area_ratio':1.},{'person_nms_iou':-1},{'person_nms_iou':float('nan')}]:
            with self.subTest(config=config),self.assertRaises(ValueError):TrackingConfig(**config)
