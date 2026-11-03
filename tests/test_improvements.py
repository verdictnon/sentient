import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from improve_v2 import Head,fit_bias,score,cross_calibrate


class ImprovementTests(unittest.TestCase):
    def test_residual_initialization_preserves_trained_model(self):
        model=Head('residual').eval();x=torch.randn(3,2048)
        with torch.no_grad():self.assertTrue(torch.equal(model(x),model.base(x)))
    def test_calibration_preserves_touch_bias_and_cannot_decrease_fit_score(self):
        y=np.array([0,0,1,1,3,3,2]);logits=np.array([[2,0,0,0],[2,0,0,0],[1,.8,0,0],[1,.7,0,0],[0,0,0,2],[0,0,0,2],[0,0,2,0]],np.float32)
        bias=fit_bias(y,logits)
        self.assertEqual(float(bias[2]),0.);self.assertGreaterEqual(score(y,(logits+bias).argmax(1)),score(y,logits.argmax(1)))
    def test_out_of_fold_bias_never_fits_heldout_labels(self):
        labels=np.array([0,0,3,3]);folds=np.array([0,0,1,1]);received=[]
        def fit(y,p):received.append(y.tolist());return np.zeros(4)
        with patch('improve_v2.fit_bias',side_effect=fit):cross_calibrate(labels,np.zeros((4,4)),folds)
        self.assertEqual(received,[[3,3],[0,0]])


if __name__=='__main__':unittest.main()
