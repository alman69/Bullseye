"""Demo autopilot: drive to a colored mineral, turn around, drive to the black flag.

Deliberately simple for the hackathon demo:
  * The minerals are in front of the rover and the black home-base flag is behind them
    (it comes into view after a 180-degree turn). Pickup and drop are done by hand.
  * Every move is ONE vendor N2 timed step through FactoryBridge.pulse(), which sends a STOP
    before and after the step, so the rover never keeps driving if the laptop goes away.
  * After each step it waits for a camera frame taken after the move ended, then asks the
    vision planner (the same one the dashboard shows as DRY RUN) for the next step.
  * Obstacle avoidance is optional: with avoid=True the planner's camera floor check can
    steer around things that aren't floor or the target (yellow is always an obstacle); with
    avoid=False it drives at the target.
  * It never gives up: when a full spin doesn't find the target (e.g. it's hidden behind an
    obstacle), it drives toward open floor and spins again, until it finds it or STOP is pressed.
    Before each exploring/avoiding forward step it reads the ultrasonic sensor; a wall closer
    than `wall_distance_cm` turns it toward open floor instead.
  * STOP on /bench cancels the task immediately; the step in progress still ends with a STOP.
    It only stops on its own for a fault: no fresh camera frames.
"""
import asyncio
import time

MOVE_TO_DIRECTION = {"turn_left": "left", "turn_right": "right", "forward": "forward", "backward": "backward"}
TURN_DIRECTION = "left"


class AutopilotError(RuntimeError):
    """The run could not finish (target lost, no camera, too many steps). Not a hardware fault."""


class Autopilot:
    def __init__(self, bridge, vision, report=lambda state, message: None):
        self.bridge, self.vision, self.report = bridge, vision, report

    # ------------------------------------------------------------ helpers
    def _planner(self):
        pipeline = self.vision.v.pipeline
        cfg = self.vision.cfg
        settings = pipeline.NavSettings.from_dict(cfg["navigation"])
        settings.explore_timeout_s = 0          # never give up: keep exploring until found or STOP
        return pipeline.Planner(settings, use_distance=False, stop_line_y=cfg["ground"].get("stop_line_y"))

    def _seq(self) -> int:
        latest = self.vision.latest
        return latest["seq"] if latest else -1

    async def _fresh_view(self, after_seq: int, timeout: float = 3.0):
        """Detection + floor result from a frame captured after the last move ended."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._seq() >= after_seq + 2:          # +2: skip a frame that may have been mid-capture
                with self.vision.lock:
                    return self.vision._det, self.vision.nav.free
            await asyncio.sleep(0.05)
        raise AutopilotError("No fresh camera frame. Check the camera and Wi-Fi.")

    async def _step(self, move: str, pwm: int, duration_ms: int):
        direction = MOVE_TO_DIRECTION[move]
        await self.bridge.pulse(direction, max(1, min(100, int(pwm))), max(50, min(1000, int(duration_ms))))

    # ------------------------------------------------------------ behaviors
    async def _wall_distance(self):
        """Fresh ultrasonic reading in cm, or None if unknown (no echo or sensor unavailable)."""
        refresh = getattr(self.bridge, "refresh_distance", None)
        if refresh is None or not await refresh():
            return None
        return self.bridge.distance_cm

    async def go_to(self, color: str, avoid: bool = False, max_steps: int | None = None) -> bool:
        """Drive to `color` until it arrives. Never gives up; max_steps is only for tests."""
        self.vision.set_target(color)
        planner = self._planner()
        seq = self._seq()
        steps = 0
        while max_steps is None or steps < max_steps:
            steps += 1
            det, free = await self._fresh_view(seq)
            # The floor check always guards exploring (so it never drives into a wall); while
            # approaching a visible target it is only used when obstacle avoidance is on.
            use_floor = avoid or not (det is not None and det.detected)
            action = planner.decide(det, free if use_floor else None, None, camera_ok=True)
            if action.kind == "forward" and action.state in ("EXPLORE", "AVOID"):
                distance = await self._wall_distance()
                limit = planner.s.wall_distance_cm
                if distance is not None and 0 < distance < limit:
                    action = planner.wall_ahead(distance, free)
            self.report("running", f"{color}: {action.state} → {action.label()}")
            if action.kind == "arrived":
                self.report("arrived", f"Arrived at {color}. {action.reason}")
                return True
            seq = self._seq()
            if action.kind in MOVE_TO_DIRECTION:
                await self._step(action.kind, action.pwm, action.duration_ms)
                seq = self._seq()
        raise AutopilotError(f"Test step limit reached without reaching {color}.")

    def turn_plan(self, degrees: float = 180.0) -> list:
        """Timed turn steps (ms) that add up to about `degrees`, from the measured step table."""
        steps = self.vision.cfg["navigation"].get("steps", {}).get("turn", {})
        table = sorted(((int(ms), float(deg)) for ms, deg in steps.items() if deg and float(deg) > 0), reverse=True)
        if not table:
            raise AutopilotError("Measure the turn steps first (Calibration → Steps).")
        plan, remaining = [], float(degrees)
        for ms, deg in table:
            while remaining >= deg * 0.75:
                plan.append(ms)
                remaining -= deg
        return plan

    async def turn_around(self, degrees: float = 180.0) -> None:
        plan = self.turn_plan(degrees)
        for i, ms in enumerate(plan, 1):
            self.report("running", f"Turning around: step {i}/{len(plan)} ({ms} ms)")
            await self._step(f"turn_{TURN_DIRECTION}", self.vision.cfg["navigation"].get("turn_pwm", 60), ms)

    async def return_to_base(self, avoid: bool = False) -> bool:
        await self.turn_around()
        return await self.go_to("black", avoid=avoid)
