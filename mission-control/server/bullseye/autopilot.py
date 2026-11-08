"""Simple autonomy: find the selected color, stop about 15 cm in front of it, hand over.

    1. One 360° scan: turn right in measured steps, checking the camera after each one and
       recording an ultrasonic reading for every heading. This is the only full turn.
    2. Color seen -> approach: turn to center it, step forward, stop about 15 cm away.
    3. Not seen -> face the heading with the most ultrasonic room and explore: drive forward
       (camera and sonar checked between steps) until a wall is near. A close wall means the
       sample is probably not that way, so check left and right with the sonar (camera still
       watching while turning), face the more open side and keep driving. Never another 360.
    4. Lost the sample during an approach -> look toward the side it was last seen, then
       keep exploring.
    5. On arrival autonomy ends: the pilot drives up, squeezes the gripper and brings the
       sample home manually.

Every move is one bounded timed pulse (STOP before and after) followed by fresh camera
frames. There are no encoders or map; distances come from the measured step table.
"""
import asyncio
import math
import time

RED, GREEN = (255, 0, 0), (0, 255, 0)
NAMES = {"red": "ruby", "green": "emerald"}   # what the operator sees

STOP_CM = 15.0          # final gap in front of the sample
SONAR_MARGIN_CM = 1.0   # the sonar reports whole centimetres: stop one centimetre early, never short of 15
STOP_BUFFER_CM = 0.0    # optional extra leeway on top of STOP_CM (slider "Stop early by")
STEP_SHARE = 0.6        # near the end a step covers at most this share of the remaining room (step-table error)
FAR_STEP_CM = 15.0      # longest forward step when there is lots of room (one 1 s pulse)
NEAR_STEP_CM = 7.5      # forward step once the sample is within FAR_GAP_CM or low in the image
FINAL_STEP_CM = 2.7     # forward step when the sample is almost at the bottom of the image
FAR_GAP_CM = 35.0       # approach gaps above this use FAR_STEP_CM
SCAN_STEP_MAX_DEG = 40  # scan turn size, capped so camera views still overlap
SCAN_OVERLAP = 0.75     # scan step = this share of the camera's horizontal view
SIDE_DEG = 90           # at a wall, check this far to the left and to the right
OPEN_ENOUGH_CM = 100    # a side this open is taken without checking the other side
LOST_PEEK_DEG = 60      # after losing the sample, look this far toward where it was last seen
DEAD_ENDS_MAX = 3       # wall checks in a row with no forward progress = boxed in
ARRIVE_BOTTOM_Y = 0.85  # sample's bottom edge this low in the image = right in front of the rover
SEARCH_LIMIT_S = 600    # safety net only: the search otherwise continues until STOP


class AutopilotError(RuntimeError):
    """The run could not finish (no camera, no sonar, blocked path). Not a hardware fault."""


def _table(raw) -> list:
    """{"200": 2.7, ...} -> [(0, 0.0), (200, 2.7), ...] sorted, positive values only."""
    rows = []
    for ms, value in (raw or {}).items():
        try:
            ms, value = int(ms), float(value)
        except (TypeError, ValueError):
            continue
        if 50 <= ms <= 1000 and math.isfinite(value) and value > 0:
            rows.append((ms, value))
    return [(0, 0.0)] + sorted(rows)


def _interp(table, x, src, dst):
    """Piecewise-linear lookup in `table` from column `src` to column `dst`, extrapolating the last segment."""
    for a, b in zip(table, table[1:]):
        if x <= b[src]:
            return a[dst] + (b[dst] - a[dst]) * (x - a[src]) / (b[src] - a[src])
    a, b = table[-2], table[-1]
    return b[dst] + (b[dst] - a[dst]) * (x - b[src]) / (b[src] - a[src])


def _wrap(deg):
    return (deg + 180) % 360 - 180


class Autopilot:
    def __init__(self, bridge, vision, report=lambda state, message: None):
        self.bridge, self.vision, self.report = bridge, vision, report
        nav = getattr(vision, "cfg", {}).get("navigation", {})
        steps = nav.get("steps", {})
        self.turn_table = _table(steps.get("turn"))
        self.forward_table = _table(steps.get("forward"))
        self.turn_pwm = int(nav.get("turn_pwm", 60))
        self.drive_pwm = int(nav.get("drive_pwm", 60))
        self.deadband = float(nav.get("aim_deadband", 0.12))
        self.hfov = float(nav.get("camera_hfov_deg") or nav.get("glimpse_hfov_deg") or 50)
        self.wall_cm = float(nav.get("wall_distance_cm", 25))
        self.arrive_bottom_y = float(nav.get("arrive_bottom_y", ARRIVE_BOTTOM_Y))
        self.stop_buffer = max(0.0, float(nav.get("stop_buffer_cm", STOP_BUFFER_CM)))
        self.deadline = math.inf
        self.first_side = "left"           # alternate which side is checked first at walls
        self.obstacles = None              # blue-obstacle check from the latest looked-at frame

    # ------------------------------------------------------------ step table
    def turn_deg(self, ms):
        return _interp(self.turn_table, ms, 0, 1)

    def turn_ms(self, deg):
        return _interp(self.turn_table, deg, 1, 0)

    def forward_cm(self, ms):
        return _interp(self.forward_table, ms, 0, 1)

    def forward_ms(self, cm):
        return _interp(self.forward_table, cm, 1, 0)

    def _check_tables(self):
        if len(self.turn_table) < 2 or len(self.forward_table) < 2:
            raise AutopilotError("Measure the turn and forward steps first (Calibration → Steps).")

    def _scan_step_ms(self):
        deg = min(SCAN_STEP_MAX_DEG, self.hfov * SCAN_OVERLAP)
        return max(50, min(1000, round(self.turn_ms(deg))))

    # ------------------------------------------------------------ moves and sensors
    async def _move(self, direction, ms, pwm):
        if time.monotonic() > self.deadline:
            raise AutopilotError("Search safety limit reached. Stopped; start a new search when ready.")
        await self.bridge.pulse(direction, max(1, min(100, pwm)), max(50, min(1000, int(round(ms)))))

    async def _turn(self, degrees):
        """Blind turn by `degrees` (+ = right) in pulses of at most one second."""
        direction = "right" if degrees > 0 else "left"
        remaining = abs(degrees)
        while remaining > max(1.0, self.turn_deg(50) / 2):
            ms = max(50, min(1000, self.turn_ms(remaining)))
            await self._move(direction, ms, self.turn_pwm)
            remaining -= self.turn_deg(ms)

    async def _forward(self, cm):
        """One forward pulse of about `cm` (at most one second). Returns the distance driven, 0 if too short."""
        ms = min(1000, self.forward_ms(cm))
        if ms < 50:
            return 0.0
        await self._move("forward", ms, self.drive_pwm)
        return self.forward_cm(ms)

    async def _sonar(self, retries=1):
        """Fresh ultrasonic distance in cm, or None (no echo is unknown, never open space)."""
        refresh = getattr(self.bridge, "refresh_distance", None)
        for _ in range(1 + retries):
            if refresh is not None and await refresh():
                d = self.bridge.distance_cm
                if not isinstance(d, bool) and isinstance(d, (int, float)) and math.isfinite(d) and d > 0:
                    return float(d)
        return None

    async def _led(self, rgb):
        setter = getattr(self.bridge, "set_led", None)
        if setter is not None and await setter(rgb) is not True:
            raise AutopilotError("LED/STOP command was not acknowledged. Motion halted.")

    # ------------------------------------------------------------ camera
    def _seq(self):
        latest = self.vision.latest
        return latest["seq"] if latest else -1

    async def _frame(self, timeout=3.0):
        """Detection and blue-obstacle check from a frame captured after the last move ended."""
        after = self._seq()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._seq() >= after + 2:        # skip a frame that may have been mid-capture
                with self.vision.lock:
                    return self.vision._det, getattr(self.vision.nav, "free_obstacles", None)
            await asyncio.sleep(0.02)
        raise AutopilotError("No fresh camera frame. Check the camera and Wi-Fi.")

    async def _look(self, color, confirm=True):
        """(the target's Detection or None, obstacle check). With `confirm` a sighting must repeat in the next frame."""
        det, obstacles = await self._frame()
        seen = det is not None and det.detected and det.target_color == color
        if seen and confirm:
            again, obstacles = await self._frame()
            seen = again is not None and again.detected and again.target_color == color
            det = again if seen else det
        self.obstacles = obstacles
        return (det if seen else None), obstacles

    @staticmethod
    def _blocked(obstacles):
        """Only blue obstacles block in the camera; walls are the sonar's job."""
        return obstacles is not None and obstacles.center_blocked

    async def _swing(self, color, degrees):
        """Turn by `degrees` (+ = right) in scan-sized steps, looking after each. Returns a sighting or None."""
        step = self._scan_step_ms()
        direction = "right" if degrees > 0 else "left"
        remaining = abs(degrees)
        while remaining > max(1.0, self.turn_deg(50) / 2):
            ms = max(50, min(step, round(self.turn_ms(remaining))))
            await self._move(direction, ms, self.turn_pwm)
            remaining -= self.turn_deg(ms)
            det, _ = await self._look(color)
            if det is not None:
                return det
        return None

    # ------------------------------------------------------------ behaviors
    async def scan(self, color):
        """The one 360°. Returns (detection or None, [(heading_deg, sonar_cm), ...], heading turned)."""
        step_ms = self._scan_step_ms()
        step_deg = self.turn_deg(step_ms)
        readings, heading = [], 0.0
        while heading < 360:
            self.report("scanning", f"Scanning for the {NAMES[color]}: {min(360, round(heading))}/360°.")
            det, _ = await self._look(color)
            if det is not None:
                return det, readings, heading
            readings.append((heading, await self._sonar(retries=0)))
            await self._move("right", step_ms, self.turn_pwm)
            heading += step_deg
        return None, readings, heading

    def open_heading(self, readings):
        """Heading with the most ultrasonic room."""
        valid = [r for r in readings if r[1] is not None]
        if not valid:
            raise AutopilotError("The ultrasonic sensor gave no readings during the scan. Stopped; check the sensor.")
        best = max(valid, key=lambda r: r[1])
        if best[1] <= self.wall_cm:
            raise AutopilotError("No open space in any direction. Reposition the rover, then search again.")
        return best

    async def explore(self, color):
        """Drive toward open space until a wall is near. Returns (sighting or None, cm driven)."""
        driven = 0.0
        name = NAMES[color]
        while True:
            det, obstacles = await self._look(color)
            if det is not None:
                return det, driven
            distance = await self._sonar()
            if distance is None or self._blocked(obstacles):
                return None, driven
            room = distance - self.wall_cm
            moved = await self._forward(min(room, FAR_STEP_CM)) if room > 0 else 0.0
            if not moved:
                return None, driven             # wall close: the sample is probably not this way
            driven += moved
            self.report("relocating", f"No {name} in sight. Ultrasonic {distance:.0f} cm; driving toward open space.")

    async def pick_open_side(self, color):
        """At a wall: check both sides with the sonar (camera watching), face the more open one.

        Returns (sighting or None, room cm of the chosen side). A side that is very open is
        taken without checking the other; if both are walls the rover turns around.
        """
        first = self.first_side
        self.first_side = "right" if first == "left" else "left"
        sign = 1 if first == "right" else -1
        self.report("relocating", f"Wall ahead. Checking {first} and the other side for open space.")
        det = await self._swing(color, sign * SIDE_DEG)
        if det is not None:
            return det, None
        first_cm = await self._sonar() or 0.0
        if first_cm >= OPEN_ENOUGH_CM:
            return None, first_cm
        det = await self._swing(color, -sign * 2 * SIDE_DEG)
        if det is not None:
            return det, None
        second_cm = await self._sonar() or 0.0
        if max(first_cm, second_cm) <= self.wall_cm:
            await self._turn(-sign * SIDE_DEG)  # both sides walled: face back the way we came
            return None, 0.0
        if first_cm > second_cm:
            await self._turn(sign * 2 * SIDE_DEG)
            return None, first_cm
        return None, second_cm

    async def reacquire(self, color, last):
        """The sample dropped out of view mid-approach: look toward the side it was last seen."""
        side = 1 if (last.horizontal_error or 0) >= 0 else -1
        self.report("scanning", f"Lost the {NAMES[color]}. Looking where it was last seen.")
        det = await self._swing(color, side * LOST_PEEK_DEG)
        if det is None:
            det = await self._swing(color, -side * 2 * LOST_PEEK_DEG)
        return det

    def _step_for(self, det, gap):
        """Forward step size: long when far and high in the image, short as the sample gets close."""
        low = det.bottom_y is not None and det.bottom_y >= self.arrive_bottom_y - 0.15
        high = det.bottom_y is None or det.bottom_y < self.arrive_bottom_y - 0.25
        step = FINAL_STEP_CM if low else FAR_STEP_CM if high and (gap is None or gap > FAR_GAP_CM) else NEAR_STEP_CM
        if gap is not None:
            # Never aim past the buffered stop point, and near the end cover only part of the
            # remaining room, so a step that runs long (battery, floor) still stops short.
            room = gap - STOP_CM - self.stop_buffer - SONAR_MARGIN_CM
            step = min(step, max(FINAL_STEP_CM, STEP_SHARE * room), room)
        return step

    async def approach(self, color, det):
        """Center the target and step forward until about 15 cm away. Returns (arrived, last detection)."""
        name = NAMES[color]
        misses, last = 0, det
        while True:
            if det is None:
                misses += 1
                if (misses >= 2 and last.bottom_y is not None and last.bottom_y >= self.arrive_bottom_y - 0.1
                        and abs(last.horizontal_error) <= self.deadband * 2):
                    return True, last           # it slid under the camera, right in front of the gripper
                if misses >= 3:
                    return False, last
                det, _ = await self._look(color, confirm=False)
                continue
            misses, last = 0, det
            error = det.horizontal_error
            if abs(error) > self.deadband:
                degrees = abs(error) * self.hfov / 2 * 0.8   # undershoot, then look again
                self.report("approaching", f"{name.title()} found. Turning to center it.")
                await self._move("right" if error > 0 else "left", max(50, min(400, self.turn_ms(degrees))), self.turn_pwm)
            else:
                gap = await self._gap(det)
                if gap is not None and gap - SONAR_MARGIN_CM <= STOP_CM + self.stop_buffer:
                    return True, det
                if self._blocked(self.obstacles):
                    raise AutopilotError(f"An obstacle is between the rover and the {name}. Stopped; drive around it manually.")
                where = "distance unknown" if gap is None else f"about {gap:.0f} cm away"
                self.report("approaching", f"{name.title()} centered, {where}. Driving closer.")
                if not await self._forward(self._step_for(det, gap)):
                    return True, det            # closer than the smallest safe step
            det, _ = await self._look(color, confirm=False)

    async def _gap(self, det):
        """Closest of the sonar reading and the camera's ground estimate; STOP_CM once the sample is at the bottom."""
        if det.bottom_y is not None and det.bottom_y >= self.arrive_bottom_y:
            return STOP_CM
        readings = [d for d in (await self._sonar(retries=0), det.distance_cm_est) if d is not None and d > 0]
        return min(readings) if readings else None

    async def find(self, color):
        """One 360° scan, then explore by sonar until the color is found; stop about 15 cm in front of it."""
        self._check_tables()
        self.vision.set_target(color)
        self.deadline = time.monotonic() + SEARCH_LIMIT_S
        name = NAMES[color]
        det, readings, heading = await self.scan(color)
        if det is None:
            bearing, distance = self.open_heading(readings)
            self.report("relocating", f"Full scan done; no {name} in sight. Heading for the most open space ({distance:.0f} cm).")
            await self._turn(_wrap(bearing - heading))
        dead_ends = 0
        while True:
            if det is not None:
                arrived, last = await self.approach(color, det)
                if arrived:
                    if await self.bridge.stop() is not True:
                        raise AutopilotError("Arrival STOP was not acknowledged.")
                    return True
                det = await self.reacquire(color, last)
                continue
            det, driven = await self.explore(color)
            if det is not None:
                continue
            dead_ends = 0 if driven > 0 else dead_ends + 1
            if dead_ends >= DEAD_ENDS_MAX:
                raise AutopilotError("Walls on every side. Stopped; reposition the rover, then search again.")
            det, _ = await self.pick_open_side(color)

    async def mission(self, color, avoid=True):
        """Find the sample, stop about 15 cm away, and hand over to the pilot for pickup and return."""
        if color not in NAMES:
            raise AutopilotError("Choose the ruby or the emerald. Blue is an obstacle.")
        await self._led(RED)
        await self.find(color)
        await self._led(GREEN)
        self.report("manual_pickup", f"Stopped about 15 cm from the {NAMES[color]}. Drive up, squeeze to grab it, "
                                     "drive it home, then press Delivered.")
        return True
