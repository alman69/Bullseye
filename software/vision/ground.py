"""Ground-plane geometry: image row of an object's bottom edge -> distance on the floor.

The camera sits at a fixed height and only pans, and the arena floor is flat, so
an object whose bottom edge is lower in the image is closer. Calibrate once with
a block at a few known distances (ground_calib.py stopline); then:

    ground = GroundModel.from_dict(cfg["ground"])
    ground.distance_cm(bottom_y)      # bottom_y = (bbox_y + bbox_h) / frame_height
    ground.stop_line_y                # row where the block is at navigation.arrive_distance_cm
    ground.plausible(bottom_y, width_norm)   # on the floor, and the right size for its distance

Inside the calibrated range, distances are interpolated between the measured
points. Outside it, a pinhole fit `1/d = a*y + b` is used. Its zero crossing
(`horizon_y`) is the row of "infinitely far": nothing above it can be standing
on the arena floor.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class GroundModel:
    points: list = field(default_factory=list)   # [[bottom_y_norm, distance_cm], ...]
    width_k: float | None = None                 # block width_norm * distance_cm (about constant)
    horizon_y: float | None = None
    stop_line_y: float | None = None
    size_tolerance: float = 2.0                  # accept 1/tol .. tol times the expected width

    @classmethod
    def from_dict(cls, d: dict | None) -> "GroundModel":
        d = d or {}
        return cls(points=[list(map(float, p)) for p in d.get("points", [])],
                   width_k=d.get("width_k"), horizon_y=d.get("horizon_y"),
                   stop_line_y=d.get("stop_line_y"), size_tolerance=d.get("size_tolerance", 2.0))

    def to_dict(self) -> dict:
        return {"points": [[round(y, 4), round(dist, 1)] for y, dist in self.points],
                "width_k": None if self.width_k is None else round(self.width_k, 4),
                "horizon_y": None if self.horizon_y is None else round(self.horizon_y, 4),
                "stop_line_y": None if self.stop_line_y is None else round(self.stop_line_y, 4),
                "size_tolerance": self.size_tolerance}

    @property
    def calibrated(self) -> bool:
        return len(self.points) >= 2

    # -- fitting ---------------------------------------------------------
    @classmethod
    def fit(cls, samples: list, size_tolerance: float = 2.0) -> "GroundModel":
        """samples: [(bottom_y_norm, distance_cm, width_norm or None), ...] from snapshots."""
        if len(samples) < 2:
            raise ValueError("need at least 2 calibration snapshots")
        samples = sorted(samples, key=lambda s: s[0])          # far (small y) -> near (large y)
        ys = np.array([s[0] for s in samples], float)
        ds = np.array([s[1] for s in samples], float)
        if np.any(np.diff(ys) <= 0) or np.any(np.diff(ds) >= 0):
            raise ValueError("closer blocks must sit lower in the image: check the snapshot order "
                             "and that each snapshot detected the block")
        widths = [s[2] * s[1] for s in samples if s[2]]
        model = cls(points=[[float(y), float(d)] for y, d in zip(ys, ds)],
                    width_k=float(np.median(widths)) if widths else None,
                    size_tolerance=size_tolerance)
        a, b = model._inverse_fit()
        model.horizon_y = float(-b / a) if a > 0 else None
        return model

    def _inverse_fit(self) -> tuple:
        ys = np.array([p[0] for p in self.points])
        inv = 1.0 / np.array([p[1] for p in self.points])
        a, b = np.polyfit(ys, inv, 1)
        return float(a), float(b)

    # -- queries ---------------------------------------------------------
    def distance_cm(self, bottom_y: float) -> float | None:
        if not self.calibrated:
            return None
        ys = [p[0] for p in self.points]
        ds = [p[1] for p in self.points]
        if ys[0] <= bottom_y <= ys[-1]:
            return float(np.interp(bottom_y, ys, ds))
        a, b = self._inverse_fit()
        inv = a * bottom_y + b
        if inv <= 1e-4:
            return None                                      # at/above the horizon: not on the floor
        return float(min(1000.0, max(0.0, 1.0 / inv)))

    def row_for_distance(self, distance_cm: float) -> float | None:
        if not self.calibrated:
            return None
        ys = [p[0] for p in self.points]
        ds = [p[1] for p in self.points]
        if ds[-1] <= distance_cm <= ds[0]:
            return float(np.interp(distance_cm, ds[::-1], ys[::-1]))
        a, b = self._inverse_fit()
        return float((1.0 / distance_cm - b) / a) if a > 0 else None

    def expected_width(self, distance_cm: float) -> float | None:
        if self.width_k is None or not distance_cm:
            return None
        return self.width_k / distance_cm

    def plausible(self, bottom_y: float, width_norm: float, margin: float = 0.02) -> bool:
        """False if the object can't be a block standing on the arena floor."""
        if not self.calibrated:
            return True
        if self.horizon_y is not None and bottom_y < self.horizon_y + margin:
            return False
        dist = self.distance_cm(bottom_y)
        expected = self.expected_width(dist) if dist else None
        if expected:
            ratio = width_norm / expected
            if not (1.0 / self.size_tolerance <= ratio <= self.size_tolerance):
                return False
        return True
