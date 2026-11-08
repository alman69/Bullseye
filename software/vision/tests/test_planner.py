"""State-machine tests for planner.py using hand-built detections and floor results."""
from __future__ import annotations

import unittest

from detector import Detection
from freespace import FreeSpace
from planner import (APPROACH, ARRIVED, ARRIVED_ACTION, AVOID, BACKWARD, EXPLORE, FORWARD, GIVE_UP,
                     READ_DISTANCE, SEARCH, STOP, TURN_LEFT, TURN_RIGHT, NavSettings, Planner)

BEARINGS = [-0.8, -0.4, 0.0, 0.4, 0.8]


def det(err=None, area_ratio=0.01):
    if err is None:
        return Detection("red", False, 800, 600)
    return Detection("red", True, 800, 600, center_x=int(400 + err * 400), center_y=300,
                     horizontal_error=err, area=area_ratio * 480000, area_ratio=area_ratio)


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
        close = det(0.0, 0.12)
        self.p.decide(close, CLEAR, now=0)
        a = self.p.decide(close, CLEAR, distance_cm=0, now=1)
        self.assertEqual(a.kind, FORWARD)
        self.assertNotEqual(self.p.state, ARRIVED)

    def test_vision_only_arrival_in_dry_run(self):
        p = Planner(self.s, use_distance=False)
        a = p.decide(det(0.0, 0.12), CLEAR, now=0)
        self.assertEqual(a.kind, ARRIVED_ACTION)
        self.assertIn("vision only", a.reason)
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

    def test_action_label_and_dict(self):
        a = self.p.decide(det(-0.5), CLEAR, now=0)
        self.assertTrue(a.label().startswith("TURN LEFT "))
        self.assertEqual(set(a.to_dict()), {"kind", "duration_ms", "pwm", "state", "reason"})


if __name__ == "__main__":
    unittest.main()
