import json
from pathlib import Path
import sys
import unittest
import numpy as np
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from features_v3 import labels_for
from temporal_v3 import ap,spans,match_events,normalize


class TemporalTests(unittest.TestCase):
    def test_overlapping_rare_actions_are_retained(self):
        target=labels_for({'ground_truth':{'detections':[{'label':'take','support':[2,5]},{'label':'put','support':[4,7]}]}},8)
        self.assertTrue(np.all(target[:2,3:5]==1))
        self.assertEqual(target[:,8:].sum(),0)
    def test_invalid_support_is_rejected(self):
        with self.assertRaises(ValueError):labels_for({'ground_truth':{'detections':[{'label':'touch','support':[1,10]}]}},8)
    def test_padding_does_not_affect_normalization(self):
        x=torch.zeros(2,4,32);x[:,:,:8]=2;x[:,:,8:]=100
        value,mean,std,mask=normalize(x,np.array([8,8]))
        self.assertTrue(torch.equal(mean,torch.full((4,),2.)))
        self.assertEqual(float(value.abs().sum()),0.)
    def test_average_precision_resolves_ties_as_groups(self):
        self.assertAlmostEqual(ap([1,0],[.5,.5]),.5)
        self.assertAlmostEqual(ap([0,1],[.5,.5]),.5)
        self.assertAlmostEqual(ap([1,0],[.8,.2]),1.)
    def test_event_matching_is_one_to_one(self):
        self.assertEqual(spans([0,1,1,0,1]),[(1,3),(4,5)])
        self.assertEqual(match_events([(1,5)],[(1,4),(4,5)]),(1,1,0))
    def test_visits_and_hashes_stay_in_one_role(self):
        path=Path(__file__).resolve().parents[1]/'data/retailaction_v3/manifest.json'
        if not path.exists():self.skipTest('V3 manifest unavailable')
        groups={};hashes={}
        for row in json.loads(path.read_text())['views']:
            role=row['role'];key=(row['split'],row['sample_id'])
            self.assertEqual(groups.setdefault(key,role),role)
            self.assertEqual(hashes.setdefault(row['remote_sha256'],role),role)
            if role=='test':self.assertEqual(row['split'],'test')


if __name__=='__main__':unittest.main()
