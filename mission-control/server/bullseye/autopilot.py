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
  * Brief sightings count: vision remembers the newest frame where the target was detected,
    including frames taken while the rover was moving. If the target flashed into view during a
    move but isn't in the decision frame, it turns back to where it was seen (estimated from the
    step table and when, during the turn, the frame was taken) and looks again twice.
  * Arrival: the target is centered and the ultrasonic reads the stop distance (10 cm for a
    mineral, 15 cm from the wall for the black home flag); forward steps are shortened so they
    never overshoot it. For minerals the camera "looks big enough" check only counts when the
    ultrasonic can't see the object (no echo or far reading: a ball below the beam). The flag is on a wall, so for black the
    floor check only counts obstacle colors (the wall itself is the destination).
  * LEDs: red while working, green once a sample is collected, red again after it's deposited.
  * STOP on /bench cancels the task immediately; the step in progress still ends with a STOP.
    It only stops on its own for a fault: no fresh camera frames.
"""
import asyncio
import time

RED, GREEN = (255, 0, 0), (0, 255, 0)
GLIMPSE_COOLDOWN = 3      # after chasing a glimpse that didn't pan out, ignore glimpses for this many moves
MOVE_TO_DIRECTION = {"turn_left": "left", "turn_right": "right", "forward": "forward", "backward": "backward"}
TURN_DIRECTION = "left"


class AutopilotError(RuntimeError):
    """The run could not finish (target lost, no camera, too many steps). Not a hardware fault."""


class Autopilot:
    def __init__(self, bridge, vision, report=lambda state, message: None):
        self.bridge, self.vision, self.report = bridge, vision, report

    # ------------------------------------------------------------ helpers
    def _planner(self, color: str = ""):
        pipeline = self.vision.v.pipeline
        cfg = self.vision.cfg
        settings = pipeline.NavSettings.from_dict(cfg["navigation"])
        settings.explore_timeout_s = 0          # never give up: keep exploring until found or STOP
        stop_line = cfg["ground"].get("stop_line_y")
        if color == "black":                    # the flag is big and on a wall: arrive by ultrasonic only
            settings.arrive_area_ratio, stop_line = 2.0, None
        return pipeline.Planner(settings, use_distance=False, stop_line_y=stop_line)

    def _stop_cm(self, color: str, planner) -> float:
        nav = self.vision.cfg["navigation"]
        return float(nav.get("base_distance_cm", 15)) if color == "black" else float(planner.s.arrive_distance_cm)

    async def _led(self, rgb) -> None:
        setter = getattr(self.bridge, "set_led", None)
        if setter is not None:
            await setter(rgb)

    def _seq(self) -> int:
        latest = self.vision.latest
        return latest["seq"] if latest else -1

    async def _fresh_view(self, after_seq: int, timeout: float = 3.0):
        """Detection + floor result from a frame captured after the last move ended."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._seq() >= after_seq + 2:          # +2: skip a frame that may have been mid-capture
                with self.vision.lock:
                    nav = self.vision.nav
                    return self.vision._det, nav.free, getattr(nav, "free_obstacles", None)
            await asyncio.sleep(0.02)
        raise AutopilotError("No fresh camera frame. Check the camera and Wi-Fi.")

    async def _step(self, move: str, pwm: int, duration_ms: int):
        direction = MOVE_TO_DIRECTION[move]
        await self.bridge.pulse(direction, max(1, min(100, int(pwm))), max(50, min(1000, int(duration_ms))))

    @staticmethod
    def _table(planner) -> list:
        return [(0, 0.0)] + list(planner.turn_table)

    def _turn_deg(self, planner, ms: float) -> float:
        """Degrees turned by a timed turn of `ms`, interpolated from the measured step table."""
        table = self._table(planner)
        if len(table) < 2:
            return ms * 0.049                       # ~49 degrees per second (measured at PWM 60)
        for (m0, d0), (m1, d1) in zip(table, table[1:]):
            if ms <= m1:
                return d0 + (d1 - d0) * (ms - m0) / (m1 - m0)
        (m0, d0), (m1, d1) = table[-2], table[-1]
        return d1 + (d1 - d0) * (ms - m1) / (m1 - m0)

    def _turn_ms(self, planner, deg: float) -> float:
        """Timed turn (ms) for `deg` degrees: the inverse of _turn_deg (capped by the caller)."""
        table = self._table(planner)
        if len(table) < 2:
            return deg / 0.049
        for (m0, d0), (m1, d1) in zip(table, table[1:]):
            if deg <= d1:
                return m0 + (m1 - m0) * (deg - d0) / (d1 - d0)
        return table[-1][0]

    def _glimpse(self, color: str, after_seq: int, move, planner):
        """Bearing (degrees, + = right of the current heading) of a sighting newer than `after_seq`."""
        seen = self.vision.last_seen
        target = "home" if color == "black" else color          # palette vision names the black flag "home"
        if not seen or seen["target"] not in (color, target) or seen["seq"] <= after_seq:
            return None
        nav = self.vision.cfg["navigation"]
        hfov = planner.s.camera_hfov_deg or nav.get("glimpse_hfov_deg", 50)
        bearing = seen["err"] * hfov / 2.0           # relative to the heading when the frame was taken
        if move and move["kind"] in ("turn_left", "turn_right"):
            captured = seen["t"] - nav.get("camera_latency_s", 0.15)
            span = max(1e-3, move["end"] - move["start"])
            share_after = min(1.0, max(0.0, (move["end"] - captured) / span))
            turned = share_after * self._turn_deg(planner, move["ms"])
            bearing += -turned if move["kind"] == "turn_right" else turned
        return bearing

    async def _ultrasonic_arrival(self, color: str, planner, action):
        """Before a forward step toward the centered target: arrive at the stop distance, never overshoot it."""
        distance = await self._wall_distance()
        if distance is None or distance <= 0:
            return action
        stop_cm = self._stop_cm(color, planner)
        room = distance - stop_cm
        fitting = [ms for ms, cm in planner.forward_table if cm <= room]
        if room <= 0 or not fitting:
            planner._enter("ARRIVED")
            return planner._action("arrived", f"Target centered, ultrasonic {distance:.0f} cm (stop at {stop_cm:.0f} cm).")
        if action.duration_ms > fitting[-1]:
            return planner._move("forward", fitting[-1], f"Ultrasonic {distance:.0f} cm: shorter step to stop at {stop_cm:.0f} cm.")
        return action

    async def _confirm_size_arrival(self, color: str, planner, action):
        """The camera says "looks big enough". If the ultrasonic can see the object (a reading a little
        beyond the stop distance), keep creeping to the stop distance instead. If it reads far or no
        echo, the object is below the beam: trust the camera."""
        distance = await self._wall_distance()
        stop_cm = self._stop_cm(color, planner)
        if distance is None or distance <= 0 or distance > stop_cm + 15:
            return action
        room = distance - stop_cm
        fitting = [ms for ms, cm in planner.forward_table if cm <= room]
        if room <= 0 or not fitting:
            return planner._action("arrived", f"Target centered, ultrasonic {distance:.0f} cm (stop at {stop_cm:.0f} cm).")
        planner._enter("APPROACH")
        return planner._move("forward", fitting[-1], f"Looks close; ultrasonic {distance:.0f} cm: creeping to {stop_cm:.0f} cm.")

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
        planner = self._planner(color)
        seq = self._seq()
        decided_seq = seq        # newest frame at the previous decision
        move = None              # the last move: kind, ms and when the wheels turned
        relook = 0               # "look again" decisions left after turning back to a glimpse
        cooldown = 0             # moves left during which glimpses are ignored (a chase just failed)
        steps = 0
        while max_steps is None or steps < max_steps:
            steps += 1
            det, free, free_obstacles = await self._fresh_view(seq)
            if color == "black" and det is not None and det.detected and free_obstacles is not None:
                free = free_obstacles               # heading for the wall itself: only obstacles block
            detected = det is not None and det.detected
            action = None
            if detected or planner.was_at_line:      # at the stop line, vanishing = arrived (planner decides)
                relook = cooldown = 0
            else:
                # Don't chase a new glimpse while still checking the last one, or right after one failed.
                bearing = None if (cooldown > 0 or relook > 0) else self._glimpse(color, decided_seq, move, planner)
                if bearing is not None:
                    relook = 2
                    side = "right" if bearing > 0 else "left"
                    if abs(bearing) >= 3:
                        ms = max(60, min(1000, round(self._turn_ms(planner, abs(bearing)))))
                        action = planner._move(f"turn_{side}", ms,
                                               f"Saw {color} briefly about {abs(bearing):.0f}° {side}: turning back to it.")
                    else:
                        action = planner._action("stop", f"Saw {color} briefly straight ahead: looking again.")
                elif relook > 0:
                    relook -= 1
                    action = planner._action("stop", f"Looking again where {color} was seen ({2 - relook}/2).")
                    if relook == 0:
                        cooldown = GLIMPSE_COOLDOWN       # not there: probably a false flicker, keep searching
            decided_seq = self._seq()
            if action is None:
                # The floor check always guards exploring (so it never drives into a wall); while
                # approaching a visible target it is only used when obstacle avoidance is on.
                use_floor = avoid or not detected
                action = planner.decide(det, free if use_floor else None, None, camera_ok=True)
            if action.kind == "forward" and action.state == "APPROACH":
                action = await self._ultrasonic_arrival(color, planner, action)
            elif action.kind == "arrived" and color != "black":
                action = await self._confirm_size_arrival(color, planner, action)
            elif action.kind == "forward" and action.state in ("EXPLORE", "AVOID"):
                distance = await self._wall_distance()
                limit = planner.s.wall_distance_cm
                if distance is not None and 0 < distance < limit:
                    action = planner.wall_ahead(distance, free)
            self.report("running", f"{color}: {action.state} → {action.label()}")
            if action.kind == "arrived":
                self.report("arrived", f"Arrived at {color}. {action.reason}")
                return True
            seq = self._seq()
            move = None
            if cooldown > 0 and action.kind in MOVE_TO_DIRECTION and relook == 0:
                cooldown -= 1
            if action.kind in MOVE_TO_DIRECTION:
                started = time.monotonic()
                await self._step(action.kind, action.pwm, action.duration_ms)
                wheels = min(1000, max(50, action.duration_ms)) / 1000
                overhead = max(0.0, time.monotonic() - started - wheels) / 2   # STOP before/after
                move = {"kind": action.kind, "ms": wheels * 1000,
                        "start": started + overhead, "end": started + overhead + wheels}
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

    async def collect(self, color: str, avoid: bool = False) -> bool:
        """Drive to a mineral; on arrival the LEDs turn green and Mission Control says 'Sample collected'."""
        if color == "black":
            return await self.go_to(color, avoid=avoid)
        await self._led(RED)
        await self.go_to(color, avoid=avoid)
        await self._led(GREEN)
        self.report("collected", f"Sample collected: {color}. LEDs green.")
        return True

    async def return_to_base(self, avoid: bool = False) -> bool:
        """Turn around, drive to the black flag; then 'Sample deposited' and the LEDs go back to red."""
        await self.turn_around()
        await self.go_to("black", avoid=avoid)
        self.report("deposited", "Sample deposited at home base. LEDs red.")
        await self._led(RED)
        return True

    async def mission(self, color: str, avoid: bool = False, pause_s: float = 3.0) -> bool:
        """The whole run: collect the mineral, pause (pickup), return to base."""
        await self.collect(color, avoid=avoid)
        await asyncio.sleep(pause_s)
        return await self.return_to_base(avoid=avoid)
