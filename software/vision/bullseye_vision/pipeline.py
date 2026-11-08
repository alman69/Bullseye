"""Shared per-frame pipeline: detection -> floor free-space -> planner decision.

Used by the standalone app (main.py --navigate) and by Mission Control's vision
service. It only computes and reports actions; it never sends anything to the rover.
"""
from __future__ import annotations

import time

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
        self.planner = Planner(self.settings, use_distance=False,
                               stop_line_y=cfg.get("ground", {}).get("stop_line_y"))
        self.free = None
        self.action = None
        self.next_at = 0.0

    def reset(self) -> None:
        self.planner.reset()
        self.action = None
        self.next_at = 0.0

    def update(self, frame, det: Detection, detector: ColorDetector, now: float | None = None, prepared=None):
        now = time.monotonic() if now is None else now
        exclude = detector.mask(frame.image, det.target_color, prepared=prepared) if det.detected else None
        floor_mask = detector.class_mask(prepared, self.floor_class)
        self.free = analyze_floor(frame.image, self.floor, exclude, floor_mask=floor_mask)
        if now >= self.next_at:
            fresh = now - frame.received_at < 1.0
            self.action = self.planner.decide(det, self.free, None, camera_ok=fresh, now=now)
            self.next_at = now + self.settings.decision_period_s
        return self.action

    def to_dict(self) -> dict:
        return {"dry_run": True, "state": self.planner.state,
                "action": self.action.to_dict() if self.action else None,
                "free_space": self.free.to_dict() if self.free else None}
