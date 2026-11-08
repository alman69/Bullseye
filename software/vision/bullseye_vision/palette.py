"""Small, explicit arena palette: circular hue + saturation/brightness envelopes.

Each pixel gets at most one label; uncertain/out-of-range pixels stay unknown.
A user-selected patch creates a robust profile immediately, without training a
Gaussian model or changing white balance as objects enter the camera view.
"""
from __future__ import annotations

import cv2
import numpy as np

OBJECTS = ("red", "green", "blue", "yellow", "cardboard", "home")
CLASSES = OBJECTS + ("floor",)
HOME_MAX_VALUE = 40  # near-black only, even for previously saved broad profiles
HOME_MAX_CHROMA = 12  # max(B,G,R) - min(B,G,R); HSV saturation is unstable near black
# OpenCV HSV units. Background materials compete with objects, never count as them.
DEFAULT_PROFILES = {
    "red": ([0, 215, 175], [12, 100, 115]),
    "green": ([62, 190, 160], [25, 110, 110]),
    "blue": ([112, 215, 160], [22, 100, 110]),
    "yellow": ([28, 215, 205], [10, 105, 90]),
    "cardboard": ([18, 110, 155], [12, 85, 100]),
    "home": ([0, 0, 20], [90, 255, 20]),
    "floor": ([0, 15, 215], [90, 60, 125]),
}


def hue_delta(h, center):
    return (h - center + 90) % 180 - 90


def fit_patch(pixels: np.ndarray, name: str) -> dict:
    """Fit one tight patch; reject mixed selections instead of widening indefinitely."""
    if name not in CLASSES:
        raise ValueError("Choose an arena color, wall, home base, or floor.")
    if len(pixels) < 9:
        raise ValueError("Choose a patch at least 3 × 3 pixels inside the material.")
    hsv = cv2.cvtColor(pixels.reshape(1, -1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    # Hue is circular: samples straddling red's 179/0 boundary are adjacent.
    angles = hsv[:, 0] * (2 * np.pi / 180)
    anchor = float(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean()) * 180 / (2 * np.pi) % 180)
    center = np.median(hsv, axis=0)
    center[0] = (anchor + np.median(hue_delta(hsv[:, 0], anchor))) % 180
    delta = np.abs(hsv - center)
    delta[:, 0] = np.abs(hue_delta(hsv[:, 0], center[0]))
    spread = np.percentile(delta, 90, axis=0)
    if name in OBJECTS[:4] and (center[1] < 35 or center[2] < 20):
        raise ValueError("That patch is too gray or dark. Click the colored surface away from glare/shadow.")
    if name == "home" and (center[2] > HOME_MAX_VALUE or np.median(pixels.max(axis=1) - pixels.min(axis=1)) > HOME_MAX_CHROMA):
        raise ValueError("Home base requires near-black: choose a neutral black patch, not a gray shadow or colored surface.")
    if name not in ("home", "floor") and spread[0] > 18:
        raise ValueError("That patch mixes colors. Select a smaller area inside one material.")
    scale = np.clip(spread * 1.6 + [5, 25, 35], [7, 35, 45], [23, 100, 110])
    if name in ("floor", "home"):
        center[0], scale[0] = 0, 90
    if name == "home":
        center[1], scale[1] = 0, 255  # near-black neutrality uses absolute channel spread
        scale[2] = float(np.clip(spread[2] * 1.6 + 6, 8, 14))
    return {"center": [round(float(x), 3) for x in center],
            "scale": [round(float(x), 3) for x in scale], "pixels": int(len(pixels))}


class ArenaPalette:
    classes = CLASSES
    reference_floor = None  # deliberate: calibration and inference use the same camera pixels

    def __init__(self, profiles=None):
        self.profiles = profiles or {}
        # Compile profiles once. Preserve all 180 hue values; S/V bins are only four
        # levels wide (<=2 levels of quantization error), much finer than fit margins.
        h, s, v = np.meshgrid(np.arange(180), np.arange(64) * 4 + 2,
                             np.arange(64) * 4 + 2, indexing="ij")
        grid = np.stack((h, s, v), axis=-1).reshape(180, -1, 3).astype(np.float32)
        self._lut = self._labels_hsv(grid).reshape(180, 64, 64)

    def index(self, name):
        return self.classes.index(name) if name in self.classes else None

    def labels(self, bgr):
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        labels = self._lut[hsv[..., 0], hsv[..., 1] >> 2, hsv[..., 2] >> 2].copy()
        # Exact guard also applies to old saved profiles and lookup-bin boundaries.
        labels[(labels == self.index("home")) &
               ((hsv[..., 2] > HOME_MAX_VALUE) | ((bgr.max(axis=2) - bgr.min(axis=2)) > HOME_MAX_CHROMA))] = -1
        return labels

    def _labels_hsv(self, hsv):
        best = np.full(hsv.shape[:2], np.inf, np.float32)
        second = best.copy()
        labels = np.full(hsv.shape[:2], -1, np.int8)
        for idx, name in enumerate(self.classes):
            defaults = DEFAULT_PROFILES[name]
            profiles = self.profiles.get(name) or [{"center": defaults[0], "scale": defaults[1]}]
            score = np.full(hsv.shape[:2], np.inf, np.float32)
            for profile in profiles:
                center, scale = np.asarray(profile["center"], np.float32), np.asarray(profile["scale"], np.float32)
                d = np.abs(hsv - center).astype(np.float32)
                d[..., 0] = np.abs(hue_delta(hsv[..., 0], center[0]))
                if name in ("home", "floor"):
                    d[..., 0] = 0
                if name == "home":
                    d[..., 1] = 0  # valid even for old S=255, V≈0 black calibration
                d /= scale
                valid = np.max(d, axis=2) <= 1
                if name == "home":
                    valid &= (hsv[..., 2] <= HOME_MAX_VALUE) & (hsv[..., 1] * hsv[..., 2] / 255 <= HOME_MAX_CHROMA)
                elif name in OBJECTS[:4]:
                    valid &= hsv[..., 2] >= 20
                # Hue carries most identity for colors; brightness still separates yellow/cardboard.
                cost = (2 * d[..., 0] ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2) / 4
                score = np.minimum(score, np.where(valid, cost, np.inf))
            wins = score < best
            second = np.where(wins, best, np.minimum(second, score))
            best = np.where(wins, score, best)
            labels[wins] = idx
        # Reject near ties, rather than assigning floor/wall pixels an arbitrary object color.
        finite = np.isfinite(best) & np.isfinite(second)
        ambiguous = np.zeros(best.shape, bool)
        ambiguous[finite] = second[finite] - best[finite] < 0.025
        labels[ambiguous] = -1
        return labels
