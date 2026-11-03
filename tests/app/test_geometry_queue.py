import unittest

from app.domain import Observation, TrackKey
from app.geometry import Line, Polygon
from app.queues import QueueVisit


class GeometryTests(unittest.TestCase):
    def test_polygon_boundary_is_inside(self):
        polygon = Polygon(((55, 0), (85, 0), (85, 100), (55, 100)))
        for point in ((55, 50), (85, 50), (55, 0), (70, 50)):
            self.assertTrue(polygon.contains(point))
        self.assertFalse(polygon.contains((54, 50)))

    def test_hysteresis_is_measured_in_pixels(self):
        line = Line((50, 100), (50, 0), 5)
        for x, side in ((45, -1), (46, 0), (54, 0), (55, 1)):
            self.assertEqual(line.side((x, 50)), side)

    def test_invalid_geometry_is_rejected(self):
        for points in (((0, 0), (0, 0)), ((0, 0), (float("nan"), 0))):
            with self.assertRaises(ValueError):
                Line(*points)
        for vertices in (((0, 0), (1, 1)), ((0, 0), (1, 1), (2, 2)),
                         ((0, 0), (3, 3), (0, 2), (3, 0))):
            with self.assertRaises(ValueError):
                Polygon(vertices)

    def test_invalid_internal_time_and_position_are_rejected(self):
        key = TrackKey("s", "c", "session", "7")
        # Original v1 schema permits fractional milliseconds (type=number).
        for time in (-1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Observation("id", key, time, (0, 0))
        with self.assertRaises(ValueError):
            Observation("id", key, 0, (float("inf"), 0))


class QueueTests(unittest.TestCase):
    def test_queue_abandonment_does_not_report_completed_wait(self):
        result = QueueVisit(1000).end(4000, departure_observed=True)
        self.assertEqual(result["outcome"], "abandoned")
        self.assertIsNone(result["waiting_ms"])
        self.assertEqual(result["observed_waiting_ms"], 3000)
        self.assertIsNone(result["service_ms"])

    def test_service_completion_wait_ends_at_service_start(self):
        visit = QueueVisit(1000)
        visit.start_service(3000)
        result = visit.end(6000, departure_observed=True)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["waiting_ms"], 2000)
        self.assertEqual(result["service_ms"], 3000)

    def test_missing_departure_is_censored_not_abandoned_or_completed(self):
        visit = QueueVisit(1000)
        visit.start_service(2000)
        result = visit.end(3000, departure_observed=False)
        self.assertEqual(result["outcome"], "censored")
        self.assertIsNone(result["service_ms"])

    def test_partial_queue_track_has_no_completed_wait(self):
        visit = QueueVisit(1000, entry_observed=False)
        visit.start_service(2000)
        result = visit.end(3000, departure_observed=True)
        self.assertIsNone(result["waiting_ms"])
        self.assertEqual(result["observed_waiting_ms"], 1000)

    def test_invalid_or_repeated_queue_transitions_are_rejected(self):
        visit = QueueVisit(1000)
        with self.assertRaises(ValueError):
            visit.start_service(500)
        visit.start_service(2000)
        with self.assertRaises(ValueError):
            visit.start_service(3000)
        with self.assertRaises(ValueError):
            visit.end(1500, departure_observed=True)
        visit.end(3000, departure_observed=True)
        with self.assertRaises(ValueError):
            visit.end(4000, departure_observed=True)
