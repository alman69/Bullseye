"""Shared per-frame pipeline: detection -> floor free-space check.

Used by the standalone app (main.py --navigate) and by Mission Control's vision
service. It only measures the floor; it never sends anything to the rover. Motion
decisions live in Mission Control's autopilot.
"""
from __future__ import annotations

import cv2
import numpy as np

from .detector import ColorDetector, Detection
from .freespace import FloorSettings, analyze_floor


class NavRunner:
    """Floor check on every frame: where the floor is open and where obstacles block it."""

    def __init__(self, cfg: dict):
        self.floor = FloorSettings.from_dict(cfg["floor"])
        self.floor_class = cfg.get("classifier", {}).get("floor_class", "floor")
        # Blue obstacles always count as "not floor", even if they look pale.
        self.obstacle_colors = list(cfg.get("obstacles", {}).get("colors", ["blue"]))
        self.free = None
        self.free_obstacles = None   # obstacle colors only (walls/shadows ignored): for driving up to a wall

    def reset(self) -> None:
        """Kept for callers that reset on a target change; the floor check has no state."""

    def update(self, frame, det: Detection, detector: ColorDetector, now: float | None = None, prepared=None):
        exclude = detector.mask(frame.image, det.target_color, prepared=prepared) if det.detected else None
        rover = detector.ignore_mask(frame.image.shape)   # the rover's own parts count as floor, never as obstacles
        if rover is not None:
            exclude = rover if exclude is None else cv2.bitwise_or(exclude, rover)
        floor_mask = detector.class_mask(prepared, self.floor_class)
        obstacle = None
        for color in self.obstacle_colors:
            if color != det.target_color and color in detector.colors:
                m = detector.mask(frame.image, color, prepared=prepared)
                obstacle = m if obstacle is None else cv2.bitwise_or(obstacle, m)
        if obstacle is not None and rover is not None:
            obstacle = self._without_rover_parts(obstacle, rover)
        self.free = analyze_floor(frame.image, self.floor, exclude, floor_mask=floor_mask, obstacle_mask=obstacle)
        everything = np.full(frame.image.shape[:2], 255, np.uint8)
        self.free_obstacles = analyze_floor(frame.image, self.floor, None, floor_mask=everything, obstacle_mask=obstacle)
        return self.free

    @staticmethod
    def _without_rover_parts(obstacle: np.ndarray, rover: np.ndarray, max_ratio: float = 0.02) -> np.ndarray:
        """Drop small obstacle-colored blobs touching the rover mask: the gripper's blue tape poking
        out past the mask boxes is part of the rover, not an obstacle. Real obstacles are bigger."""
        near = cv2.dilate(rover, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(obstacle, connectivity=8)
        limit = max_ratio * obstacle.shape[0] * obstacle.shape[1]
        out = obstacle.copy()
        for i in range(1, count):
            if stats[i, cv2.CC_STAT_AREA] < limit:
                blob = labels == i
                if near[blob].any():
                    out[blob] = 0
        return out

    def to_dict(self) -> dict:
        return {"free_space": self.free.to_dict() if self.free else None}
