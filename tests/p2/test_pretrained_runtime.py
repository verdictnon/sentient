"""Actual checkpoint execution; setup is required, and nothing is skipped/mocked."""

import unittest
from PIL import Image

from app.p2.config import TrackingConfig
from app.p2.detector import PretrainedDetector, model_metadata
from app.p2.setup import DEFAULT_CACHE, WEIGHTS_SHA256


class PretrainedRuntimeTests(unittest.TestCase):
    def test_pinned_original_weights_load_and_run_on_cpu(self):
        metadata=model_metadata(DEFAULT_CACHE,'rtdetrv2')
        self.assertEqual(metadata['checkpoint_sha256'],WEIGHTS_SHA256)
        detector=PretrainedDetector(TrackingConfig(device='cpu'))
        try:
            output=detector.detect(Image.new('RGB',(320,180),'black'),17)
            self.assertEqual(detector.metadata['device'],'cpu')
            for detection in output:
                self.assertEqual(detection.frame_index,17)
                self.assertEqual(detection.class_id,0)
                self.assertTrue(0<=detection.box[0]<detection.box[2]<=320)
                self.assertTrue(0<=detection.box[1]<detection.box[3]<=180)
        finally:detector.close()
