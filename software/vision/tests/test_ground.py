"""Ground-plane model, calibration script, and detector ground filters."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

import ground_calib
from config import DEFAULT_CONFIG_PATH, load_config
from detector import ColorDetector
from ground import GroundModel

HORIZON, A, K = 0.35, 6.0, 2.5           # synthetic pinhole camera: y = horizon + A/d, width = K/d


def row(d):
    return HORIZON + A / d


SAMPLES = [(row(d), d, K / d) for d in (10, 15, 20, 30)]


class GroundModelTests(unittest.TestCase):
    def setUp(self):
        self.g = GroundModel.fit(SAMPLES)

    def test_fit(self):
        self.assertTrue(self.g.calibrated)
        self.assertAlmostEqual(self.g.horizon_y, HORIZON, places=3)
        self.assertAlmostEqual(self.g.width_k, K, places=3)

    def test_distance_inside_and_outside_range(self):
        self.assertAlmostEqual(self.g.distance_cm(row(20)), 20, places=3)
        self.assertAlmostEqual(self.g.distance_cm(row(17)), 17, delta=0.6)    # interpolated
        self.assertAlmostEqual(self.g.distance_cm(row(60)), 60, delta=1)      # pinhole extrapolation
        self.assertIsNone(self.g.distance_cm(HORIZON - 0.05))                 # above the horizon

    def test_row_for_distance(self):
        self.assertAlmostEqual(self.g.row_for_distance(15), row(15), places=3)
        self.assertAlmostEqual(self.g.row_for_distance(50), row(50), places=2)

    def test_plausible(self):
        self.assertTrue(self.g.plausible(row(20), K / 20))
        self.assertFalse(self.g.plausible(HORIZON - 0.01, 0.01))     # above the horizon: off the floor
        self.assertFalse(self.g.plausible(row(20), 4 * K / 20))      # far too wide for a block there
        self.assertFalse(self.g.plausible(row(20), K / 20 / 4))      # far too narrow
        self.assertTrue(GroundModel().plausible(0.1, 0.5))           # uncalibrated: no filtering

    def test_wrong_order_rejected(self):
        bad = [(0.9, 30, None), (0.6, 10, None)]
        with self.assertRaises(ValueError):
            GroundModel.fit(bad)

    def test_dict_roundtrip(self):
        again = GroundModel.from_dict(self.g.to_dict())
        self.assertAlmostEqual(again.distance_cm(row(20)), 20, places=1)


def write_result(folder: Path, name: str, bottom: float, width: float, err=0.0, detected=True):
    w, h = 800, 600
    bw = int(width * w)
    bh = 30
    y = int(bottom * h) - bh
    x = int(400 + err * 400) - bw // 2
    det = {"detected": detected, "bbox": [x, y, bw, bh] if detected else None, "frame_width": w,
           "frame_height": h, "horizontal_error": err}
    path = folder / name
    path.write_text(json.dumps({"detection": det}))
    return str(path)


class CalibScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg_path = self.tmp / "config.json"
        shutil.copy(DEFAULT_CONFIG_PATH, self.cfg_path)

    def test_stopline_writes_ground(self):
        files = [write_result(self.tmp, f"{d}.json", row(d), K / d) for d in (10, 15, 20, 30)]
        rc = ground_calib.main(["--config", str(self.cfg_path), "stopline", "--distances", "10,15,20,30", *files])
        self.assertEqual(rc, 0)
        cfg = load_config(self.cfg_path)
        self.assertEqual(len(cfg["ground"]["points"]), 4)
        arrive = cfg["navigation"]["arrive_distance_cm"]
        self.assertAlmostEqual(cfg["ground"]["stop_line_y"], row(arrive), delta=0.01)
        self.assertAlmostEqual(cfg["ground"]["horizon_y"], HORIZON, delta=0.01)

    def test_stopline_rejects_missing_detection(self):
        files = [write_result(self.tmp, "a.json", row(10), 0.2), write_result(self.tmp, "b.json", 0, 0, detected=False)]
        rc = ground_calib.main(["--config", str(self.cfg_path), "stopline", "--distances", "10,20", *files])
        self.assertEqual(rc, 2)

    def test_fov(self):
        center = write_result(self.tmp, "c.json", 0.6, 0.1, err=0.0)
        side = write_result(self.tmp, "s.json", 0.6, 0.1, err=0.5)   # 10 cm at 30 cm = 18.4 deg -> half frame
        rc = ground_calib.main(["--config", str(self.cfg_path), "fov", "--distance-cm", "30", "--offset-cm", "10", center, side])
        self.assertEqual(rc, 0)
        self.assertAlmostEqual(load_config(self.cfg_path)["navigation"]["camera_hfov_deg"], 73.7, delta=0.2)


class DetectorGroundTests(unittest.TestCase):
    def test_ground_filters_and_distance(self):
        cfg = load_config()
        det = ColorDetector.from_config(cfg, mode="hsv")
        det.ground = GroundModel.fit(SAMPLES)
        img = np.full((600, 800, 3), 110, np.uint8)
        d = 20
        bottom_px, width_px = int(row(d) * 600), int(K / d * 800)
        cv2.rectangle(img, (500, bottom_px - width_px), (500 + width_px, bottom_px), (0, 0, 255), -1)  # real block
        cv2.rectangle(img, (100, 60), (180, 140), (0, 0, 255), -1)          # red thing above the horizon (on a wall)
        result = det.detect(img, "red")
        self.assertEqual(result.candidates, 1)
        self.assertAlmostEqual(result.distance_cm_est, d, delta=1.0)
        self.assertAlmostEqual(result.bottom_y, row(d), delta=0.01)


if __name__ == "__main__":
    unittest.main()
