"""Scan all bearings, relocate to nearby clear space, and approach a selected color.

Timed motion needs arena calibration: there are no encoders or a global map.
Sample missions finish stopped for manual pickup and a manual trip home.
"""
import asyncio
import copy
import math
import statistics
import time

RED, GREEN = (255, 0, 0), (0, 255, 0)
MOVE_TO_DIRECTION = {"turn_left": "left", "turn_right": "right", "forward": "forward", "backward": "backward"}
TURN_DIRECTION = "left"
SONAR_CLEARANCE_CM = 1.0  # integer sonar reports: stop one centimetre early
SAMPLE_DISTANCE_CM = 10.0
RELOCATE_MAX_CM = 15.0


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
        # Physical arrival always needs sonar; image size/stop-line disappearance is not arrival.
        settings.arrive_area_ratio = 2.0
        return pipeline.Planner(settings, use_distance=False, stop_line_y=None)

    def _stop_cm(self, color: str, planner) -> float:
        nav = self.vision.cfg["navigation"]
        return float(nav.get("base_distance_cm", 15)) if color == "home" else SAMPLE_DISTANCE_CM

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

    async def _settled_target(self, view):
        """Observe three distinct frames while stopped before trusting a target bearing."""
        errors = [view[0].horizontal_error]
        for _ in range(2):
            seq = self._seq()
            await asyncio.sleep(0.08)
            view = await self._fresh_view(seq)
            det = view[0]
            if det is None or not det.detected:
                return None
            errors.append(det.horizontal_error)
        if max(errors) - min(errors) > 0.18:
            return None  # competing/flickering blobs: hold instead of chasing either
        det = copy.copy(view[0])
        det.horizontal_error = statistics.median(errors)
        return det, view[1], view[2]

    def _alignment_ms(self, planner, error, previous=None):
        hfov = planner.s.camera_hfov_deg or self.vision.cfg['navigation'].get('glimpse_hfov_deg', 50)
        degrees = abs(error) * hfov / 2
        # Approach the bearing conservatively, then measure again after stopping.
        duration = max(50, min(350, round(self._turn_ms(planner, degrees) * 0.75)))
        if previous and previous[0] * error < 0:
            duration = max(50, min(duration, previous[1] // 2))
        return duration

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
        if distance is None:
            raise AutopilotError("Target approach needs a valid ultrasonic reading. Stopped; check the sensor.")
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

    # ------------------------------------------------------------ behaviors
    async def _wall_distance(self):
        """Fresh positive finite ultrasonic reading; no echo is never open space."""
        refresh = getattr(self.bridge, "refresh_distance", None)
        if refresh is None or not await refresh():
            return None
        distance = self.bridge.distance_cm
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            return None
        return float(distance) if math.isfinite(distance) and distance > 0 else None

    def _scan_step_ms(self, planner):
        """Overlap camera views and count measured degrees, rather than a fixed turn count."""
        table = planner.turn_table
        if (not table or any(not 50 <= ms <= 1000 or not math.isfinite(deg) for ms, deg in table)
                or any(b[1] <= a[1] for a, b in zip(table, table[1:]))):
            raise AutopilotError("Measure increasing turn steps from 50–1000 ms before a 360° scan.")
        hfov = planner.s.camera_hfov_deg or self.vision.cfg["navigation"].get("glimpse_hfov_deg", 50)
        # Keep the view overlap even with the largest configured search pulse.
        degrees = min(30.0, max(5.0, float(hfov) * 0.6))
        ms = max(50, min(1000, planner.s.search_turn_ms, round(self._turn_ms(planner, degrees))))
        if self._turn_deg(planner, ms) > degrees + 1:
            raise AutopilotError("The shortest turn is too large for overlapping camera views. Recalibrate turn steps.")
        return ms

    @staticmethod
    def _heading_delta(target, heading):
        return (target - heading + 180) % 360 - 180

    def _open_heading(self, samples, heading, planner):
        """Pick a nearby reachable waypoint, with the least heading change.

        A single front sonar cannot map a globally nearest region. Every sampled ray
        offers a waypoint at most 15 cm away, provided wall clearance and visible floor
        permit a safe pulse. Prefer the closest bearing, then the greater clearance.
        """
        valid = [(bearing, distance) for bearing, distance, floor_ok in samples
                 if distance is not None and floor_ok
                 and distance > planner.s.wall_distance_cm + SONAR_CLEARANCE_CM]
        reachable = []
        for bearing, distance in valid:
            try:
                self._fitting_forward_ms(planner, min(RELOCATE_MAX_CM,
                    distance - planner.s.wall_distance_cm - SONAR_CLEARANCE_CM))
            except AutopilotError:
                continue
            reachable.append((bearing, distance))
        if not reachable:
            if not any(distance is not None for _, distance, _ in samples):
                raise AutopilotError("Search needs a valid wall-distance reading. Stopped; check the ultrasonic sensor.")
            raise AutopilotError("Full scan complete; no reachable clear opening. Reposition the rover, then search again.")
        return min(reachable, key=lambda item: (abs(self._heading_delta(item[0], heading)), -item[1]))[0]

    async def go_to(self, color: str, avoid: bool = True, max_steps: int | None = None) -> bool:
        """Scan → short sonar-checked relocation → repeat; approach a stable color by sonar."""
        color = "home" if color == "black" else color
        self.vision.set_target(color)
        planner = self._planner(color)
        scan_ms = self._scan_step_ms(planner)
        scan_degrees = self._turn_deg(planner, scan_ms)
        seq = self._seq()
        phase, heading, swept = "scanning", 0.0, 0.0
        samples, selected_heading = [], None
        previous_aim = None
        steps = arrivals = 0
        deadline = time.monotonic() + planner.s.explore_timeout_s
        while max_steps is None or steps < max_steps:
            if time.monotonic() >= deadline:
                raise AutopilotError("Search time limit reached. Stopped; reposition or start a new run.")
            steps += 1
            view = await self._fresh_view(seq)
            detected = view[0] is not None and view[0].detected
            if detected:
                self.report("approaching", f"{color.title()} seen. Holding still to confirm alignment.")
                settled = await self._settled_target(view)
                if settled is not None:
                    view = settled
                else:
                    # A flickering blob must not pin the rover in place forever.
                    detected = False
            det, free, free_obstacles = view
            if detected:
                phase = "approaching"
                swept, samples, selected_heading = 0.0, [], None
                planner._enter("APPROACH")
                error = det.horizontal_error
                if abs(error) > planner.s.aim_deadband:
                    arrivals = 0
                    ms = self._alignment_ms(planner, error, previous_aim)
                    previous_aim = (error, ms)
                    self.report("approaching", f"{color.title()} found. Aligning before approaching to 10 cm.")
                    await self._step("turn_left" if error < 0 else "turn_right", planner.s.turn_pwm, ms)
                else:
                    previous_aim = None
                    path = free_obstacles if color == "home" and free_obstacles is not None else free
                    if avoid and path is not None and path.center_blocked and not planner._clear_to_target(det, path):
                        raise AutopilotError("Target found, but the path is blocked. Stopped; reposition and search again.")
                    action = await self._ultrasonic_arrival(color, planner,
                        planner._move("forward", min(1000, max(50, planner.s.forward_ms)), "Approaching the selected color."))
                    self.report("approaching", f"{color.title()} found. {action.reason}")
                    if action.kind == "arrived":
                        if await self.bridge.stop() is not True:
                            raise AutopilotError("Arrival STOP was not acknowledged.")
                        arrivals += 1
                        if arrivals >= 3:
                            self.report("arrived", f"Stopped at {color}. {action.reason}")
                            return True
                    else:
                        arrivals = 0
                        await self._step("forward", action.pwm, action.duration_ms)
                seq = self._seq()
                continue

            arrivals, previous_aim = 0, None
            if phase == "approaching":
                self.report("scanning", f"Lost {color}. Starting a new 360° scan while stopped between turns.")
                phase, heading, swept, samples = "scanning", 0.0, 0.0, []
            floor_ok = free is not None and free.floor_visible and not free.center_blocked
            if phase == "scanning":
                distance = await self._wall_distance()
                samples.append((heading % 360, distance, floor_ok))
                if swept < 360:
                    self.report("scanning", f"Searching for {color}: 360° scan {min(360, round(swept))}/360°. Checking every direction.")
                    await self._step("turn_right", planner.s.turn_pwm, scan_ms)
                    heading += scan_degrees
                    swept += scan_degrees
                    seq = self._seq()
                    continue
                selected_heading = self._open_heading(samples, heading, planner)
                phase = "relocating"
                self.report("relocating", f"Full 360° scan complete; {color} not found. Moving toward a nearby sonar-clear opening.")

            delta = self._heading_delta(selected_heading, heading)
            if abs(delta) > max(1.0, self._turn_deg(planner, 50) / 2):
                ms = max(50, min(scan_ms, round(self._turn_ms(planner, abs(delta)))))
                direction = "turn_right" if delta > 0 else "turn_left"
                self.report("relocating", "Turning toward the nearest clear bearing. Rechecking space before driving.")
                await self._step(direction, planner.s.turn_pwm, ms)
                heading += self._turn_deg(planner, ms) * (1 if delta > 0 else -1)
            else:
                # Survey readings can be stale after turning. Never drive on the stored reading.
                distance = await self._wall_distance()
                if distance is None:
                    raise AutopilotError("Relocation needs a valid wall-distance reading. Stopped; check the ultrasonic sensor.")
                room = distance - planner.s.wall_distance_cm - SONAR_CLEARANCE_CM
                if not floor_ok or room <= 0:
                    self.report("scanning", "Opening is no longer clear. Holding position and scanning again.")
                else:
                    ms = self._fitting_forward_ms(planner, min(RELOCATE_MAX_CM, room))
                    self.report("relocating", f"{color.title()} not found. Ultrasonic {distance:.0f} cm; taking one short step into clear space, then scanning again.")
                    await self._step("forward", planner.s.drive_pwm, min(1000, ms))
                phase, heading, swept, samples, selected_heading = "scanning", 0.0, 0.0, [], None
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
        self.report("awaiting_pickup", f"Stopped about 10 cm from {color}. Pick up the material by hand, then bring it back to home base and confirm completion.")
        return True

    async def return_to_base(self, avoid: bool = True) -> bool:
        """Reacquire the home flag, stop at the wall, and reset the LEDs to red."""
        # Reacquire the actual flag from the current heading; no blind 180-degree assumption.
        await self.go_to("home", avoid=avoid)
        await self._led(RED)
        self.report("home_arrived", "Stopped at the black home flag. Mission complete; LEDs red.")
        return True

    async def mission(self, color: str, avoid: bool = True, pause_s: float = 3.0) -> bool:
        """Reach the selected material and stay stopped for human pickup and return.

        ``pause_s`` is accepted for older callers; there is no timed automatic departure.
        """
        return await self.collect(color, avoid=avoid)
