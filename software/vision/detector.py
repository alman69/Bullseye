"""HSV color-target detector. No GUI, no camera, no motor code.

    detector = ColorDetector.from_config(load_config())
    result = detector.detect(frame_bgr, "red")       # -> Detection
    result.to_dict()                                  # JSON-ready for navigation

Pipeline: Gaussian blur -> HSV -> inRange (two ranges when red hue wraps)
-> morphological open (remove speckle) + close (fill holes) -> external
contours -> filters -> best candidate.

Candidate filters (all in config.json "detector"):
  * area >= min_area_px                 rejects noise and far-away specks
  * area / frame_area <= max_area_ratio rejects walls / floor patches / lighting
  * area / bbox_area >= min_extent      rejects thin streaks, edges and diagonal lines
A color region that passes is only a *candidate*; it is not proof that it is a rock.

Best-candidate rule: the LARGEST passing contour (by contour area).

`area` and `area_ratio` are image measurements only. They grow as the rover
approaches a target and can drive a relative "getting closer" behavior, but
they are NOT a physical distance: rock size, angle and partial occlusion all
change them. Proximity must be confirmed by the ultrasonic sensor.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

COLORS = ("red", "green", "blue")
LEFT, CENTER, RIGHT, NONE = "LEFT", "CENTER", "RIGHT", "NONE"


@dataclass(frozen=True)
class HsvRange:
    """OpenCV HSV bounds: H 0-179, S/V 0-255.

    If h[0] > h[1] the hue range wraps around 179 -> 0 (needed for red),
    e.g. h=(170, 10) means 170..179 plus 0..10.
    """
    h: tuple
    s: tuple
    v: tuple

    @property
    def wraps(self) -> bool:
        return self.h[0] > self.h[1]

    @classmethod
    def from_dict(cls, d: dict) -> "HsvRange":
        return cls(tuple(int(x) for x in d["h"]), tuple(int(x) for x in d["s"]), tuple(int(x) for x in d["v"]))

    def to_dict(self) -> dict:
        return {"h": list(self.h), "s": list(self.s), "v": list(self.v)}


@dataclass(frozen=True)
class Candidate:
    bbox: tuple          # (x, y, w, h) in pixels
    center: tuple        # (cx, cy) contour centroid in pixels
    area: float          # contour area in pixels
    extent: float        # contour area / bbox area


@dataclass
class Detection:
    target_color: str
    detected: bool
    frame_width: int
    frame_height: int
    center_x: int | None = None
    center_y: int | None = None
    horizontal_error: float | None = None   # -1 (left edge) .. 0 (center) .. +1 (right edge)
    area: float | None = None               # pixels; relative size only, not distance
    area_ratio: float | None = None         # area / frame area
    bbox: list | None = None                # [x, y, w, h]
    direction: str = NONE                   # LEFT / CENTER / RIGHT / NONE
    candidates: int = 0                     # passing regions of the target color
    frame_id: int | None = None
    timestamp: float | None = None          # time.time() when the frame was received
    processing_ms: float = 0.0              # detect() run time
    all_candidates: list = field(default_factory=list, repr=False)  # for drawing; not in to_dict()

    def to_dict(self) -> dict:
        return {
            "target_color": self.target_color,
            "detected": self.detected,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "horizontal_error": None if self.horizontal_error is None else round(self.horizontal_error, 4),
            "area": None if self.area is None else round(self.area, 1),
            "area_ratio": None if self.area_ratio is None else round(self.area_ratio, 5),
            "bbox": self.bbox,
            "direction": self.direction,
            "candidates": self.candidates,
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "processing_ms": round(self.processing_ms, 2),
        }


@dataclass
class DetectorSettings:
    min_area_px: float = 400
    max_area_ratio: float = 0.6
    min_extent: float = 0.3
    blur_kernel: int = 5        # odd; 0 or 1 disables blur
    morph_kernel: int = 5       # odd; 0 or 1 disables open/close
    center_deadband: float = 0.12  # |horizontal_error| <= this -> CENTER

    @classmethod
    def from_dict(cls, d: dict) -> "DetectorSettings":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def direction_for(error: float, deadband: float) -> str:
    if abs(error) <= deadband:
        return CENTER
    return LEFT if error < 0 else RIGHT


class ColorDetector:
    def __init__(self, ranges: dict, settings: DetectorSettings | None = None):
        self.ranges = {name: (r if isinstance(r, HsvRange) else HsvRange.from_dict(r)) for name, r in ranges.items()}
        self.settings = settings or DetectorSettings()
        k = self.settings.morph_kernel
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)) if k and k > 1 else None

    @classmethod
    def from_config(cls, cfg: dict) -> "ColorDetector":
        return cls(cfg["colors"], DetectorSettings.from_dict(cfg["detector"]))

    @property
    def colors(self) -> tuple:
        return tuple(self.ranges)

    def set_range(self, color: str, rng: HsvRange) -> None:
        self.ranges[color] = rng

    def to_hsv(self, frame_bgr: np.ndarray) -> np.ndarray:
        k = self.settings.blur_kernel
        if k and k > 1:
            frame_bgr = cv2.GaussianBlur(frame_bgr, (k, k), 0)
        return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

    def mask(self, frame_bgr: np.ndarray, color: str, hsv: np.ndarray | None = None) -> np.ndarray:
        """Binary (0/255) mask of `color` after noise cleanup."""
        rng = self._range(color)
        hsv = self.to_hsv(frame_bgr) if hsv is None else hsv
        lo_sv = (rng.s[0], rng.v[0])
        hi_sv = (rng.s[1], rng.v[1])
        if rng.wraps:
            m = cv2.inRange(hsv, np.array((rng.h[0],) + lo_sv), np.array((179,) + hi_sv))
            m |= cv2.inRange(hsv, np.array((0,) + lo_sv), np.array((rng.h[1],) + hi_sv))
        else:
            m = cv2.inRange(hsv, np.array((rng.h[0],) + lo_sv), np.array((rng.h[1],) + hi_sv))
        if self._kernel is not None:
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self._kernel)
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, self._kernel)
        return m

    def candidates(self, mask: np.ndarray) -> list:
        """Contours that pass the size/shape filters, largest first."""
        h, w = mask.shape[:2]
        frame_area = float(w * h)
        s = self.settings
        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        out = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < s.min_area_px or area / frame_area > s.max_area_ratio:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            extent = area / float(bw * bh)
            if extent < s.min_extent:
                continue
            m = cv2.moments(c)
            if m["m00"] > 0:
                center = (int(round(m["m10"] / m["m00"])), int(round(m["m01"] / m["m00"])))
            else:
                center = (x + bw // 2, y + bh // 2)
            out.append(Candidate((x, y, bw, bh), center, area, extent))
        out.sort(key=lambda cand: cand.area, reverse=True)
        return out

    def detect(self, frame_bgr: np.ndarray, target_color: str,
               frame_id: int | None = None, timestamp: float | None = None) -> Detection:
        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]
        cands = self.candidates(self.mask(frame_bgr, target_color))
        det = Detection(target_color, False, w, h, frame_id=frame_id, timestamp=timestamp,
                        candidates=len(cands), all_candidates=cands)
        if cands:
            best = cands[0]
            cx, cy = best.center
            err = max(-1.0, min(1.0, (cx - w / 2.0) / (w / 2.0)))
            det.detected = True
            det.center_x, det.center_y = cx, cy
            det.horizontal_error = err
            det.area = best.area
            det.area_ratio = best.area / float(w * h)
            det.bbox = list(best.bbox)
            det.direction = direction_for(err, self.settings.center_deadband)
        det.processing_ms = (time.perf_counter() - t0) * 1000.0
        return det

    def _range(self, color: str) -> HsvRange:
        try:
            return self.ranges[color]
        except KeyError:
            raise ValueError(f"unknown color {color!r}; configured: {', '.join(self.ranges)}") from None
