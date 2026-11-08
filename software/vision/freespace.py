"""Floor-based free-space check: "where can the rover drive?"

The arena floor is matte white, so any low-saturation pixel counts as floor
(white or gray floor, shadows included). Everything else at the bottom of the
image is in the way: cardboard walls, painted obstacles, other minerals.

Only the bottom part of the image (from `roi_top` down) is analyzed, split into
`strips` vertical strips. In each pixel column we measure how far the floor
reaches upward from the bottom edge before something interrupts it, and each
strip reports a low percentile of its columns (conservative). On a flat floor,
an interruption lower in the image is closer to the rover.

    free = analyze_floor(frame_bgr, FloorSettings(), exclude_mask=target_mask)
    free.heights   # per strip, 0 = blocked at the bottom edge, 1 = floor all the way up
    free.center_blocked

Pixels of the current target are passed as `exclude_mask` and treated as floor,
so the rover never avoids the rock it is driving to.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class FloorSettings:
    roi_top: float = 0.5            # analyze rows from this fraction of the height down to the bottom
    strips: int = 5
    s_max: int = 50                 # floor = saturation <= s_max ...
    v_min: int = 90                 # ... and brightness >= v_min (rejects black and deep shadow)
    blocked_height: float = 0.35    # strip floor height (fraction of the analyzed area) below this = blocked
    corridor_half_width: float = 0.45  # strips whose center |bearing| <= this form the rover's path
    column_percentile: float = 25   # per strip: this percentile of its column heights
    work_width: int = 160           # analysis is done on a downscaled copy for speed
    morph_kernel: int = 3
    target_dilate: int = 7          # grow the excluded target mask (pixels at work scale)
    min_floor_visible: float = 0.3  # bottom rows must be at least this much floor, else warn

    @classmethod
    def from_dict(cls, d: dict) -> "FloorSettings":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class FreeSpace:
    heights: list               # per strip, 0..1
    bearings: list              # strip centers, -1 (left edge) .. +1 (right edge)
    blocked: list               # per strip bool
    corridor: list              # indices of strips in the rover's path
    center_blocked: bool        # any corridor strip blocked
    best_strip: int             # strip with the most floor (ties -> closest to center)
    floor_visible: bool         # False -> camera sees almost no floor at the bottom edge
    bottom_floor_fraction: float
    roi_top_px: int
    frame_width: int
    frame_height: int
    mask: np.ndarray | None = field(default=None, repr=False)  # floor mask (work scale); not in to_dict()

    def to_dict(self) -> dict:
        return {
            "heights": [round(h, 3) for h in self.heights],
            "blocked": self.blocked,
            "center_blocked": self.center_blocked,
            "best_strip": self.best_strip,
            "floor_visible": self.floor_visible,
            "bottom_floor_fraction": round(self.bottom_floor_fraction, 3),
        }

    def side_space(self) -> tuple:
        """(left, right) mean floor height of the strips left/right of center."""
        mid = len(self.heights) // 2
        left = self.heights[:mid] or [0.0]
        right = self.heights[mid + (len(self.heights) % 2):] or [0.0]
        return float(np.mean(left)), float(np.mean(right))


def analyze_floor(frame_bgr: np.ndarray, settings: FloorSettings | None = None,
                  exclude_mask: np.ndarray | None = None) -> FreeSpace:
    s = settings or FloorSettings()
    h, w = frame_bgr.shape[:2]
    top = min(h - 1, max(0, int(h * s.roi_top)))
    roi = frame_bgr[top:, :]
    work_w = max(s.strips, min(s.work_width, w))
    work_h = max(1, int(round(roi.shape[0] * work_w / w)))
    small = cv2.resize(roi, (work_w, work_h), interpolation=cv2.INTER_AREA)

    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    floor = cv2.inRange(hsv, (0, 0, s.v_min), (179, s.s_max, 255))
    if s.morph_kernel and s.morph_kernel > 1:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (s.morph_kernel, s.morph_kernel))
        floor = cv2.morphologyEx(floor, cv2.MORPH_OPEN, k)
        floor = cv2.morphologyEx(floor, cv2.MORPH_CLOSE, k)
    if exclude_mask is not None:
        ex = cv2.resize(exclude_mask[top:, :], (work_w, work_h), interpolation=cv2.INTER_NEAREST)
        if s.target_dilate and s.target_dilate > 1:
            ex = cv2.dilate(ex, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (s.target_dilate, s.target_dilate)))
        floor = cv2.bitwise_or(floor, ex)

    from_bottom = floor[::-1] > 0                 # row 0 = bottom edge of the image
    not_floor = ~from_bottom
    first_block = np.where(not_floor.any(axis=0), not_floor.argmax(axis=0), work_h)
    column_heights = first_block / float(work_h)  # 0..1 per column

    edges = np.linspace(0, work_w, s.strips + 1).astype(int)
    heights, bearings = [], []
    for i in range(s.strips):
        cols = column_heights[edges[i]:edges[i + 1]]
        heights.append(float(np.percentile(cols, s.column_percentile)) if cols.size else 0.0)
        center = (edges[i] + edges[i + 1]) / 2.0
        bearings.append((center - work_w / 2.0) / (work_w / 2.0))
    blocked = [hgt < s.blocked_height for hgt in heights]
    corridor = [i for i, b in enumerate(bearings) if abs(b) <= s.corridor_half_width] or [s.strips // 2]
    best = max(range(s.strips), key=lambda i: (round(heights[i], 3), -abs(bearings[i])))
    bottom_rows = from_bottom[:max(1, work_h // 10)]
    bottom_fraction = float(bottom_rows.mean())

    return FreeSpace(
        heights=heights, bearings=bearings, blocked=blocked, corridor=corridor,
        center_blocked=any(blocked[i] for i in corridor), best_strip=best,
        floor_visible=bottom_fraction >= s.min_floor_visible,
        bottom_floor_fraction=bottom_fraction, roi_top_px=top,
        frame_width=w, frame_height=h, mask=floor,
    )
