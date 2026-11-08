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

Two mask sources:
  * "hsv": fixed HSV range per color (config "colors"), the original method.
  * "classifier": per-pixel material classifier learned from samples
    (colorclass.py, color_model.json), after floor-referenced white balance.
    Colors the model doesn't know fall back to their HSV range.
When the ground is calibrated (ground.py), candidates whose bottom edge is above
the horizon or whose width doesn't fit a block at that distance are rejected,
and the result carries `bottom_y` and `distance_cm_est`.

`area` and `area_ratio` are image measurements only. They grow as the rover
approaches a target and can drive a relative "getting closer" behavior, but
they are NOT a physical distance: rock size, angle and partial occlusion all
change them. Proximity must be confirmed by the ultrasonic sensor.
"""
from __future__ import annotations

import time
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

COLORS = ("red", "yellow", "green", "blue", "black")
Prepared = namedtuple("Prepared", "corrected labels")   # white-balanced frame + class-index image
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
    bottom_y: float = 0.0             # bbox bottom / frame height
    distance_cm: float | None = None  # from the ground calibration


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
    bottom_y: float | None = None           # bbox bottom / frame height (lower = closer)
    distance_cm_est: float | None = None    # from the bottom edge (ground calibration); camera estimate only
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
            "bottom_y": None if self.bottom_y is None else round(self.bottom_y, 4),
            "distance_cm_est": None if self.distance_cm_est is None else round(self.distance_cm_est, 1),
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
    def __init__(self, ranges: dict, settings: DetectorSettings | None = None,
                 model=None, white_balance=None, ground=None, ignore_boxes=None):
        self.ranges = {name: (r if isinstance(r, HsvRange) else HsvRange.from_dict(r)) for name, r in ranges.items()}
        self.settings = settings or DetectorSettings()
        k = self.settings.morph_kernel
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)) if k and k > 1 else None
        self.model = model                  # colorclass.ColorModel or None (HSV mode)
        self.wb = white_balance             # colorclass.WhiteBalance or None
        self.ground = ground                # ground.GroundModel or None
        # Rover mask: normalized [x0, y0, x1, y1] boxes over parts of the rover itself (e.g. the
        # gripper) that are always in view. Ignored for colors and treated as floor by the floor check.
        self.ignore_boxes = [list(map(float, b)) for b in (ignore_boxes or [])]
        self._ignore_cache = (None, None)

    @classmethod
    def from_config(cls, cfg: dict, base_dir=None, mode: str | None = None) -> "ColorDetector":
        from .colorclass import ColorModel
        from .ground import GroundModel
        cc = cfg.get("classifier", {})
        mode = mode or cc.get("mode", "auto")
        base = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
        model_path = base / cc.get("model_path", "color_model.json")
        model = None
        if mode == "classifier" or (mode == "auto" and model_path.exists()):
            if not model_path.exists():
                raise FileNotFoundError(f"{model_path} not found: sample colors first (main.py, key k)")
            model = ColorModel.load(model_path)
        ground = GroundModel.from_dict(cfg.get("ground"))
        det = cls(cfg["colors"], DetectorSettings.from_dict(cfg["detector"]),
                  ground=ground if ground.calibrated else None,
                  ignore_boxes=cfg.get("rover_mask", {}).get("boxes", []))
        det.set_model(model, cc)
        return det

    def set_model(self, model, cc: dict | None = None) -> None:
        from .colorclass import WhiteBalance
        cc = cc or {}
        self.model = model
        self.wb = None
        if model is not None and model.reference_floor is not None and cc.get("white_balance", True):
            self.wb = WhiteBalance(model.reference_floor, cc.get("wb_alpha", 0.2), cc.get("wb_min_floor", 0.02),
                                   cc.get("wb_gain_min", 0.6), cc.get("wb_gain_max", 1.6))

    @property
    def mode(self) -> str:
        return "classifier" if self.model is not None else "hsv"

    def prepare(self, frame_bgr: np.ndarray):
        """Classifier mode: blur, white-balance and label every pixel once per frame. HSV mode: None."""
        if self.model is None:
            return None
        k = self.settings.blur_kernel
        img = cv2.GaussianBlur(frame_bgr, (k, k), 0) if k and k > 1 else frame_bgr
        if self.wb is not None:
            self.wb.update(img)
            img = self.wb.apply(img)
        return Prepared(img, self.model.labels(img))

    def class_mask(self, prepared, name: str):
        """0/255 mask of a model class (e.g. "floor"), or None if the model doesn't have it."""
        if prepared is None or self.model is None:
            return None
        idx = self.model.index(name)
        return None if idx is None else (prepared.labels == idx).astype(np.uint8) * 255

    def ignore_mask(self, shape):
        """0/255 mask of the rover-mask boxes for a frame of this shape, or None if there are none."""
        if not self.ignore_boxes:
            return None
        h, w = shape[:2]
        key = (h, w, tuple(tuple(b) for b in self.ignore_boxes))
        if self._ignore_cache[0] != key:
            m = np.zeros((h, w), np.uint8)
            for x0, y0, x1, y1 in self.ignore_boxes:
                m[int(round(y0 * h)):int(round(y1 * h)), int(round(x0 * w)):int(round(x1 * w))] = 255
            self._ignore_cache = (key, m)
        return self._ignore_cache[1]

    def _without_ignored(self, m: np.ndarray) -> np.ndarray:
        ignore = self.ignore_mask(m.shape)
        if ignore is not None:
            m = m.copy()
            m[ignore > 0] = 0
        return m

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

    def mask(self, frame_bgr: np.ndarray, color: str, hsv: np.ndarray | None = None,
             prepared=None) -> np.ndarray:
        """Binary (0/255) mask of `color` after noise cleanup."""
        if self.model is not None and self.model.index(color) is not None:
            prepared = prepared if prepared is not None else self.prepare(frame_bgr)
            m = self.class_mask(prepared, color)
            if self._kernel is not None:
                m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self._kernel)
                m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, self._kernel)
            return self._without_ignored(m)
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
        return self._without_ignored(m)

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
            bottom = (y + bh) / float(h)
            distance = None
            if self.ground is not None:
                if not self.ground.plausible(bottom, bw / float(w)):
                    continue                     # above the horizon or wrong size for a block there
                distance = self.ground.distance_cm(bottom)
            m = cv2.moments(c)
            if m["m00"] > 0:
                center = (int(round(m["m10"] / m["m00"])), int(round(m["m01"] / m["m00"])))
            else:
                center = (x + bw // 2, y + bh // 2)
            out.append(Candidate((x, y, bw, bh), center, area, extent, bottom, distance))
        out.sort(key=lambda cand: cand.area, reverse=True)
        return out

    def detect(self, frame_bgr: np.ndarray, target_color: str,
               frame_id: int | None = None, timestamp: float | None = None, prepared=None) -> Detection:
        t0 = time.perf_counter()
        h, w = frame_bgr.shape[:2]
        if prepared is None and self.model is not None:
            prepared = self.prepare(frame_bgr)
        cands = self.candidates(self.mask(frame_bgr, target_color, prepared=prepared))
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
            det.bottom_y = best.bottom_y
            det.distance_cm_est = best.distance_cm
        det.processing_ms = (time.perf_counter() - t0) * 1000.0
        return det

    def _range(self, color: str) -> HsvRange:
        if color not in self.ranges and self.model is not None:
            raise ValueError(f"unknown color {color!r}; model classes: {', '.join(self.model.classes)}")
        try:
            return self.ranges[color]
        except KeyError:
            raise ValueError(f"unknown color {color!r}; configured: {', '.join(self.ranges)}") from None
