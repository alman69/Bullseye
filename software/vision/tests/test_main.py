"""Window-free tests for main.py drawing and snapshot helpers."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

import main
from bullseye_vision.detector import ColorDetector
from support import clean_config


class MainHelperTests(unittest.TestCase):
    def setUp(self):
        self.det = ColorDetector.from_config(clean_config())
        self.frame = np.full((600, 800, 3), 110, np.uint8)
        cv2.circle(self.frame, (600, 300), 40, (0, 0, 255), -1)
        cv2.circle(self.frame, (200, 300), 25, (0, 0, 255), -1)
        self.frame.flags.writeable = False
        self.info = {"stream_fps": 14.2, "proc_fps": 14.1, "age_ms": 12.0, "state": "reconnecting",
                     "error": "URLError: timed out", "lost_for": 1.5, "source": "test",
                     "probe": ((10, 10), (0, 0, 110))}

    def test_overlay_detected_and_lost(self):
        for color in ("red", "blue"):
            with self.subTest(color=color):
                d = self.det.detect(self.frame, color)
                out = main.draw_overlay(self.frame, d, self.info, 0.12)
                self.assertEqual(out.shape, self.frame.shape)
                self.assertFalse(np.array_equal(out, self.frame))

    def test_snapshot_files(self):
        d = self.det.detect(self.frame, "red")
        annotated = main.draw_overlay(self.frame, d, self.info, 0.12)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(main, "CAPTURE_DIR", Path(tmp)):
            stem = main.save_snapshot(self.frame, annotated, d, self.info, self.det)
            names = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(len(names), 3)
            record = json.loads(Path(f"{stem}_result.json").read_text())
            self.assertTrue(record["detection"]["detected"])
            self.assertEqual(record["detection"]["candidates"], 2)
            self.assertIn("h", record["hsv_range"])


if __name__ == "__main__":
    unittest.main()
