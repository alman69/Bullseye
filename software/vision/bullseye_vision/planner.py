"""Reactive navigation planner: look -> decide -> one small timed move.

No camera, motor or network code lives here. `Planner.decide()` takes what the
rover sees right now (a Detection from detector.py, a FreeSpace from
freespace.py, and optionally an ultrasonic distance) and returns ONE Action,
which the caller executes as a short timed move (vendor N2 command) before the
next decision on a fresh, post-move frame.

States:
  SEARCH   target not visible: turn in place in fixed steps (a full circle max)
  APPROACH target visible: turn toward it if off-center, else step forward;
           verify with the ultrasonic sensor when it is centered and large
  AVOID    floor ahead blocked by something that is not the target: turn toward
           the side with more floor, step forward, turn back
  EXPLORE  nothing found after a full circle: drive toward the most open floor,
           then SEARCH again; give up after `explore_timeout_s`
  ARRIVED  arrival_mode "camera" (default, for small blocks below the ultrasonic beam):
           target centered and its bottom edge at/below the calibrated stop line,
           or it dropped out of the bottom of the view while centered next to the line.
           arrival_mode "ultrasonic" (tall ores): centered, large, ultrasonic <= arrive distance.
  GIVE_UP  explored too long; ask the pilot (recovers as soon as the target is seen)

The ultrasonic sensor is a wall/tall-obstacle backstop. Its reactions are
ignored while the target is centered and inside the approach zone, so a block
in front of a wall never triggers a back-off.

With a calibrated step table (navigation.steps, measured on the floor) and
camera_hfov_deg, turn and forward steps are sized from the needed angle and the
estimated remaining distance; otherwise fixed durations are used.

The rover has no wheel encoders, so it does not know where it is in the arena;
every decision is based on the current view only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

SEARCH, APPROACH, AVOID, EXPLORE, ARRIVED, GIVE_UP = "SEARCH", "APPROACH", "AVOID", "EXPLORE", "ARRIVED", "GIVE_UP"
TURN_LEFT, TURN_RIGHT, FORWARD, BACKWARD = "turn_left", "turn_right", "forward", "backward"
STOP, READ_DISTANCE, ARRIVED_ACTION = "stop", "read_distance", "arrived"
MOVES = (TURN_LEFT, TURN_RIGHT, FORWARD, BACKWARD)


@dataclass(frozen=True)
class Action:
    kind: str                # one of the constants above
    duration_ms: int = 0     # for moves: the N2 timer
    pwm: int = 0             # for moves: N2 base speed (0-255)
    state: str = ""
    reason: str = ""

    def label(self) -> str:
        text = self.kind.replace("_", " ").upper()
        return f"{text} {self.duration_ms} ms" if self.kind in MOVES else text

    def to_dict(self) -> dict:
        return {"kind": self.kind, "duration_ms": self.duration_ms, "pwm": self.pwm,
                "state": self.state, "reason": self.reason}


@dataclass
class NavSettings:
    turn_pwm: int = 60
    drive_pwm: int = 60
    aim_deadband: float = 0.12          # |horizontal_error| <= this counts as centered
    turn_min_ms: int = 80               # aiming turn length grows with the error ...
    turn_max_ms: int = 250              # ... up to this
    forward_ms: int = 300
    slow_forward_ms: int = 150
    slow_area_ratio: float = 0.04       # target this large -> shorter forward steps
    arrive_area_ratio: float = 0.08     # target this large and centered -> check arrival
    arrive_distance_cm: float = 20
    emergency_distance_cm: float = 10   # closer than this and not the target -> back off
    distance_check_every: int = 3       # forward steps between ultrasonic safety checks
    lost_decisions: int = 3             # decisions without the target before SEARCH
    search_turn_ms: int = 300           # one search step (calibrate to ~45 degrees)
    search_turns: int = 8               # steps before giving up on turning and exploring
    avoid_turn_ms: int = 300
    avoid_forward_steps: int = 2
    avoid_max_retries: int = 4
    backoff_ms: int = 300
    explore_steps: int = 3              # forward steps per exploration leg
    explore_forward_ms: int = 0         # one exploration forward step (0 = forward_ms)
    explore_timeout_s: float = 120      # 0 = never give up (the autopilot keeps exploring until STOP)
    wall_distance_cm: float = 25        # ultrasonic closer than this while exploring/avoiding = wall: turn
    decision_period_s: float = 0.5      # dry run only: how often to decide (approximates move + settle)
    arrival_mode: str = "camera"        # "camera" (stop line) or "ultrasonic" (tall ores)
    approach_zone_y: float = 0.08       # band above the stop line: tiny steps, ultrasonic ignored
    camera_hfov_deg: float = 0.0        # 0 = not calibrated (ground_calib.py fov)
    steps: dict = field(default_factory=lambda: {"forward": {}, "turn": {}})  # {"200": cm or deg, ...}

    @classmethod
    def from_dict(cls, d: dict) -> "NavSettings":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class Planner:
    def __init__(self, settings: NavSettings | None = None, use_distance: bool = True,
                 stop_line_y: float | None = None):
        self.s = settings or NavSettings()
        self.use_distance = use_distance   # False in the vision-only dry run
        self.stop_line_y = stop_line_y     # from the ground calibration (normalized row)
        self.forward_table = self._table(self.s.steps.get("forward"))
        self.turn_table = self._table(self.s.steps.get("turn"))
        self.reset()

    @staticmethod
    def _table(raw) -> list:
        """{"200": 4.5, ...} -> [(200, 4.5), ...] sorted by duration; ignores empty/invalid entries."""
        out = []
        for ms, value in (raw or {}).items():
            try:
                if float(value) > 0:
                    out.append((int(ms), float(value)))
            except (TypeError, ValueError):
                continue
        return sorted(out)

    def reset(self) -> None:
        self.state = SEARCH
        self.lost = 0
        self.last_error = 0.0
        self.search_count = 0
        self.search_dir = TURN_RIGHT
        self.forward_since_check = 0
        self.awaiting_distance = False
        self.avoid_queue: list = []
        self.avoid_side = TURN_RIGHT
        self.avoid_retries = 0
        self.explore_started: float | None = None
        self.explore_forward = 0
        self.backed_off = False            # exploring with no floor in view: back off once, then turn
        self.was_at_line = False           # last decision: centered and inside the approach zone

    # ------------------------------------------------------------------ helpers
    def _move(self, kind: str, ms: int, reason: str, pwm: int | None = None) -> Action:
        if pwm is None:
            pwm = self.s.drive_pwm if kind in (FORWARD, BACKWARD) else self.s.turn_pwm
        return Action(kind, int(ms), int(pwm), self.state, reason)

    def _action(self, kind: str, reason: str) -> Action:
        return Action(kind, 0, 0, self.state, reason)

    def _enter(self, state: str) -> None:
        self.state = state
        if state == SEARCH:
            self.search_count = 0
            self.search_dir = TURN_LEFT if self.last_error < 0 else TURN_RIGHT
        elif state == APPROACH:
            self.explore_started = None
            self.forward_since_check = 0
        elif state == EXPLORE:
            self.explore_forward = 0

    def _camera_arrival(self) -> bool:
        return self.s.arrival_mode != "ultrasonic" or not self.use_distance

    @staticmethod
    def _bottom(det) -> float | None:
        bottom = getattr(det, "bottom_y", None)
        if bottom is None and det is not None and det.bbox and det.frame_height:
            bottom = (det.bbox[1] + det.bbox[3]) / det.frame_height
        return bottom

    def _in_zone(self, det) -> bool:
        bottom = self._bottom(det)
        return bool(self.stop_line_y is not None and bottom is not None
                    and bottom >= self.stop_line_y - self.s.approach_zone_y)

    def _target_close(self, det) -> bool:
        """Target centered and close: ultrasonic reactions are ignored (it may be the block or the wall behind it)."""
        if det is None or not det.detected or abs(det.horizontal_error) > self.s.aim_deadband * 2:
            return False
        if self.stop_line_y is not None:
            return self._in_zone(det)
        return det.area_ratio is not None and det.area_ratio >= self.s.arrive_area_ratio

    def _aim_ms(self, err: float) -> int:
        if self.turn_table and self.s.camera_hfov_deg > 0:
            needed = abs(err) * self.s.camera_hfov_deg / 2.0
            fitting = [ms for ms, deg in self.turn_table if deg <= needed]
            return fitting[-1] if fitting else self.turn_table[0][0]
        span = max(1e-6, 1.0 - self.s.aim_deadband)
        frac = min(1.0, max(0.0, (abs(err) - self.s.aim_deadband) / span))
        return int(round(self.s.turn_min_ms + frac * (self.s.turn_max_ms - self.s.turn_min_ms)))

    def _forward_step(self, det) -> tuple:
        """(duration_ms, note) for one forward step toward the target."""
        dist = getattr(det, "distance_cm_est", None)
        if self.forward_table and dist is not None:
            if self._in_zone(det):
                return self.forward_table[0][0], " Inside the approach zone: smallest step."
            remaining = max(0.0, dist - self.s.arrive_distance_cm)
            fitting = [ms for ms, cm in self.forward_table if cm <= 0.7 * remaining]
            ms = fitting[-1] if fitting else self.forward_table[0][0]
            return ms, f" About {remaining:.0f} cm to go."
        if self._in_zone(det):
            return self.s.slow_forward_ms, " Inside the approach zone: short step."
        slow = det.area_ratio is not None and det.area_ratio >= self.s.slow_area_ratio
        return (self.s.slow_forward_ms, " Slowing down.") if slow else (self.s.forward_ms, "")

    # ------------------------------------------------------------------ main
    def decide(self, det, free, distance_cm: float | None = None,
               camera_ok: bool = True, now: float | None = None) -> Action:
        now = time.monotonic() if now is None else now
        if self.state == ARRIVED:
            return self._action(STOP, "Arrived. Waiting for Mission Control confirmation.")
        if self.state == GIVE_UP and not (det is not None and det.detected):
            return self._action(STOP, "Explored too long without finding the target. Ask the pilot.")
        if not camera_ok:
            return self._action(STOP, "Camera frame is stale. Waiting for fresh video.")

        expecting_distance, self.awaiting_distance = self.awaiting_distance, False
        if (distance_cm is not None and 0 < distance_cm < self.s.emergency_distance_cm
                and not self._target_close(det)):
            return self._start_avoid(free, f"Ultrasonic {distance_cm:.0f} cm and it is not the target", backoff=True)

        if det is not None and det.detected:
            self.last_error = det.horizontal_error
            self.lost = 0
            if self.state in (SEARCH, EXPLORE, GIVE_UP):
                self._enter(APPROACH)
        else:
            self.lost += 1

        if self.state == AVOID:
            return self._avoid(det, free)
        if self.state == APPROACH:
            return self._approach(det, free, distance_cm, expecting_distance)
        if self.state == SEARCH:
            return self._search(free, now)
        return self._explore(free, now)

    # ------------------------------------------------------------------ states
    def _approach(self, det, free, distance_cm, expecting_distance) -> Action:
        if det is None or not det.detected:
            if self.was_at_line and self._camera_arrival():
                self._enter(ARRIVED)
                return self._action(ARRIVED_ACTION, "Target left the bottom of the view while centered at the stop line.")
            self.was_at_line = False
            if self.lost >= self.s.lost_decisions:
                self._enter(SEARCH)
                return self._search(free, time.monotonic())
            return self._action(STOP, f"Target not seen ({self.lost}/{self.s.lost_decisions}). Looking again.")
        err = det.horizontal_error
        centered = abs(err) <= self.s.aim_deadband
        large = det.area_ratio is not None and det.area_ratio >= self.s.arrive_area_ratio
        self.was_at_line = centered and self._in_zone(det)

        if self._camera_arrival():
            bottom = self._bottom(det)
            if self.stop_line_y is not None:
                if centered and bottom is not None and bottom >= self.stop_line_y:
                    self._enter(ARRIVED)
                    est = getattr(det, "distance_cm_est", None)
                    where = f" (about {est:.0f} cm)" if est is not None else ""
                    return self._action(ARRIVED_ACTION, f"Bottom edge reached the stop line{where}.")
            elif centered and large:
                self._enter(ARRIVED)
                return self._action(ARRIVED_ACTION, "Target centered and large (stop line not calibrated; size only).")
        elif centered and large:
            if not self.use_distance:
                self._enter(ARRIVED)
                return self._action(ARRIVED_ACTION, "Target centered and large (vision only; ultrasonic not checked).")
            if expecting_distance and distance_cm is not None:
                if 0 < distance_cm <= self.s.arrive_distance_cm:
                    self._enter(ARRIVED)
                    return self._action(ARRIVED_ACTION, f"Target centered, large, ultrasonic {distance_cm:.0f} cm.")
                why = "no echo" if distance_cm <= 0 else f"ultrasonic {distance_cm:.0f} cm"
                self.forward_since_check = 0
                return self._move(FORWARD, self.s.slow_forward_ms, f"Target large but {why}; creeping closer.")
            self.awaiting_distance = True
            return self._action(READ_DISTANCE, "Target centered and large; verify the distance.")

        if free is not None and free.center_blocked and not self._clear_to_target(det, free):
            return self._start_avoid(free, "Floor ahead is blocked by something that is not the target")

        if not centered:
            kind = TURN_LEFT if err < 0 else TURN_RIGHT
            return self._move(kind, self._aim_ms(err), f"Aim at target (error {err:+.2f}).")

        if (self.use_distance and self.forward_since_check >= self.s.distance_check_every
                and not expecting_distance and not self._target_close(det)):
            self.forward_since_check = 0
            self.awaiting_distance = True
            return self._action(READ_DISTANCE, "Periodic ultrasonic safety check (walls).")
        self.forward_since_check += 1
        ms, note = self._forward_step(det)
        return self._move(FORWARD, ms, "Target centered; path clear." + note)

    def _clear_to_target(self, det, free) -> bool:
        """True when the floor in the rover's path is open all the way to the target's bottom edge.

        Something behind the target (e.g. a yellow sheet just past the ball) blocks the floor
        *beyond* the target, which doesn't matter: only obstacles between the rover and the target do.
        """
        bottom = self._bottom(det)
        if bottom is None or not free.frame_height:
            return False
        roi_top = free.roi_top_px / free.frame_height
        if roi_top >= 1:
            return False
        reach = (1.0 - bottom) / (1.0 - roi_top)     # target's bottom edge as a floor height (0 = image bottom)
        return all(free.heights[i] >= reach - 0.05 for i in free.corridor)

    def wall_ahead(self, distance_cm: float, free) -> Action:
        """The ultrasonic sees a wall right ahead of a planned forward step: turn toward open floor instead."""
        left, right = free.side_space() if free is not None else (0.0, 0.0)
        if abs(left - right) < 0.05:
            side = self.search_dir
        else:
            side = TURN_LEFT if left > right else TURN_RIGHT
        if self.state == AVOID:
            self.avoid_queue = []
            self._enter(SEARCH)
        return self._move(side, self.s.avoid_turn_ms,
                          f"Ultrasonic: wall {distance_cm:.0f} cm ahead. Turning toward open floor.")

    def _start_avoid(self, free, reason: str, backoff: bool = False) -> Action:
        left, right = free.side_space() if free is not None else (0.0, 0.0)
        if abs(left - right) < 0.05:
            side = TURN_LEFT if self.last_error < 0 else TURN_RIGHT
        else:
            side = TURN_LEFT if left > right else TURN_RIGHT
        back = TURN_RIGHT if side == TURN_LEFT else TURN_LEFT
        self.state = AVOID
        self.avoid_side = side
        self.avoid_retries = 0
        self.avoid_queue = ([(BACKWARD, self.s.backoff_ms)] if backoff else []) + \
            [(side, self.s.avoid_turn_ms)] + [(FORWARD, self.s.forward_ms)] * self.s.avoid_forward_steps + \
            [(back, self.s.avoid_turn_ms)]
        kind, ms = self.avoid_queue.pop(0)
        side_name = "left" if side == TURN_LEFT else "right"
        return self._move(kind, ms, f"{reason}. Going around on the {side_name}.")

    def _avoid(self, det, free) -> Action:
        if not self.avoid_queue:
            self._enter(APPROACH if det is not None and det.detected else SEARCH)
            if self.state == APPROACH:
                return self._approach(det, free, None, False)
            return self._search(free, time.monotonic())
        kind, ms = self.avoid_queue[0]
        if kind == FORWARD and free is not None and free.center_blocked:
            self.avoid_retries += 1
            if self.avoid_retries > self.s.avoid_max_retries:
                self.avoid_queue = []
                self._enter(SEARCH)
                return self._move(BACKWARD, self.s.backoff_ms, "Still blocked after several turns. Backing off.")
            return self._move(self.avoid_side, self.s.avoid_turn_ms, "Still blocked while avoiding. Turning further.")
        self.avoid_queue.pop(0)
        return self._move(kind, ms, "Avoiding obstacle.")

    def _search(self, free, now) -> Action:
        if self.search_count >= self.s.search_turns:
            self._enter(EXPLORE)
            return self._explore(free, now)
        self.search_count += 1
        return self._move(self.search_dir, self.s.search_turn_ms,
                          f"Searching for the target (turn {self.search_count}/{self.s.search_turns}).")

    def _explore(self, free, now) -> Action:
        if self.explore_started is None:
            self.explore_started = now
        if self.s.explore_timeout_s > 0 and now - self.explore_started > self.s.explore_timeout_s:
            self._enter(GIVE_UP)
            return self._action(STOP, "Explored too long without finding the target. Ask the pilot.")
        if free is None or not free.floor_visible or max(free.heights) < 0.05:
            if not self.backed_off:
                self.backed_off = True
                return self._move(BACKWARD, self.s.backoff_ms, "No open floor in view. Backing off.")
            return self._move(self.search_dir, self.s.avoid_turn_ms, "Still no open floor in view. Turning to look for some.")
        self.backed_off = False
        if not free.center_blocked:
            self.explore_forward += 1
            action = self._move(FORWARD, self.s.explore_forward_ms or self.s.forward_ms, "Exploring toward open floor.")
            if self.explore_forward >= self.s.explore_steps:
                self._enter(SEARCH)
            return action
        bearing = free.bearings[free.best_strip]
        kind = TURN_LEFT if bearing < 0 else TURN_RIGHT
        ms = self.s.turn_min_ms + abs(bearing) * (self.s.avoid_turn_ms - self.s.turn_min_ms)
        return self._move(kind, ms, "Exploring: turning toward the most open floor.")
