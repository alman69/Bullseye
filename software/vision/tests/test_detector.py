"""Synthetic-image tests for detector.py and config.py (no camera needed)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from config import DEFAULTS, load_config, save_config
from detector import CENTER, LEFT, NONE, RIGHT, ColorDetector, HsvRange, direction_for

W, H = 640, 480
BGR = {"red": (0, 0, 255), "green": (0, 200, 0), "blue": (255, 0, 0)}


def background(gray=110):
    """Low-saturation 'arena' background with mild noise."""
    rng = np.random.default_rng(0)
    img = np.full((H, W, 3), gray, np.uint8)
    noise = rng.integers(-12, 13, size=img.shape, dtype=np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def hsv_color(h, s=220, v=220):
    return tuple(int(c) for c in cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0])


def blob(img, center, radius, bgr):
    cv2.circle(img, center, radius, bgr, -1)
    return img


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.det = ColorDetector.from_config(load_config(Path(tempfile.gettempdir()) / "missing.json"))

    def test_each_color_detected_with_correct_center(self):
        for color, bgr in BGR.items():
            with self.subTest(color=color):
                img = blob(background(), (400, 260), 40, bgr)
                d = self.det.detect(img, color)
                self.assertTrue(d.detected)
                self.assertAlmostEqual(d.center_x, 400, delta=2)
                self.assertAlmostEqual(d.center_y, 260, delta=2)
                self.assertAlmostEqual(d.area, np.pi * 40 ** 2, delta=0.1 * np.pi * 40 ** 2)
                self.assertEqual((d.frame_width, d.frame_height), (W, H))

    def test_selects_requested_color_among_others(self):
        img = background()
        blob(img, (120, 240), 45, BGR["red"])
        blob(img, (320, 240), 45, BGR["green"])
        blob(img, (520, 240), 45, BGR["blue"])
        expected = {"red": (120, LEFT), "green": (320, CENTER), "blue": (520, RIGHT)}
        for color, (x, direction) in expected.items():
            with self.subTest(color=color):
                d = self.det.detect(img, color)
                self.assertTrue(d.detected)
                self.assertEqual(d.candidates, 1)
                self.assertAlmostEqual(d.center_x, x, delta=2)
                self.assertEqual(d.direction, direction)

    def test_red_hue_wraps_around(self):
        self.assertTrue(self.det.ranges["red"].wraps)
        for hue in (175, 178, 2, 7):
            with self.subTest(hue=hue):
                img = blob(background(), (320, 240), 40, hsv_color(hue))
                self.assertTrue(self.det.detect(img, "red").detected)
        for hue in (25, 60, 90, 150):  # orange/yellow, green, cyan, magenta
            with self.subTest(hue=hue):
                img = blob(background(), (320, 240), 40, hsv_color(hue))
                self.assertFalse(self.det.detect(img, "red").detected)

    def test_largest_candidate_wins(self):
        img = background()
        blob(img, (150, 240), 25, BGR["red"])
        blob(img, (480, 240), 50, BGR["red"])
        d = self.det.detect(img, "red")
        self.assertEqual(d.candidates, 2)
        self.assertAlmostEqual(d.center_x, 480, delta=2)
        self.assertEqual(len(d.all_candidates), 2)

    def test_small_noise_rejected(self):
        img = blob(background(), (320, 240), 8, BGR["blue"])  # ~200 px < min_area_px
        d = self.det.detect(img, "blue")
        self.assertFalse(d.detected)

    def test_salt_noise_removed_by_morphology(self):
        img = background()
        rng = np.random.default_rng(1)
        ys, xs = rng.integers(0, H, 3000), rng.integers(0, W, 3000)
        img[ys, xs] = BGR["red"]
        self.assertFalse(self.det.detect(img, "red").detected)

    def test_huge_region_rejected(self):
        img = np.full((H, W, 3), BGR["green"], np.uint8)  # e.g. a green wall filling the view
        self.assertFalse(self.det.detect(img, "green").detected)

    def test_thin_diagonal_streak_rejected(self):
        img = background()
        cv2.line(img, (20, 20), (620, 460), BGR["blue"], 14)
        d = self.det.detect(img, "blue")
        self.assertFalse(d.detected)

    def test_empty_frame_result(self):
        d = self.det.detect(background(), "red", frame_id=7, timestamp=123.5)
        self.assertFalse(d.detected)
        self.assertEqual(d.direction, NONE)
        out = d.to_dict()
        json.dumps(out)
        for key in ("center_x", "center_y", "horizontal_error", "area", "area_ratio", "bbox"):
            self.assertIsNone(out[key])
        self.assertEqual(out["frame_id"], 7)
        self.assertNotIn("all_candidates", out)

    def test_to_dict_fields(self):
        img = blob(background(), (160, 240), 40, BGR["red"])
        out = self.det.detect(img, "red").to_dict()
        json.dumps(out)
        expected = {"target_color", "detected", "center_x", "center_y", "frame_width", "frame_height",
                    "horizontal_error", "area", "area_ratio", "bbox", "direction", "candidates",
                    "frame_id", "timestamp", "processing_ms"}
        self.assertEqual(set(out), expected)
        self.assertAlmostEqual(out["horizontal_error"], -0.5, delta=0.01)
        self.assertEqual(out["direction"], LEFT)

    def test_horizontal_error_and_direction(self):
        self.assertEqual(direction_for(0.0, 0.12), CENTER)
        self.assertEqual(direction_for(0.12, 0.12), CENTER)
        self.assertEqual(direction_for(-0.13, 0.12), LEFT)
        self.assertEqual(direction_for(0.5, 0.12), RIGHT)
        img = blob(background(), (W - 41, 240), 40, BGR["green"])
        d = self.det.detect(img, "green")
        self.assertGreater(d.horizontal_error, 0.85)
        self.assertLessEqual(d.horizontal_error, 1.0)

    def test_read_only_frame_accepted(self):
        img = blob(background(), (320, 240), 40, BGR["red"])
        img.flags.writeable = False
        self.assertTrue(self.det.detect(img, "red").detected)

    def test_unknown_color_raises(self):
        with self.assertRaises(ValueError):
            self.det.detect(background(), "purple")

    def test_set_range_changes_detection(self):
        img = blob(background(), (320, 240), 40, hsv_color(150))  # magenta
        self.assertFalse(self.det.detect(img, "red").detected)
        self.det.set_range("red", HsvRange((140, 10), (120, 255), (70, 255)))
        self.assertTrue(self.det.detect(img, "red").detected)


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"colors": {"red": {"h": [160, 5], "s": [1, 2], "v": [3, 4]}}}))
            cfg = load_config(path)
            self.assertEqual(cfg["colors"]["red"]["h"], [160, 5])
            self.assertEqual(cfg["colors"]["green"], DEFAULTS["colors"]["green"])  # filled from defaults
            self.assertEqual(cfg["camera"]["stream_url"], DEFAULTS["camera"]["stream_url"])
            cfg["detector"]["min_area_px"] = 999
            save_config(cfg, path)
            self.assertEqual(load_config(path), cfg)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["config.json"])  # no temp files left

    def test_repo_config_matches_defaults_keys(self):
        cfg = load_config()
        self.assertEqual(set(cfg), set(DEFAULTS))
        for color in ("red", "green", "blue"):
            HsvRange.from_dict(cfg["colors"][color])


if __name__ == "__main__":
    unittest.main()
