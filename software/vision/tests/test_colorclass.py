"""Classifier + white balance tests, using colors measured on the real arena (2026-11-07)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from bullseye_vision.colorclass import UNKNOWN, ColorModel, WhiteBalance, format_confusion, load_samples, save_samples
from bullseye_vision.detector import ColorDetector
from support import clean_config

# OpenCV HSV values measured in the arena snapshots.
MEASURED_HSV = {
    "red": (177, 80, 125),        # far red cube: pale
    "green": (66, 60, 110),
    "blue": (106, 116, 81),
    "yellow": (28, 40, 183),      # pale yellow cube
    "floor": (60, 14, 180),
    "cardboard": (21, 50, 116),   # close to yellow in hue
    "obstacle": (12, 98, 150),    # orange crumpled paper: next to red
}


def hsv_to_bgr(h, s, v):
    return cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0].astype(np.int16)


def make_samples(n=1500, sd=4.0, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for name, hsv in MEASURED_HSV.items():
        base = hsv_to_bgr(*hsv)
        out[name] = np.clip(base + rng.normal(0, sd, (n, 3)), 0, 255).astype(np.uint8)
    return out


class ColorModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = make_samples()
        cls.model = ColorModel.fit(cls.samples)

    def test_confusing_pairs_separate(self):
        conf = self.model.confusion(self.samples)
        for name in MEASURED_HSV:
            # The color gate may move up to 20% of a mineral's dullest sample pixels to the background.
            floor = 0.78 if name in ("red", "green", "blue", "yellow") else 0.9
            self.assertGreater(conf[name][name], floor, f"{name}: {conf[name]}")
        self.assertLess(conf["red"]["obstacle"], 0.02)
        self.assertLess(conf["obstacle"]["red"], 0.02)
        self.assertLess(conf["yellow"]["cardboard"], 0.02)
        self.assertLess(conf["cardboard"]["yellow"], 0.02)
        self.assertIn("unknown", format_confusion(conf))

    def test_lookup_table_matches_direct_classification(self):
        rng = np.random.default_rng(1)
        px = rng.integers(0, 256, (20000, 3), dtype=np.uint8)
        direct = self.model.classify_bgr(px)
        lut = self.model.labels(px.reshape(-1, 1, 3)).ravel()
        self.assertGreater(np.mean(direct == lut), 0.95)

    def test_unknown_colors_rejected(self):
        purple = hsv_to_bgr(150, 220, 200).astype(np.uint8).reshape(1, 1, 3)
        self.assertEqual(self.model.labels(purple)[0, 0], UNKNOWN)

    def test_reference_floor_recorded(self):
        self.assertIsNotNone(self.model.reference_floor)
        np.testing.assert_allclose(self.model.reference_floor, hsv_to_bgr(*MEASURED_HSV["floor"]), atol=3)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.model.save(Path(tmp) / "m.json")
            again = ColorModel.load(path)
            self.assertEqual(again.classes, self.model.classes)
            self.assertGreater(np.mean(again.lut == self.model.lut), 0.999)

    def test_samples_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save_samples({"red": self.samples["red"][:10]}, Path(tmp) / "s.npz")
            loaded = load_samples(path)
            np.testing.assert_array_equal(loaded["red"], self.samples["red"][:10])
            self.assertEqual(load_samples(Path(tmp) / "missing.npz"), {})

    def test_needs_two_classes(self):
        with self.assertRaises(ValueError):
            ColorModel.fit({"red": self.samples["red"]})


def arena_frame(cast=(1.0, 1.0, 1.0)):
    img = np.empty((600, 800, 3), np.uint8)
    img[:] = hsv_to_bgr(*MEASURED_HSV["floor"]).astype(np.uint8)
    img[:200] = hsv_to_bgr(*MEASURED_HSV["cardboard"]).astype(np.uint8)
    cv2.rectangle(img, (180, 330), (215, 362), hsv_to_bgr(*MEASURED_HSV["red"]).tolist(), -1)        # pale far cube
    cv2.rectangle(img, (520, 300), (640, 380), hsv_to_bgr(*MEASURED_HSV["obstacle"]).tolist(), -1)   # orange paper
    out = np.clip(img.astype(np.float32) * np.float32(cast), 0, 255).astype(np.uint8)
    return out


class ColorGateTests(unittest.TestCase):
    def test_tinted_floor_is_not_a_mineral(self):
        """Floodlit floor with a warm or bluish tint must never count as red or blue, while real balls still do."""
        samples = make_samples()
        model = ColorModel.fit(samples)
        self.assertEqual(set(model.gates), {"red", "green", "blue", "yellow"})
        warm_floor = np.full((20, 20, 3), hsv_to_bgr(176, 45, 200), np.uint8)    # floor, slightly pink
        blue_floor = np.full((20, 20, 3), hsv_to_bgr(100, 30, 190), np.uint8)    # floor, slightly blue
        for patch, mineral in ((warm_floor, "red"), (blue_floor, "blue")):
            self.assertFalse((model.labels(patch) == model.index(mineral)).any(), mineral)
        for name in ("red", "green", "blue", "yellow"):
            self.assertGreater(model.confusion(samples)[name][name], 0.8, name)
        again = ColorModel.from_dict(json.loads(json.dumps(model.to_dict())))
        self.assertEqual(again.gates.keys(), model.gates.keys())


class WhiteBalanceTests(unittest.TestCase):
    def test_corrects_color_cast(self):
        model = ColorModel.fit(make_samples())
        wb = WhiteBalance(model.reference_floor)
        cast = arena_frame(cast=(0.8, 1.0, 1.2))              # warm floodlight-like cast (BGR gains)
        wb.update(cast)
        fixed = wb.apply(cast)
        floor_after = fixed[550, 400].astype(float)
        np.testing.assert_allclose(floor_after, model.reference_floor, atol=6)

    def test_keeps_gains_without_floor(self):
        wb = WhiteBalance((180, 180, 180))
        wb.update(np.full((300, 400, 3), (40, 80, 200), np.uint8))   # no floor-like pixels
        np.testing.assert_allclose(wb.gains, 1.0)


class DetectorIntegrationTests(unittest.TestCase):
    def test_classifier_finds_pale_cube_hsv_misses_it(self):
        cfg = clean_config()
        frame = arena_frame()
        hsv_det = ColorDetector.from_config(cfg, mode="hsv")
        self.assertFalse(hsv_det.detect(frame, "red").detected)          # S 80 < HSV red s_min 120
        det = ColorDetector.from_config(cfg, mode="hsv")
        det.settings.min_area_px = 200
        det.set_model(ColorModel.fit(make_samples()), cfg["classifier"])
        result = det.detect(frame, "red")
        self.assertTrue(result.detected)
        self.assertAlmostEqual(result.center_x, 197, delta=4)            # the cube, not the orange paper
        self.assertEqual(det.mode, "classifier")

    def test_classifier_under_color_cast(self):
        cfg = clean_config()
        det = ColorDetector.from_config(cfg, mode="hsv")
        det.settings.min_area_px = 200
        det.set_model(ColorModel.fit(make_samples()), cfg["classifier"])
        result = det.detect(arena_frame(cast=(0.85, 1.0, 1.15)), "red")
        self.assertTrue(result.detected)
        self.assertAlmostEqual(result.center_x, 197, delta=4)

    def test_floor_class_mask(self):
        cfg = clean_config()
        det = ColorDetector.from_config(cfg, mode="hsv")
        det.set_model(ColorModel.fit(make_samples()), cfg["classifier"])
        prepared = det.prepare(arena_frame())
        floor = det.class_mask(prepared, "floor")
        self.assertGreater(floor[400:].mean() / 255, 0.8)
        self.assertLess(floor[:150].mean() / 255, 0.05)
        self.assertIsNone(det.class_mask(prepared, "nonexistent"))


if __name__ == "__main__":
    unittest.main()
