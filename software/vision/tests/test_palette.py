"""Arena palette regression scenes; no hardware or saved calibration required."""
import copy
import unittest

import cv2
import numpy as np

from bullseye_vision.config import DEFAULTS
from bullseye_vision.detector import ColorDetector
from bullseye_vision.palette import ArenaPalette, CLASSES, fit_patch


def bgr(h, s, v):
    return cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0]


class PaletteTests(unittest.TestCase):
    def test_all_materials_at_once_and_brightness_variation(self):
        palette = ArenaPalette()
        swatches = {"red": (0, 230, 210), "green": (60, 200, 180), "blue": (115, 220, 190),
                    "yellow": (29, 230, 230), "cardboard": (17, 115, 160),
                    "home": (0, 10, 25), "floor": (20, 15, 230)}
        for name, hsv in swatches.items():
            for brightness in (0.8, 1.0, 1.1):
                image = np.tile(bgr(hsv[0], hsv[1], min(255, round(hsv[2] * brightness))), (20, 20, 1))
                self.assertTrue(np.all(palette.labels(image) == palette.index(name)), (name, brightness))

    def test_white_and_green_tinted_floor_preserve_green_ore(self):
        white = np.tile(bgr(0, 8, 230), (100, 1))
        tinted = np.tile(bgr(60, 70, 210), (100, 1))
        palette = ArenaPalette({"floor": [fit_patch(white, "floor"), fit_patch(tinted, "floor")]})
        image = np.array([[white[0], tinted[0], bgr(60, 200, 180)]])
        self.assertEqual(palette.labels(image).tolist(), [[6, 6, 1]])

    def test_red_wrap_and_unknown_magenta(self):
        image = np.array([[bgr(179, 230, 210), bgr(1, 230, 210), bgr(150, 240, 220)]])
        self.assertEqual(ArenaPalette().labels(image).tolist(), [[0, 0, -1]])
        pixels = np.tile(image[0, :2], (100, 1))
        profile = fit_patch(pixels, "red")
        self.assertLess(profile["scale"][0], 15)
        self.assertEqual(ArenaPalette({"red": [profile]}).labels(image).tolist(), [[0, 0, -1]])

    def test_background_samples_reject_washed_out_false_colors(self):
        wall = np.tile(bgr(24, 65, 200), (200, 1))
        floor = np.tile(bgr(105, 45, 220), (200, 1))
        palette = ArenaPalette({"cardboard": [fit_patch(wall, "cardboard")],
                                "floor": [fit_patch(floor, "floor")]})
        image = np.array([[wall[0], floor[0], bgr(28, 230, 225), bgr(115, 220, 185)]])
        self.assertEqual(palette.labels(image).tolist(), [[4, 6, 3, 2]])

    def test_calibration_recovers_muted_object_and_leaves_others(self):
        muted = np.tile(bgr(3, 90, 180), (200, 1))
        self.assertEqual(ArenaPalette().labels(muted.reshape(1, -1, 3))[0, 0], -1)
        palette = ArenaPalette({"red": [fit_patch(muted, "red")]})
        self.assertEqual(palette.labels(muted.reshape(1, -1, 3))[0, 0], 0)
        self.assertEqual(palette.labels(np.array([[bgr(28, 235, 235)]]))[0, 0], 3)

    def test_rejects_mixed_gray_and_bright_home_samples(self):
        for pixels, name in ((np.full((100, 3), 180, np.uint8), "red"),
                             (np.full((100, 3), 180, np.uint8), "home"),
                             (np.tile([bgr(0, 230, 200), bgr(60, 230, 200)], (100, 1)), "red")):
            with self.assertRaises(ValueError):
                fit_patch(pixels, name)

    def test_polygon_masks_all_materials_but_preserves_gap(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["rover_mask"] = {"polygons": [[[0, 0.5], [0.35, 0.5], [0.35, 1], [0, 1]],
                                         [[0.65, 0.5], [1, 0.5], [1, 1], [0.65, 1]]]}
        detector = ColorDetector.from_config(cfg)
        for name, pixel in (("blue", bgr(115, 230, 200)), ("home", bgr(0, 0, 20))):
            image = np.tile(pixel, (200, 300, 1))
            prepared = detector.prepare(image)
            mask = detector.mask(image, name, prepared=prepared)
            self.assertEqual(mask[160, 30], 0)
            self.assertEqual(mask[160, 270], 0)
            self.assertEqual(mask[160, 150], 255)

    def test_wall_and_home_do_not_use_rock_size_filters(self):
        cfg = copy.deepcopy(DEFAULTS)
        detector = ColorDetector.from_config(cfg)
        image = np.tile(bgr(17, 115, 160), (200, 300, 1))
        self.assertTrue(detector.detect(image, "cardboard").detected)
        self.assertFalse(detector.detect(image, "yellow").detected)

    def test_multiple_floor_and_green_variations_accumulate(self):
        floor = [bgr(0, 0, 240), bgr(100, 20, 150)]
        green = [bgr(60, 200, 160), bgr(85, 200, 160), bgr(60, 200, 30)]
        profiles = {name: [fit_patch(np.tile(px, (100, 1)), name) for px in pixels]
                    for name, pixels in (("floor", floor), ("green", green))}
        palette = ArenaPalette(profiles)
        self.assertEqual(palette.labels(np.array([floor + green])).tolist(), [[6, 6, 1, 1, 1]])

    def test_home_stays_near_black_even_with_old_broad_calibration(self):
        old = {"center": [0, 0, 50], "scale": [90, 130, 100]}
        for palette in (ArenaPalette(), ArenaPalette({"home": [old]})):
            for value in (45, 60, 85, 100):
                self.assertNotEqual(palette.labels(np.full((1, 1, 3), value, np.uint8))[0, 0], palette.index("home"))
            self.assertEqual(palette.labels(np.full((1, 1, 3), 20, np.uint8))[0, 0], palette.index("home"))
        profile = fit_patch(np.full((100, 3), 20, np.uint8), "home")
        palette = ArenaPalette({"home": [profile]})
        self.assertEqual(palette.labels(np.full((1, 1, 3), 20, np.uint8))[0, 0], palette.index("home"))
        self.assertNotEqual(palette.labels(np.full((1, 1, 3), 39, np.uint8))[0, 0], palette.index("home"))
        with self.assertRaises(ValueError):
            fit_patch(np.full((100, 3), 65, np.uint8), "home")

    def test_near_black_camera_noise_uses_absolute_chroma(self):
        # Actual saved home samples are S=255 at V=5 and 10. Ratio saturation
        # should not reject a five-level channel difference in a nearly black pixel.
        saved = [{"center": [0, 255, v], "scale": [90, 35, 45]} for v in (5, 10)]
        pixels = np.uint8([[[5, 0, 0], [0, 10, 0], [8, 8, 8], [20, 0, 0], [50, 50, 50]]])
        for palette in (ArenaPalette(), ArenaPalette({"home": saved})):
            labels = palette.labels(pixels)[0]
            self.assertTrue(np.all(labels[:3] == palette.index("home")))
            self.assertTrue(np.all(labels[3:] != palette.index("home")))
        fitted = fit_patch(np.tile([0, 0, 10], (100, 1)).astype(np.uint8), "home")
        self.assertEqual(ArenaPalette({"home": [fitted]}).labels(pixels)[0, 1], 5)
        with self.assertRaises(ValueError):
            fit_patch(np.tile([0, 0, 30], (100, 1)).astype(np.uint8), "home")

    def test_tied_materials_are_unknown(self):
        profile = fit_patch(np.tile(bgr(20, 150, 180), (100, 1)), "yellow")
        palette = ArenaPalette({"yellow": [profile], "cardboard": [profile]})
        self.assertEqual(palette.labels(np.array([[bgr(20, 150, 180)]]))[0, 0], -1)


if __name__ == "__main__":
    unittest.main()
