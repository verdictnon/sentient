"""Cross-checks, footfall and routes must follow observations and never overclaim."""

import unittest

from app.insights import analyse
from app.journeys import line_flow
from app.p1.geometry import default_geometry, validate_geometry


def shop():
    """A row of areas along x, with a door line at the right edge of the shelves."""
    return dict(
        geometry_version=2,
        frame_dims_px=[1000, 400],
        max_gap_ms=1500,
        active=dict(line=None, zone=None, queue="queue", checkout="till"),
        shapes=[
            dict(id="shelf", name="Drinks", kind="shelf", points=[[0, 0], [200, 0], [200, 400], [0, 400]]),
            dict(id="other", name="Dairy", kind="shelf", points=[[220, 0], [400, 0], [400, 400], [220, 400]]),
            dict(id="queue", name="Line", kind="queue", points=[[420, 0], [600, 0], [600, 400], [420, 400]]),
            dict(id="till", name="Till", kind="checkout", points=[[620, 0], [800, 0], [800, 400], [620, 400]]),
            dict(
                id="door",
                name="Door",
                kind="line",
                points=[[900, 400], [900, 0]],
                hysteresis_px=2,
                positive_means="leave",
            ),
        ],
    )


def walk(track, stops):
    """Observation rows for one track visiting (ms, x) stops."""
    return [
        dict(
            frame_index=index,
            media_ms=ms,
            tracks=[dict(local_track_id=track, box=[x - 10, 100, x + 10, 300], position_status="observed")],
        )
        for index, (ms, x) in enumerate(stops)
    ]


def pickup(ms, track="local:1"):
    return dict(id="p1", local_track_id=track, start_media_ms=ms - 400, end_media_ms=ms, detected_media_ms=ms, score=0.99)


def merged(*groups):
    rows = {}
    for group in groups:
        for row in group:
            target = rows.setdefault(row["media_ms"], dict(frame_index=row["frame_index"], media_ms=row["media_ms"], tracks=[]))
            target["tracks"].extend(row["tracks"])
    return [rows[key] for key in sorted(rows)]


def status(rows, geometry, event=None, physical=True):
    data = analyse(rows, geometry, [event or pickup(200)], physical_time=physical)
    return data["crosscheck"]["events"][0], data


class CrossCheckTests(unittest.TestCase):
    def test_pickup_followed_to_a_till(self):
        check, data = status(walk("local:1", [(0, 100), (200, 100), (800, 300), (1600, 500), (2400, 700), (3000, 700)]), shop())
        self.assertEqual(check["status"], "reached_checkout")
        self.assertEqual(check["checkout_name"], "Till")
        self.assertEqual(check["shelf_name"], "Drinks")
        self.assertEqual(data["summary"]["needs_review"], 0)
        incident = next(e for e in data["incidents"] if e["kind"] == "pickup")
        self.assertEqual(incident["crosscheck"]["status"], "reached_checkout")

    def test_pickup_followed_to_the_door_without_a_till(self):
        # Skips the till polygon by jumping past it between two observed samples.
        check, data = status(walk("local:1", [(0, 100), (200, 100), (1000, 500), (1900, 850), (2800, 950)]), shop())
        self.assertEqual(check["status"], "left_without_checkout")
        self.assertEqual(check["exit_ms"], 2800)
        self.assertEqual(data["summary"]["needs_review"], 1)

    def test_a_track_that_ends_early_is_lost_not_a_thief(self):
        others = walk("local:2", [(ms, 700) for ms in range(0, 12000, 800)])
        check, data = status(merged(walk("local:1", [(0, 100), (200, 100), (800, 100)]), others), shop())
        self.assertEqual(check["status"], "lost_before_checkout")
        self.assertIsNone(check["exit_ms"])
        self.assertEqual(data["crosscheck"]["totals"]["needs_review"], 1)

    def test_a_recording_that_ends_first_leaves_the_outcome_open(self):
        check, data = status(walk("local:1", [(0, 100), (200, 100), (800, 100)]), shop())
        self.assertEqual(check["status"], "still_in_view")
        self.assertEqual(data["summary"]["needs_review"], 0)

    def test_no_mapped_till_is_not_a_failed_check(self):
        layout = shop()
        layout["shapes"] = [s for s in layout["shapes"] if s["kind"] != "checkout"]
        layout["active"].update(queue=None, checkout=None)
        check, _ = status(walk("local:1", [(0, 100), (200, 100), (800, 950)]), layout)
        self.assertEqual(check["status"], "checkout_not_mapped")

    def test_sparse_footage_cannot_be_cross_checked(self):
        check, data = status(walk("local:1", [(0, 100), (200, 100)]), shop(), physical=False)
        self.assertEqual(check["status"], "unavailable")
        self.assertIsNone(data["footfall"]["entries"])
        self.assertEqual(data["routes"]["routes"], [])


class FootfallTests(unittest.TestCase):
    def test_declared_door_direction_counts_entries_and_exits(self):
        rows = walk("local:1", [(0, 950), (800, 850), (1600, 950)])
        data = analyse(rows, shop())
        self.assertEqual(data["footfall"]["entries"], 1)
        self.assertEqual(data["footfall"]["exits"], 1)
        self.assertEqual(data["summary"]["entries"], 1)

    def test_undeclared_door_stays_unknown_rather_than_zero(self):
        layout = shop()
        del layout["shapes"][-1]["positive_means"]
        data = analyse(walk("local:1", [(0, 950), (800, 850)]), layout)
        self.assertFalse(data["footfall"]["configured"])
        self.assertIsNone(data["summary"]["entries"])
        self.assertEqual(data["line_counts"][0]["positive"] + data["line_counts"][0]["negative"], 1)

    def test_line_flow_inverts_the_negative_direction(self):
        self.assertEqual(line_flow(dict(positive_means="enter"), "positive"), "enter")
        self.assertEqual(line_flow(dict(positive_means="enter"), "negative"), "leave")
        self.assertIsNone(line_flow(dict(), "positive"))

    def test_geometry_accepts_only_known_door_meanings(self):
        layout = default_geometry([100, 100])
        line = dict(id="d", name="Door", kind="line", points=[[0, 0], [0, 50]], hysteresis_px=1)
        layout["shapes"] = [line]
        validate_geometry(layout, [100, 100])
        line["positive_means"] = "enter"
        validate_geometry(layout, [100, 100])
        line["positive_means"] = "sideways"
        with self.assertRaises(ValueError):
            validate_geometry(layout, [100, 100])
        line.pop("positive_means")
        layout["shapes"][0] = dict(id="z", name="Zone", kind="zone", points=[[0, 0], [9, 0], [9, 9]], positive_means="enter")
        with self.assertRaises(ValueError):
            validate_geometry(layout, [100, 100])


class RouteTests(unittest.TestCase):
    def stay(self, start, x, seconds):
        return [(start + i * 1000, x) for i in range(seconds)]

    def test_routes_count_stops_and_skip_walking_through(self):
        stops = self.stay(0, 100, 8) + [(8000, 300)] + self.stay(9000, 500, 8) + self.stay(17000, 700, 8)
        data = analyse(walk("local:1", stops), shop())
        names = [step["name"] for step in data["routes"]["routes"][0]["steps"]]
        self.assertEqual(names, ["Drinks", "Line", "Till"])
        flow = {(f["from_name"], f["to_name"]): f["count"] for f in data["routes"]["flows"]}
        self.assertEqual(flow["Drinks", "Line"], 1)

    def test_funnel_steps_are_nested(self):
        shopper = walk("local:1", self.stay(0, 100, 10) + self.stay(10000, 700, 4))
        passer = walk("local:2", self.stay(0, 700, 4))
        funnel = {s["key"]: s["count"] for s in analyse(merged(shopper, passer), shop())["funnel"]}
        self.assertEqual(funnel["observed"], 2)
        self.assertEqual(funnel["engaged"], 1)
        self.assertEqual(funnel["checkout"], 1)


class QueueOutcomeTests(unittest.TestCase):
    def test_served_and_abandoned_waits(self):
        served = walk("local:1", [(ms, 500) for ms in range(0, 3000, 500)] + [(3000, 700), (3500, 700)])
        gave_up = walk("local:2", [(ms, 500) for ms in range(0, 3000, 500)] + [(3000, 300), (3500, 300)])
        queue = analyse(merged(served, gave_up), shop(), rules=dict(queue_limit=2))["queue"]
        self.assertEqual(queue["served"], 1)
        self.assertEqual(queue["abandoned"], 1)
        self.assertEqual(queue["peak_people"], 2)
        self.assertEqual(queue["over_limit_seconds"], 3.0)

    def test_waiting_in_line_is_not_an_extended_dwell_signal(self):
        rows = walk("local:1", [(ms, 500) for ms in range(0, 6000, 500)])
        data = analyse(rows, shop(), rules=dict(dwell_limit_s=1, queue_limit=9))
        self.assertEqual([e for e in data["incidents"] if e["kind"] == "dwell"], [])
        shelf = analyse(walk("local:1", [(ms, 100) for ms in range(0, 6000, 500)]), shop(), rules=dict(dwell_limit_s=1))
        self.assertEqual(len([e for e in shelf["incidents"] if e["kind"] == "dwell"]), 1)


if __name__ == "__main__":
    unittest.main()
