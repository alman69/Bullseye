"""Shared per-frame pipeline: detection -> floor free-space -> planner decision.

Used by the standalone app (main.py --navigate) and by Mission Control's vision
service. It only computes and reports actions; it never sends anything to the rover.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from .detector import ColorDetector, Detection
from .freespace import FloorSettings, analyze_floor
from .planner import NavSettings, Planner


class NavRunner:
    """Dry run: floor check on every frame, one planner decision every
    `decision_period_s` (roughly one move + settle in the real loop).
    It only computes and displays actions; it never sends anything to the rover.
    Vision only: the ultrasonic sensor is not read, so arrival is visual."""

    def __init__(self, cfg: dict):
        self.floor = FloorSettings.from_dict(cfg["floor"])
        self.settings = NavSettings.from_dict(cfg["navigation"])
        self.floor_class = cfg.get("classifier", {}).get("floor_class", "floor")
        # Obstacle colors (the blue obstacles) always count as "not floor", even if they look pale.
        self.obstacle_colors = list(cfg.get("obstacles", {}).get("colors", ["blue"]))
        self.planner = Planner(self.settings, use_distance=False,
                               stop_line_y=cfg.get("ground", {}).get("stop_line_y"))
        self.free = None
        self.free_obstacles = None   # obstacle colors only (walls/shadows ignored): for driving up to a wall
        self.action = None
        self.next_at = 0.0

    def reset(self) -> None:
        self.planner.reset()
        self.action = None
        self.next_at = 0.0

    def update(self, frame, det: Detection, detector: ColorDetector, now: float | None = None, prepared=None):
        now = time.monotonic() if now is None else now
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
        if now >= self.next_at:
            fresh = now - frame.received_at < 1.0
            self.action = self.planner.decide(det, self.free, None, camera_ok=fresh, now=now)
            self.next_at = now + self.settings.decision_period_s
        return self.action

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
        return {"dry_run": True, "state": self.planner.state,
                "action": self.action.to_dict() if self.action else None,
                "free_space": self.free.to_dict() if self.free else None}
