"""Regression checks for the new temporal mapping and bounded crop context."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from compare_backbones_v7 import map_nodes
from short_hand_v8 import short_boxes


class BackboneInputTests(unittest.TestCase):
    def test_temporal_interpolation_preserves_linear_signal_and_clamps_edges(self):
        value = map_nodes(np.array([[2., 6., 10.]]), np.array([1, 3, 5]), 7)
        np.testing.assert_allclose(value, [[2, 2, 4, 6, 8, 10, 10]])

    def test_duplicate_sampled_indices_do_not_introduce_a_temporal_jump(self):
        value = map_nodes(np.array([[1., 3., 6.]]), np.array([0, 0, 4]), 5)
        np.testing.assert_allclose(value, [[2, 3, 4, 5, 6]])

    def test_short_crop_is_unaffected_by_motion_outside_its_context(self):
        points = np.zeros((24, 2), np.float32)
        confidence = np.zeros(24, np.float32)
        points[[8, 9, 10]] = [[.2, .3], [.2, .4], [.2, .42]]
        confidence[[8, 9, 10]] = 1
        sequence = [(points.copy(), confidence.copy()) for _ in range(9)]
        original, validity = short_boxes(sequence, 600, 600)
        sequence[-1][0][[8, 9, 10], 0] = .9
        changed, _ = short_boxes(sequence, 600, 600)
        self.assertEqual(original[2], changed[2])
        self.assertNotEqual(original[-1], changed[-1])
        self.assertTrue(np.all(validity[0] == 1))
        self.assertTrue(np.all(validity[1] == 0))


if __name__ == '__main__':
    unittest.main()
