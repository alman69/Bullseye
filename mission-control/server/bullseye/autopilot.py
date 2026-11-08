"""Demo autopilot: drive to a colored mineral, turn around, drive to the black flag.

Deliberately simple for the hackathon demo:
  * The minerals are in front of the rover and the black home-base flag is behind them
    (it comes into view after a 180-degree turn). Pickup and drop are done by hand.
  * Every move is ONE vendor N2 timed step through FactoryBridge.pulse(), which sends a STOP
    before and after the step, so the rover never keeps driving if the laptop goes away.
  * After each step it waits for a camera frame taken after the move ended, then asks the
    vision planner (the same one the dashboard shows as DRY RUN) for the next step.
  * Obstacle avoidance is optional: with avoid=True the planner's camera floor check can
    steer around things that aren't floor or the target; with avoid=False it drives at the target.
  * STOP on /bench cancels the task immediately; the step in progress still ends with a STOP.
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
    async def go_to(self, color: str, avoid: bool = False, max_steps: int = 60, timeout_s: float = 150) -> bool:
        self.vision.set_target(color)
        planner = self._planner()
        seq = self._seq()
        started = time.monotonic()
        for _ in range(max_steps):
            if time.monotonic() - started > timeout_s:
                raise AutopilotError(f"Timed out driving to {color}.")
            det, free = await self._fresh_view(seq)
            action = planner.decide(det, free if avoid else None, None, camera_ok=True)
            self.report("running", f"{color}: {planner.state} → {action.label()}")
            if action.kind == "arrived":
                self.report("arrived", f"Arrived at {color}. {action.reason}")
                return True
            if planner.state in ("EXPLORE", "GIVE_UP"):
                raise AutopilotError(f"Lost the {color} target after a full search turn. Point the rover at it and retry.")
            seq = self._seq()
            if action.kind in MOVE_TO_DIRECTION:
                await self._step(action.kind, action.pwm, action.duration_ms)
                seq = self._seq()
        raise AutopilotError(f"Too many steps without reaching {color}.")

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
