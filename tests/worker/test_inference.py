"""Exercise real decoding/runtime/errors with an explicitly synthetic tiny model."""
from fractions import Fraction
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import av
import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from worker.model import Runtime, sha256, FORMAT, CLASSES
from worker.pipeline import Policy, index_video, run_video
from worker.contracts import canonical
from test_pipeline import track


def make_video(path):
    times=[5000,5030,5090,5250,5500,5570,5800,5830,5960,6200,6500,6900,7200,7600]
    with av.open(str(path),'w',format='mp4') as container:
        stream=container.add_stream('libx264',rate=10)
        stream.width=100;stream.height=100;stream.pix_fmt='yuv420p'
        stream.time_base=Fraction(1,1000);stream.codec_context.time_base=Fraction(1,1000)
        stream.codec_context.max_b_frames=0
        for i,t in enumerate(times):
            frame=av.VideoFrame.from_ndarray(np.full((100,100,3),i*15,dtype=np.uint8),format='rgb24')
            frame.pts=t;frame.time_base=Fraction(1,1000)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    return times


class TinyFixture(torch.nn.Module):
    def forward(self,x):
        m=x.mean(dim=(1,2,3,4))
        return torch.stack((m,-m,m*.25,m*.1),dim=1)


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.video=self.root/'vfr.mp4';self.times=make_video(self.video)
        self.model=self.root/'model';self.model.mkdir()
        torch.jit.trace(TinyFixture(),torch.zeros(1,3,13,160,160)).save(str(self.model/'model.pt'))
        self.metadata={'format':FORMAT,'model_sha256':sha256(self.model/'model.pt'),
            'classes':CLASSES,'model_id':'SYNTHETIC_TEST_ONLY'}
        (self.model/'manifest.json').write_text(json.dumps(self.metadata))
        self.tracks=self.root/'tracks.jsonl'
        self.rows=[track(i,t) for i,t in enumerate(self.times)]
        self.write_tracks(self.rows)
        self.runtime=Runtime(self.model,'cpu')

    def tearDown(self):self.temp.cleanup()

    def write_tracks(self,rows):self.tracks.write_text(''.join(canonical(r)+'\n' for r in rows))

    def run_worker(self,name='output',policy=None,runtime=None):
        return run_video(self.video,self.tracks,self.model,self.root/name,policy or Policy(),runtime or self.runtime)

    def test_vfr_nonzero_pts_retry_is_byte_identical(self):
        index=index_video(self.video)
        self.assertAlmostEqual(index['frames'][0]['media_ms'],5000)
        summary=self.run_worker()
        self.assertGreater(summary['scored_windows'],0)
        self.run_worker('retry')
        self.assertEqual((self.root/'output/records.jsonl').read_bytes(),(self.root/'retry/records.jsonl').read_bytes())
        evidence=json.loads((self.root/'output/evidence.json').read_text())
        for w in evidence['windows']:
            self.assertGreaterEqual(w['start_media_ms'],5000)
            self.assertEqual(len(w['model_input_indices']),13)
        rows=[json.loads(s) for s in (self.root/'output/records.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['action']=='unknown' and r['abstained'] for r in rows if r['type']=='action.scored'))

    def test_frame_record_mismatch_leaves_no_output(self):
        self.rows[1]['media_time_ms']+=10;self.write_tracks(self.rows)
        with self.assertRaisesRegex(ValueError,'PTS'):self.run_worker()
        self.assertFalse((self.root/'output').exists())

    def test_runtime_failure_leaves_no_fake_result(self):
        class Broken:
            metadata={'model_id':'broken','model_sha256':'broken'}
            device='cpu';torch=torch
            def predict(self,arrays):raise RuntimeError('forced inference failure')
            def memory_mib(self):return None
        with self.assertRaisesRegex(ValueError,'not committed'):self.run_worker(runtime=Broken())
        self.assertFalse((self.root/'output').exists())

    def test_model_checksum_failure_is_explicit(self):
        (self.model/'model.pt').write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError,'checksum'):Runtime(self.model,'cpu')

    def test_short_or_predicted_only_inputs_never_make_background(self):
        self.write_tracks(self.rows[:2])
        summary=self.run_worker()
        self.assertEqual(summary['scored_windows'],0)
        scored=[json.loads(s) for s in (self.root/'output/records.jsonl').read_text().splitlines() if 'action.scored' in s]
        self.assertTrue(all(r['scores'] is None and r['action']=='unknown' for r in scored))
        self.write_tracks([{**r,'position_status':'predicted'} for r in self.rows])
        empty=self.run_worker('predicted')
        self.assertEqual(empty['candidates'],0)
        self.assertEqual((self.root/'predicted/records.jsonl').read_text(),'')

    def test_concurrent_window_bound_fails_without_committing(self):
        rows=[]
        for i,t in enumerate(self.times):
            rows.extend([track(i,t,local='A'),track(i,t,local='B')])
        self.write_tracks(rows)
        with self.assertRaisesRegex(ValueError,'memory bound'):
            self.run_worker(policy=Policy(max_active_windows=1))
        self.assertFalse((self.root/'output').exists())

    def test_outputs_cannot_silently_overwrite_previous_run(self):
        self.run_worker()
        original=(self.root/'output/records.jsonl').read_bytes()
        with self.assertRaisesRegex(ValueError,'fresh'):self.run_worker()
        self.assertEqual((self.root/'output/records.jsonl').read_bytes(),original)


if __name__=='__main__':unittest.main()
