"""Timed sample mission: approach, signal success, then return to the home flag.

Experimental: color/sonar heuristics require arena acceptance tests; no servo collection.
"""
import asyncio
import time

RED, GREEN = (255, 0, 0), (0, 255, 0)
GLIMPSE_COOLDOWN = 3  # moves to keep searching after a failed glimpse chase
MOVE_TO_DIRECTION = {"turn_left": "left", "turn_right": "right", "forward": "forward", "backward": "backward"}
TURN_DIRECTION = "left"
SONAR_CLEARANCE_CM = 1.0  # integer sonar reports: stop one centimetre early


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
        settings.explore_timeout_s = max(30, min(300, settings.explore_timeout_s or 120))
        stop_line = cfg["ground"].get("stop_line_y")
        if color == "home":                    # the flag is big and on a wall: arrive by ultrasonic only
            settings.arrive_area_ratio, stop_line = 2.0, None
        return pipeline.Planner(settings, use_distance=False, stop_line_y=stop_line)

    def _stop_cm(self, color: str, planner) -> float:
        nav = self.vision.cfg["navigation"]
        return float(nav.get("base_distance_cm", 15)) if color == "home" else float(planner.s.arrive_distance_cm)

    async def _led(self, rgb) -> None:
        setter = getattr(self.bridge, "set_led", None)
        if setter is not None:
            if await setter(rgb) is not True:
                raise AutopilotError("LED/STOP command was not acknowledged. Motion halted.")

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
        if not seen or seen["target"] != color or seen["seq"] <= after_seq:
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
            if color == "home":
                raise AutopilotError("Home approach needs a valid ultrasonic reading. Stopped.")
            return action
        stop_cm = self._stop_cm(color, planner)
        room = distance - stop_cm - SONAR_CLEARANCE_CM
        if room <= 0:
            planner._enter("ARRIVED")
            return planner._action("arrived", f"Target centered, ultrasonic {distance:.0f} cm (stop at {stop_cm:.0f} cm).")
        ms = self._fitting_forward_ms(planner, room)
        if action.duration_ms > ms:
            return planner._move("forward", ms, f"Ultrasonic {distance:.0f} cm: shortened step to stop at {stop_cm:.0f} cm.")
        return action

    @staticmethod
    def _fitting_forward_ms(planner, room):
        table = sorted((ms, cm) for ms, cm in planner.forward_table if ms >= 50 and cm > 0)
        if not table:
            raise AutopilotError("Measure forward steps before autonomous approach.")
        fitting = [ms for ms, cm in table if cm <= room]
        if fitting:
            return max(fitting)
        ms, cm = table[0]
        duration = int(ms * room / cm)
        if duration < 50:
            raise AutopilotError("Stopped short of the target: remaining distance is smaller than a safe timed step. Adjust manually; arrival is not confirmed.")
        return duration

    async def _confirm_size_arrival(self, color: str, planner, action):
        """The camera says "looks big enough". If the ultrasonic can see the object (a reading a little
        beyond the stop distance), keep creeping to the stop distance instead. If it reads far or no
        echo, the object is below the beam: trust the camera."""
        distance = await self._wall_distance()
        stop_cm = self._stop_cm(color, planner)
        if distance is None or distance <= 0 or distance > stop_cm + 15:
            return action
        room = distance - stop_cm - SONAR_CLEARANCE_CM
        if room <= 0:
            return planner._action("arrived", f"Target centered, ultrasonic {distance:.0f} cm (stop at {stop_cm:.0f} cm).")
        planner._enter("APPROACH")
        return planner._move("forward", self._fitting_forward_ms(planner, room), f"Looks close; ultrasonic {distance:.0f} cm: creeping to {stop_cm:.0f} cm.")

    # ------------------------------------------------------------ behaviors
    async def _wall_distance(self):
        """Fresh ultrasonic reading in cm, or None if unknown (no echo or sensor unavailable)."""
        refresh = getattr(self.bridge, "refresh_distance", None)
        if refresh is None or not await refresh():
            return None
        return self.bridge.distance_cm

    async def go_to(self, color: str, avoid: bool = True, max_steps: int | None = None) -> bool:
        """Drive until repeated arrival confirmation or a bounded search timeout."""
        color = "home" if color == "black" else color
        self.vision.set_target(color)
        planner = self._planner(color)
        seq = self._seq()
        decided_seq = seq        # newest frame at the previous decision
        move = None              # the last move: kind, ms and when the wheels turned
        relook = 0               # "look again" decisions left after turning back to a glimpse
        cooldown = 0
        steps = arrivals = 0
        deadline = time.monotonic() + max(30, min(300, planner.s.explore_timeout_s or 120))
        while max_steps is None or steps < max_steps:
            if time.monotonic() >= deadline:
                raise AutopilotError("Search time limit reached. Stopped; reposition or start a new run.")
            steps += 1
            det, free, free_obstacles = await self._fresh_view(seq)
            if color == "home" and det is not None and det.detected and free_obstacles is not None:
                free = free_obstacles               # heading for the wall itself: only obstacles block
            detected = det is not None and det.detected
            action = None
            if detected or planner.was_at_line:      # at the stop line, vanishing = arrived (planner decides)
                relook = cooldown = 0
            else:
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
                        cooldown = GLIMPSE_COOLDOWN
            decided_seq = self._seq()
            if action is None:
                # The floor check always guards exploring (so it never drives into a wall); while
                # approaching a visible target it is only used when obstacle avoidance is on.
                use_floor = avoid or not detected
                action = planner.decide(det, free if use_floor else None, None, camera_ok=True)
            if action.kind == "forward" and action.state == "APPROACH":
                action = await self._ultrasonic_arrival(color, planner, action)
            elif action.kind == "arrived":
                if color == "home":
                    action = await self._ultrasonic_arrival(color, planner, planner._move("forward", 200, "Verify base distance"))
                else:
                    action = await self._confirm_size_arrival(color, planner, action)
            elif action.kind == "forward" and action.state in ("EXPLORE", "AVOID"):
                distance = await self._wall_distance()
                limit = planner.s.wall_distance_cm
                if distance is None or distance <= 0:
                    raise AutopilotError("Exploration needs a valid wall-distance reading. Stopped.")
                if distance <= limit + SONAR_CLEARANCE_CM:
                    action = planner.wall_ahead(distance, free)
                else:
                    ms = self._fitting_forward_ms(planner, distance - limit - SONAR_CLEARANCE_CM)
                    if action.duration_ms > ms:
                        action = planner._move("forward", ms, "Shortened exploration step to preserve wall clearance.")
            self.report("running", f"{color}: {action.state} → {action.label()}")
            if action.kind == "give_up" or action.state == "GIVE_UP":
                raise AutopilotError(action.reason)
            if action.kind == "arrived":
                if await self.bridge.stop() is not True:
                    raise AutopilotError("Arrival STOP was not acknowledged.")
                if not detected or abs(det.horizontal_error) > planner.s.aim_deadband:
                    arrivals = 0
                    planner.reset()
                else:
                    arrivals += 1
                    if arrivals >= 3:
                        self.report("arrived", f"Stopped at {color}. {action.reason}")
                        return True
                planner._enter("APPROACH")  # re-evaluate arrival on each fresh frame
                seq = self._seq()
                continue
            arrivals = 0
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

    async def collect(self, color: str, avoid: bool = True) -> bool:
        """Stop at the ore and signal sample success without actuating a gripper."""
        if color not in ("red", "green"):
            raise AutopilotError("Sample targets are red or green; blue is an obstacle.")
        await self._led(RED)
        await self.go_to(color, avoid=avoid)
        await self._led(GREEN)
        self.report("sample_reached", f"Stopped at {color}. Sample reached: green LEDs and happy display.")
        return True

    async def return_to_base(self, avoid: bool = True) -> bool:
        """Reacquire the home flag, stop at the wall, and reset the LEDs to red."""
        # Reacquire the actual flag from the current heading; no blind 180-degree assumption.
        await self.go_to("home", avoid=avoid)
        await self._led(RED)
        self.report("home_arrived", "Stopped at the black home flag. Mission complete; LEDs red.")
        return True

    async def mission(self, color: str, avoid: bool = True, pause_s: float = 3.0) -> bool:
        """Reach a red/green ore, celebrate while stopped, then automatically return home."""
        await self.collect(color, avoid=avoid)
        await asyncio.sleep(max(0.0, min(10.0, pause_s)))
        self.report("running", "Sample signalled. Searching for the black home flag; LEDs stay green until home.")
        return await self.return_to_base(avoid=avoid)
