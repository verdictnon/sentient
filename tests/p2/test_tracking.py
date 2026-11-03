from fractions import Fraction
from pathlib import Path
import tempfile
import unittest

from app.p2.config import TrackingConfig
from app.p2.detector import remap_box,convert_outputs
from app.p2.jobs import TimeSampler
from app.p2.tracker import ByteTracker,Detection
from app.p2.setup import source_revision,SOURCE_REVISION


class ConversionTests(unittest.TestCase):
    def test_source_revision_uses_pinned_marker_or_git_files_without_subprocesses(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'.sentient-source-revision').write_text(SOURCE_REVISION)
            self.assertEqual(source_revision(path),SOURCE_REVISION)
            git=path/'.git';git.mkdir();(git/'HEAD').write_text(SOURCE_REVISION)
            self.assertEqual(source_revision(path),SOURCE_REVISION)
            (git/'HEAD').write_text('ref: refs/heads/main')
            (git/'refs/heads').mkdir(parents=True)
            (git/'refs/heads/main').write_text(SOURCE_REVISION)
            self.assertEqual(source_revision(path),SOURCE_REVISION)
            (git/'refs/heads/main').unlink()
            (git/'packed-refs').write_text(SOURCE_REVISION+' refs/heads/main\n')
            self.assertEqual(source_revision(path),SOURCE_REVISION)
            (git/'HEAD').write_text('ref: ../../bad')
            with self.assertRaises(ValueError):source_revision(path)
    def test_stretch_resize_remaps_to_original_pixels_once(self):
        self.assertEqual(remap_box((64,128,320,512),(1280,720)),(128.,144.,640.,576.))
    def test_letterbox_removes_padding_before_scaling(self):
        self.assertEqual(remap_box((64,160,320,480),(1280,720),padding=(0,140),scale=.5),(128.,40.,640.,680.))
    def test_out_of_bounds_is_clipped_and_degenerate_boxes_discarded(self):
        rows=convert_outputs([(-5,-2,110,120),(12,12,12,20)],[.9,.8],[0,0],(100,100),7,.1)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].box,(0.,0.,100.,100.));self.assertEqual(rows[0].frame_index,7)
    def test_only_person_scores_and_original_frame_reference_are_returned(self):
        rows=convert_outputs([(0,0,20,30)]*3,[.9,.8,.01],[0,1,0],(100,100),12,.1)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].score,.9);self.assertEqual(rows[0].class_id,0)
    def test_torchvision_person_index_maps_to_internal_person_class(self):
        rows=convert_outputs([(0,0,20,30)],[.8],[1],(100,100),1,.1,person_class=1)
        self.assertEqual(rows[0].class_id,0)
    def test_config_rejects_nonfinite_unknown_and_incoherent_thresholds(self):
        for values in ({'sample_period_ms':float('nan')},{'device':'bad'},{'max_tracks':False},
                       {'low_threshold':.9,'high_threshold':.5},{'sample_period_ms':2000,'max_lost_ms':1000},{'extra':1}):
            with self.subTest(values=values),self.assertRaises(ValueError):TrackingConfig.parse(values)


class TrackerTests(unittest.TestCase):
    def setUp(self):self.tracker=ByteTracker(TrackingConfig())
    def test_observed_measured_boxes_and_predicted_boxes_are_distinct(self):
        first=self.tracker.advance([Detection((10,10,30,60),.9)],0,(100,100))[0]
        missing=self.tracker.advance(None,100,(100,100))[0]
        measured=self.tracker.advance([Detection((12,10,32,60),.8)],200,(100,100))[0]
        self.assertEqual(first['local_track_id'],measured['local_track_id'])
        self.assertEqual(missing['position_status'],'predicted');self.assertEqual(missing['score'],.9)
        self.assertEqual(measured['position_status'],'observed');self.assertEqual(measured['box'],(12.,10.,32.,60.))
    def test_low_confidence_second_stage_recovers_existing_identity_but_does_not_spawn(self):
        self.assertEqual(self.tracker.advance([Detection((10,10,30,60),.2)],0,(100,100)),[])
        first=self.tracker.advance([Detection((10,10,30,60),.9)],100,(100,100))[0]
        low=self.tracker.advance([Detection((11,10,31,60),.2)],200,(100,100))[0]
        self.assertEqual(first['local_track_id'],low['local_track_id']);self.assertEqual(low['position_status'],'observed')
    def test_short_occlusion_recovers_but_long_gap_creates_new_id(self):
        first=self.tracker.advance([Detection((10,10,30,60),.9)],0,(100,100))[0]
        self.tracker.advance([],200,(100,100))
        recovered=self.tracker.advance([Detection((12,10,32,60),.9)],400,(100,100))[0]
        self.assertEqual(first['local_track_id'],recovered['local_track_id'])
        fresh=self.tracker.advance([Detection((12,10,32,60),.9)],2000,(100,100))[0]
        self.assertNotEqual(first['local_track_id'],fresh['local_track_id'])
    def test_empty_scene_has_no_invented_tracks(self):self.assertEqual(self.tracker.advance([],0,(100,100)),[])
    def test_same_timestamp_and_out_of_order_frames_are_rejected(self):
        self.tracker.advance([],100,(100,100))
        for stamp in (100,50):
            with self.assertRaises(ValueError):self.tracker.advance([],stamp,(100,100))
    def test_multiple_people_get_unique_ids_and_no_identity_reuse(self):
        tracks=self.tracker.advance([Detection((10,10,30,60),.9),Detection((60,10,80,60),.8)],0,(100,100))
        self.assertEqual(len({t['local_track_id'] for t in tracks}),2)
        self.tracker.advance([],2000,(100,100))
        new=self.tracker.advance([Detection((10,10,30,60),.9)],2100,(100,100))[0]
        self.assertNotIn(new['local_track_id'],{t['local_track_id'] for t in tracks})
    def test_capacity_is_bounded_and_reported(self):
        tracker=ByteTracker(TrackingConfig(max_tracks=1))
        result=tracker.advance([Detection((10,10,30,60),.9),Detection((60,10,80,60),.8)],0,(100,100))
        self.assertEqual(len(result),1);self.assertEqual(tracker.capacity_drops,1)
    def test_sampler_uses_elapsed_source_pts_for_variable_frame_rate(self):
        sampler=TimeSampler(200)
        times=[0,100,300,350,600,1000]
        self.assertEqual([t for t in times if sampler.due(Fraction(t))],[0,300,600,1000])
    def test_fractional_time_sampling_is_not_rounded(self):
        sampler=TimeSampler(20)
        self.assertTrue(sampler.due(Fraction(1,8)))
        self.assertFalse(sampler.due(Fraction(160,8)))
        self.assertTrue(sampler.due(Fraction(161,8)))
