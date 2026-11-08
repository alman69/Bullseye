"""State-machine tests for planner.py using hand-built detections and floor results."""
from __future__ import annotations

import unittest

from bullseye_vision.detector import Detection
from bullseye_vision.freespace import FreeSpace
from bullseye_vision.planner import (APPROACH, ARRIVED, ARRIVED_ACTION, AVOID, BACKWARD, EXPLORE, FORWARD, GIVE_UP,
                     READ_DISTANCE, SEARCH, STOP, TURN_LEFT, TURN_RIGHT, NavSettings, Planner)

BEARINGS = [-0.8, -0.4, 0.0, 0.4, 0.8]


def det(err=None, area_ratio=0.01, bottom=None, dist=None):
    if err is None:
        return Detection("red", False, 800, 600)
    bottom = 0.6 if bottom is None else bottom
    d = Detection("red", True, 800, 600, center_x=int(400 + err * 400), center_y=int(bottom * 600) - 20,
                  horizontal_error=err, area=area_ratio * 480000, area_ratio=area_ratio,
                  bbox=[int(380 + err * 400), int(bottom * 600) - 40, 40, 40])
    d.bottom_y = bottom
    d.distance_cm_est = dist
    return d


def free(heights=(1, 1, 1, 1, 1), visible=True, threshold=0.35):
    blocked = [h < threshold for h in heights]
    corridor = [1, 2, 3]
    best = max(range(5), key=lambda i: (heights[i], -abs(BEARINGS[i])))
    return FreeSpace(list(heights), BEARINGS, blocked, corridor, any(blocked[i] for i in corridor),
                     best, visible, 1.0 if visible else 0.0, 300, 800, 600)


CLEAR = free()


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.s = NavSettings()
        self.p = Planner(self.s, use_distance=True)

    def test_starts_searching_and_turns(self):
        a = self.p.decide(det(), CLEAR, now=0)
        self.assertEqual(self.p.state, SEARCH)
        self.assertIn(a.kind, (TURN_LEFT, TURN_RIGHT))
        self.assertEqual(a.duration_ms, self.s.search_turn_ms)
        self.assertEqual(a.pwm, self.s.turn_pwm)

    def test_full_circle_then_explore_then_search_again(self):
        for i in range(self.s.search_turns):
            self.assertIn(self.p.decide(det(), CLEAR, now=i).kind, (TURN_LEFT, TURN_RIGHT))
        a = self.p.decide(det(), CLEAR, now=10)
        self.assertEqual(self.p.state, EXPLORE)
        self.assertEqual(a.kind, FORWARD)
        for i in range(self.s.explore_steps - 1):
            self.p.decide(det(), CLEAR, now=11 + i)
        self.assertEqual(self.p.state, SEARCH)

    def test_turns_toward_off_center_target(self):
        a = self.p.decide(det(-0.3), CLEAR, now=0)
        self.assertEqual(self.p.state, APPROACH)
        self.assertEqual(a.kind, TURN_LEFT)
        small = a.duration_ms
        b = self.p.decide(det(0.9), CLEAR, now=1)
        self.assertEqual(b.kind, TURN_RIGHT)
        self.assertGreater(b.duration_ms, small)
        self.assertTrue(self.s.turn_min_ms <= small <= b.duration_ms <= self.s.turn_max_ms)

    def test_forward_when_centered_and_slower_when_large(self):
        a = self.p.decide(det(0.05, 0.01), CLEAR, now=0)
        self.assertEqual((a.kind, a.duration_ms), (FORWARD, self.s.forward_ms))
        b = self.p.decide(det(0.0, 0.05), CLEAR, now=1)
        self.assertEqual((b.kind, b.duration_ms), (FORWARD, self.s.slow_forward_ms))

    def test_periodic_distance_check(self):
        kinds = [self.p.decide(det(0.0), CLEAR, now=i).kind for i in range(5)]
        self.assertEqual(kinds[:3], [FORWARD] * 3)
        self.assertEqual(kinds[3], READ_DISTANCE)
        self.assertEqual(self.p.decide(det(0.0), CLEAR, distance_cm=80, now=6).kind, FORWARD)

    def test_arrival_needs_ultrasonic(self):
        self.p = Planner(NavSettings(arrival_mode="ultrasonic"), use_distance=True)
        close = det(0.02, 0.12)
        self.assertEqual(self.p.decide(close, CLEAR, now=0).kind, READ_DISTANCE)
        a = self.p.decide(close, CLEAR, distance_cm=45, now=1)       # big in image but still far
        self.assertEqual((a.kind, a.duration_ms), (FORWARD, self.s.slow_forward_ms))
        self.assertEqual(self.p.decide(close, CLEAR, now=2).kind, READ_DISTANCE)
        a = self.p.decide(close, CLEAR, distance_cm=16, now=3)
        self.assertEqual(a.kind, ARRIVED_ACTION)
        self.assertEqual(self.p.state, ARRIVED)
        self.assertEqual(self.p.decide(close, CLEAR, now=4).kind, STOP)

    def test_no_echo_is_not_arrival(self):
        self.p = Planner(NavSettings(arrival_mode="ultrasonic"), use_distance=True)
        close = det(0.0, 0.12)
        self.p.decide(close, CLEAR, now=0)
        a = self.p.decide(close, CLEAR, distance_cm=0, now=1)
        self.assertEqual(a.kind, FORWARD)
        self.assertNotEqual(self.p.state, ARRIVED)

    def test_vision_only_arrival_in_dry_run(self):
        p = Planner(self.s, use_distance=False)
        a = p.decide(det(0.0, 0.12), CLEAR, now=0)
        self.assertEqual(a.kind, ARRIVED_ACTION)
        self.assertIn("size only", a.reason)        # no stop line calibrated yet
        for i in range(5):                                            # never a distance request
            self.assertNotEqual(Planner(self.s, use_distance=False).decide(det(0.0), CLEAR, now=i).kind, READ_DISTANCE)

    def test_avoids_toward_more_open_side_and_resumes(self):
        blocked = free((0.2, 0.9, 0.1, 0.3, 0.2))    # center blocked, left side more open
        a = self.p.decide(det(0.0), blocked, now=0)
        self.assertEqual(self.p.state, AVOID)
        self.assertEqual(a.kind, TURN_LEFT)
        kinds = [self.p.decide(det(0.0), CLEAR, now=1 + i).kind for i in range(3)]
        self.assertEqual(kinds, [FORWARD, FORWARD, TURN_RIGHT])
        self.p.decide(det(0.0), CLEAR, now=5)
        self.assertEqual(self.p.state, APPROACH)

    def test_avoid_keeps_turning_then_backs_off(self):
        blocked = free((0.9, 0.2, 0.1, 0.2, 0.1))
        self.p.decide(det(0.0), blocked, now=0)                        # turn left (left is open)
        for i in range(self.s.avoid_max_retries):
            a = self.p.decide(det(0.0), blocked, now=1 + i)
            self.assertEqual(a.kind, TURN_LEFT)
        a = self.p.decide(det(0.0), blocked, now=10)
        self.assertEqual(a.kind, BACKWARD)
        self.assertEqual(self.p.state, SEARCH)

    def test_emergency_distance_backs_off(self):
        self.p.decide(det(0.0), CLEAR, now=0)
        a = self.p.decide(det(0.0, 0.01), CLEAR, distance_cm=7, now=1)
        self.assertEqual(a.kind, BACKWARD)
        self.assertEqual(self.p.state, AVOID)

    def test_lost_target_searches_toward_last_side(self):
        self.p.decide(det(-0.5), CLEAR, now=0)
        for i in range(self.s.lost_decisions - 1):
            self.assertEqual(self.p.decide(det(), CLEAR, now=1 + i).kind, STOP)
        a = self.p.decide(det(), CLEAR, now=5)
        self.assertEqual(self.p.state, SEARCH)
        self.assertEqual(a.kind, TURN_LEFT)

    def test_stale_camera_stops(self):
        a = self.p.decide(det(0.0), CLEAR, camera_ok=False, now=0)
        self.assertEqual(a.kind, STOP)

    def test_explore_gives_up_after_timeout(self):
        self.p.state = EXPLORE
        self.p.decide(det(), free((0.1,) * 5), now=0)
        a = self.p.decide(det(), free((0.1,) * 5), now=self.s.explore_timeout_s + 1)
        self.assertEqual(self.p.state, GIVE_UP)
        self.assertEqual(a.kind, STOP)

    def test_explore_backs_off_without_floor(self):
        self.p.state = EXPLORE
        a = self.p.decide(det(), free((0, 0, 0, 0, 0), visible=False), now=0)
        self.assertEqual(a.kind, BACKWARD)
        a = self.p.decide(det(), free((0, 0, 0, 0, 0), visible=False), now=0)
        self.assertIn(a.kind, (TURN_LEFT, TURN_RIGHT))      # then turns instead of backing up forever

    def test_explore_never_gives_up_when_timeout_is_zero(self):
        p = Planner(NavSettings(explore_timeout_s=0), use_distance=False)
        p.state = EXPLORE
        p.decide(det(), CLEAR, now=0)
        a = p.decide(det(), CLEAR, now=10_000)
        self.assertNotEqual(p.state, GIVE_UP)
        self.assertNotEqual(a.kind, STOP)

    def test_obstacle_behind_the_target_is_ignored(self):
        p = Planner(NavSettings(), use_distance=False)
        p.state = APPROACH
        behind = free((1, 0.3, 0.3, 0.3, 1))                # floor open up to just past the close ball
        self.assertNotEqual(p.decide(det(0.0, bottom=0.9), behind, now=0).state, AVOID)
        self.assertNotEqual(p.state, AVOID)
        p = Planner(NavSettings(), use_distance=False)
        p.state = APPROACH
        between = free((1, 0.05, 0.05, 0.05, 1))           # obstacle closer than the ball
        p.decide(det(0.0, bottom=0.9), between, now=0)
        self.assertEqual(p.state, AVOID)

    def test_longer_steps_when_far_and_floor_is_open(self):
        p = Planner(NavSettings(far_forward_ms=1000, mid_forward_ms=500), use_distance=False)
        p.state = APPROACH
        self.assertEqual(p.decide(det(0.0, bottom=0.5), CLEAR, now=0).duration_ms, 1000)     # far
        self.assertEqual(p.decide(det(0.0, bottom=0.65), CLEAR, now=0).duration_ms, 500)     # mid-range
        self.assertEqual(p.decide(det(0.0, bottom=0.8), CLEAR, now=0).duration_ms, p.s.forward_ms)  # close
        partly = free((1, 0.5, 0.5, 0.5, 1))                # floor only open part of the way: no long step
        self.assertEqual(p.decide(det(0.0, bottom=0.5), partly, now=0).duration_ms, p.s.forward_ms)
        self.assertEqual(p.decide(det(0.0, bottom=0.5), None, now=0).duration_ms, p.s.forward_ms)

    def test_wall_ahead_turns_toward_open_floor(self):
        self.p.state = EXPLORE
        a = self.p.wall_ahead(15, free((1, 1, 0.2, 0.2, 0.2)))
        self.assertEqual(a.kind, TURN_LEFT)
        self.assertEqual(a.duration_ms, self.s.avoid_turn_ms)

    def test_action_label_and_dict(self):
        a = self.p.decide(det(-0.5), CLEAR, now=0)
        self.assertTrue(a.label().startswith("TURN LEFT "))
        self.assertEqual(set(a.to_dict()), {"kind", "duration_ms", "pwm", "state", "reason"})


class CameraArrivalTests(unittest.TestCase):
    STOP = 0.85

    def planner(self, **kw):
        return Planner(NavSettings(**kw), use_distance=True, stop_line_y=self.STOP)

    def test_arrives_when_bottom_edge_reaches_line(self):
        p = self.planner()
        self.assertEqual(p.decide(det(0.0, bottom=0.6), CLEAR, now=0).kind, FORWARD)
        a = p.decide(det(0.02, bottom=0.86, dist=19), CLEAR, now=1)
        self.assertEqual(a.kind, ARRIVED_ACTION)
        self.assertIn("stop line", a.reason)
        self.assertEqual(p.state, ARRIVED)

    def test_off_center_at_line_turns_first(self):
        p = self.planner()
        a = p.decide(det(0.4, bottom=0.9), CLEAR, now=0)
        self.assertEqual(a.kind, TURN_RIGHT)

    def test_dropping_out_of_view_at_line_is_arrival(self):
        p = self.planner()
        p.decide(det(0.0, bottom=0.80), CLEAR, now=0)       # centered, inside the approach zone
        a = p.decide(det(), CLEAR, now=1)                    # gone out the bottom
        self.assertEqual(a.kind, ARRIVED_ACTION)

    def test_losing_far_target_is_not_arrival(self):
        p = self.planner()
        p.decide(det(0.0, bottom=0.5), CLEAR, now=0)
        self.assertEqual(p.decide(det(), CLEAR, now=1).kind, STOP)
        self.assertNotEqual(p.state, ARRIVED)

    def test_small_steps_in_approach_zone(self):
        p = self.planner()
        far = p.decide(det(0.0, bottom=0.5), CLEAR, now=0)
        near = p.decide(det(0.0, bottom=0.80), CLEAR, now=1)
        self.assertEqual(far.duration_ms, p.s.forward_ms)
        self.assertEqual(near.duration_ms, p.s.slow_forward_ms)

    def test_ultrasonic_ignored_near_target(self):
        p = self.planner()
        a = p.decide(det(0.0, bottom=0.80), CLEAR, distance_cm=6, now=0)   # the block / wall right behind it
        self.assertNotEqual(a.kind, BACKWARD)
        for i in range(6):                                                   # no periodic checks in the zone
            self.assertNotEqual(p.decide(det(0.0, bottom=0.79), CLEAR, now=1 + i).kind, READ_DISTANCE)

    def test_ultrasonic_backstop_far_from_target(self):
        p = self.planner()
        p.decide(det(0.0, bottom=0.5), CLEAR, now=0)
        self.assertEqual(p.decide(det(0.0, bottom=0.5), CLEAR, distance_cm=6, now=1).kind, BACKWARD)

    def test_never_reads_distance_for_arrival_in_camera_mode(self):
        p = self.planner()
        kinds = [p.decide(det(0.0, area_ratio=0.2, bottom=0.6), CLEAR, now=i).kind for i in range(3)]
        self.assertNotIn(READ_DISTANCE, kinds)

    def test_give_up_recovers_when_target_seen(self):
        p = self.planner()
        p.state = GIVE_UP
        self.assertEqual(p.decide(det(), CLEAR, now=0).kind, STOP)
        p.decide(det(0.0, bottom=0.5), CLEAR, now=1)
        self.assertEqual(p.state, APPROACH)

    def test_explore_step_is_labeled_explore(self):
        p = Planner(NavSettings(explore_steps=1), use_distance=False)
        p.state = EXPLORE
        a = p.decide(det(), CLEAR, now=0)
        self.assertEqual((a.kind, a.state), (FORWARD, EXPLORE))
        self.assertEqual(p.state, SEARCH)


class StepTableTests(unittest.TestCase):
    STEPS = {"forward": {"200": 3.0, "500": 9.0, "1000": 20.0}, "turn": {"200": 12.0, "500": 35.0}}

    def planner(self):
        return Planner(NavSettings(steps=self.STEPS, camera_hfov_deg=60), use_distance=False, stop_line_y=0.85)

    def test_forward_step_sized_by_remaining_distance(self):
        p = self.planner()
        self.assertEqual(p.decide(det(0.0, bottom=0.5, dist=60), CLEAR, now=0).duration_ms, 1000)  # 40 cm to go
        self.assertEqual(p.decide(det(0.0, bottom=0.6, dist=34), CLEAR, now=1).duration_ms, 500)   # 14 cm
        self.assertEqual(p.decide(det(0.0, bottom=0.65, dist=25), CLEAR, now=2).duration_ms, 200)  # 5 cm
        self.assertEqual(p.decide(det(0.0, bottom=0.80, dist=22), CLEAR, now=3).duration_ms, 200)  # zone

    def test_turn_step_sized_by_angle(self):
        p = Planner(NavSettings(steps=self.STEPS, camera_hfov_deg=80), use_distance=False, stop_line_y=0.85)
        self.assertEqual(p.decide(det(-0.9, bottom=0.5), CLEAR, now=0).duration_ms, 500)   # 36 deg: 35 deg step fits
        self.assertEqual(p.decide(det(-0.8, bottom=0.5), CLEAR, now=1).duration_ms, 200)   # 32 deg: 35 would overshoot
        self.assertEqual(p.decide(det(0.15, bottom=0.5), CLEAR, now=2).duration_ms, 200)   # 6 deg: smallest step

    def test_ignores_invalid_table_entries(self):
        p = Planner(NavSettings(steps={"forward": {"200": 0, "x": 3}, "turn": {}}))
        self.assertEqual(p.forward_table, [])


if __name__ == "__main__":
    unittest.main()
