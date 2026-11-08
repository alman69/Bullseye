"""Synthetic arena images for freespace.py: white floor, cardboard walls, painted blocks."""
from __future__ import annotations

import unittest

import cv2
import numpy as np

from bullseye_vision.detector import ColorDetector
from bullseye_vision.freespace import FloorSettings, analyze_floor
from support import clean_config

H, W = 600, 800
FLOOR = (235, 235, 235)        # matte white floor (BGR)
SHADOW = (165, 165, 165)       # floor in shadow
CARDBOARD = (90, 136, 176)     # brown cardboard, HSV about (16, 125, 176)
YELLOW = (0, 220, 230)
RED = (30, 30, 220)


def arena(wall_bottom_y: int = 280) -> np.ndarray:
    """Wall (cardboard) from the top down to wall_bottom_y, white floor below it."""
    rng = np.random.default_rng(0)
    img = np.empty((H, W, 3), np.uint8)
    img[:] = FLOOR
    img[:wall_bottom_y] = CARDBOARD
    noise = rng.integers(-8, 9, size=img.shape, dtype=np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


class FreeSpaceTests(unittest.TestCase):
    def setUp(self):
        self.s = FloorSettings()

    def test_open_floor(self):
        free = analyze_floor(arena(wall_bottom_y=200), self.s)   # wall entirely above the analyzed half
        self.assertEqual(len(free.heights), 5)
        self.assertTrue(all(h > 0.95 for h in free.heights))
        self.assertFalse(free.center_blocked)
        self.assertTrue(free.floor_visible)
        self.assertEqual(free.best_strip, 2)                     # tie -> closest to center
        self.assertEqual(free.corridor, [1, 2, 3])

    def test_far_wall_is_not_blocking(self):
        free = analyze_floor(arena(wall_bottom_y=420), self.s)   # floor fills 180 of 300 analyzed rows
        for h in free.heights:
            self.assertAlmostEqual(h, 0.6, delta=0.05)
        self.assertFalse(free.center_blocked)

    def test_close_wall_blocks_everything(self):
        free = analyze_floor(arena(wall_bottom_y=560), self.s)
        self.assertTrue(all(free.blocked))
        self.assertTrue(free.center_blocked)

    def test_obstacle_in_center(self):
        img = arena(200)
        cv2.rectangle(img, (340, 470), (460, 560), YELLOW, -1)
        free = analyze_floor(img, self.s)
        self.assertTrue(free.blocked[2])
        self.assertFalse(free.blocked[0])
        self.assertFalse(free.blocked[4])
        self.assertTrue(free.center_blocked)
        self.assertNotEqual(free.best_strip, 2)

    def test_obstacle_on_one_side_prefers_other_side(self):
        img = arena(200)
        cv2.rectangle(img, (0, 450), (330, 600), CARDBOARD, -1)   # something big on the left
        free = analyze_floor(img, self.s)
        left, right = free.side_space()
        self.assertLess(left, right)
        self.assertTrue(free.blocked[0] and free.blocked[1])
        self.assertFalse(free.blocked[3] or free.blocked[4])
        self.assertGreaterEqual(free.bearings[free.best_strip], 0)   # center or right, never left

    def test_shadow_still_counts_as_floor(self):
        img = arena(200)
        cv2.rectangle(img, (250, 380), (550, 600), SHADOW, -1)
        free = analyze_floor(img, self.s)
        self.assertFalse(free.center_blocked)

    def test_target_is_excluded_from_obstacles(self):
        img = arena(200)
        cv2.rectangle(img, (350, 450), (450, 580), RED, -1)
        self.assertTrue(analyze_floor(img, self.s).center_blocked)
        detector = ColorDetector.from_config(clean_config())
        target_mask = detector.mask(img, "red")
        self.assertFalse(analyze_floor(img, self.s, exclude_mask=target_mask).center_blocked)

    def test_no_floor_visible(self):
        img = np.full((H, W, 3), CARDBOARD, np.uint8)
        free = analyze_floor(img, self.s)
        self.assertFalse(free.floor_visible)
        self.assertTrue(all(h == 0 for h in free.heights))

    def test_to_dict_is_json_ready(self):
        import json
        out = analyze_floor(arena(), self.s).to_dict()
        json.dumps(out)
        self.assertNotIn("mask", out)
        self.assertEqual(len(out["heights"]), 5)


if __name__ == "__main__":
    unittest.main()
