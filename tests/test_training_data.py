import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from train_pilot import metrics, pose_bounds, time_indices, windows


class TrainingDataTests(unittest.TestCase):
    def test_capture_time_sampling_uses_irregular_timestamps(self):
        timestamps = [f"1970-01-01T00:00:{value:06.3f}" for value in [0,.05,.1,.15,1.5,2,3,4]]
        indices, _ = time_indices(timestamps,"1970-01-01T00:00:00",0,4)
        self.assertEqual(indices[-1],7)
        self.assertLess(indices.count(0)+indices.count(1)+indices.count(2)+indices.count(3),7)

    def test_missing_timestamps_are_rejected(self):
        with self.assertRaisesRegex(ValueError,"missing_frame_timestamps"):
            time_indices(None,"1970-01-01T00:00:00",0,3)

    def test_fractional_labels_and_background_guard(self):
        content={"segment_info":{"sampled_at_start":"1970-01-01T00:00:00",
                                  "sampled_at_end":"1970-01-01T00:00:10"},
                 "labels":{"action":[{"start":.4,"end":.6,"label":"take"}]}}
        candidates=windows(content)
        event=next(w for w in candidates if w["label"]=="take")
        self.assertAlmostEqual((event["start"]+event["end"])/2,5)
        for background in [w for w in candidates if w["label"]=="background"]:
            self.assertTrue(background["end"]<=3.7 or background["start"]>=6.3)

    def test_pose_order_and_confidence_filter(self):
        pose={"pose":dict(a=[.1,.7],b=[.2,.8],c=[.3,.9],d=[.4,1.0],bad=[0,0]),
              "joint_scores":dict(a=1,b=1,c=1,d=1,bad=0)}
        self.assertEqual(pose_bounds([pose],[0]),(.7,.1,1.0,.4))

    def test_absent_class_metrics_remain_explicit(self):
        import torch
        result=metrics(torch.tensor([0,3]),torch.tensor([0,3]))
        self.assertIsNone(result["per_class"]["touch"]["recall"])
        self.assertIsNone(result["per_class"]["touch"]["f1"])
        self.assertEqual(result["macro_f1_supported"],1)


if __name__=="__main__":
    unittest.main()
