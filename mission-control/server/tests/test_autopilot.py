"""Simple autopilot in a tiny simulated world: real vision pipeline, fake rover bridge."""
import asyncio
import copy
import json
import math

import cv2
import numpy as np
import pytest

from bullseye.autopilot import Autopilot, AutopilotError
from bullseye.bench_demo import BenchControl
from bullseye.camera import CameraHub
from bullseye.config import Settings
from bullseye.mission import CommandError
from bullseye.vision import VisionService, _import_vision


def frame(block_x=None, size=60, bottom=420, color=(30, 30, 220)):
    img = np.full((600, 800, 3), 235, np.uint8)                  # white floor
    img[:200] = (90, 136, 176)                                    # cardboard wall
    if block_x is not None:
        cv2.rectangle(img, (int(block_x - size / 2), int(bottom - size)), (int(block_x + size / 2), int(bottom)),
                      color, -1)                                  # red block (or another color)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


@pytest.fixture
def vision(tmp_path):
    cfg = copy.deepcopy(_import_vision().config.DEFAULTS)
    cfg["navigation"]["steps"] = {"forward": {"200": 2.7, "500": 7.5, "1000": 15.0}, "turn": {"200": 8.3, "500": 20.9, "1000": 49.0}}
    cfg["navigation"]["search_turn_ms"] = 1000
    cfg["camera"]["controls"], cfg["rover_mask"] = {}, {"boxes": []}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    settings = Settings(vision_enabled=True, vision_dir=tmp_path)
    return VisionService(settings, CameraHub(settings))


class World:
    """Fake bridge: each timed step moves the block in the next camera frame."""

    connected, status, error, distance_cm, heartbeats, last_stop_acknowledged = True, "ready", None, None, 0, True

    def __init__(self, vision, block_x=200.0, size=60.0, visible=True, wall_cm=150):
        self.vision, self.x, self.size, self.bottom, self.visible = vision, block_x, size, 420.0, visible
        self.steps, self.seq, self.wall_cm = [], 0, wall_cm
        self.show()

    async def drive(self, coro):
        """Run the autopilot while the 'camera' keeps streaming frames, like the real relay."""
        async def camera():
            while True:
                self.show()
                await asyncio.sleep(0.01)
        streaming = asyncio.create_task(camera())
        try:
            return await asyncio.wait_for(coro, timeout=10)
        finally:
            streaming.cancel()

    def show(self):
        self.seq += 1
        self.vision._process(frame(self.x if self.visible else None, self.size, self.bottom,
                                   getattr(self, "color", (30, 30, 220))), self.seq)

    async def pulse(self, direction, speed, duration_ms):
        assert 1 <= speed <= 100 and 50 <= duration_ms <= 1000
        self.steps.append((direction, duration_ms))
        if direction == "left":
            self.x += duration_ms * 0.4                            # turning left moves the view's content right
        elif direction == "right":
            self.x -= duration_ms * 0.4
        elif direction == "forward":
            if self.wall_cm is not None:
                self.wall_cm -= duration_ms * 0.015
            self.size = min(260, self.size + duration_ms * 0.15)
            self.bottom = min(590, self.bottom + duration_ms * 0.05)

    async def stop(self):
        return True

    async def refresh_distance(self, timeout=2):
        self.distance_cm = self.wall_cm
        return True


class SonarWorld(World):
    """The ultrasonic sees the target: 80 cm away at the start, closer with every forward step."""

    def __init__(self, vision, color=(30, 30, 220)):
        self.color = color
        super().__init__(vision, block_x=400, size=40)
        self.bottom = 330.0
        self.cm, self.leds = 80.0, []

    async def pulse(self, direction, speed, duration_ms):
        await super().pulse(direction, speed, duration_ms)
        if direction == "forward":
            self.cm -= Autopilot(None, self.vision).forward_cm(duration_ms)
            self.size, self.bottom = 40 * 80 / self.cm, 330.0          # bigger, but never at the bottom edge

    async def refresh_distance(self, timeout=2):
        self.distance_cm = round(self.cm)
        return True

    async def set_led(self, rgb=None):
        self.leds.append(tuple(rgb))
        return True


class ArenaWorld(World):
    """A 200 × 100 cm room seen from above (x east, y south; a right turn adds to the heading).

    The sample sits in the far corner, hidden behind an inner wall until the rover is past
    x = 150 cm. The sonar sees the room walls, and the sample itself when facing it.
    """

    ROOM, SAMPLE, HIDDEN_UNTIL_X, HFOV = (200.0, 100.0), (185.0, 12.0), 150.0, 50.0

    def __init__(self, vision, start=(30.0, 20.0), heading=270.0):
        self.pos, self.heading = list(start), heading
        self.pilot = Autopilot(None, vision)
        super().__init__(vision, wall_cm=None)

    def _sample(self):
        dx, dy = self.SAMPLE[0] - self.pos[0], self.SAMPLE[1] - self.pos[1]
        offset = (math.degrees(math.atan2(dy, dx)) - self.heading + 180) % 360 - 180
        return math.hypot(dx, dy), offset

    def show(self):
        distance, offset = self._sample()
        self.visible = self.pos[0] > self.HIDDEN_UNTIL_X and abs(offset) < self.HFOV / 2 - 3
        self.x = 400 + offset / (self.HFOV / 2) * 400
        self.size, self.bottom = max(20.0, min(200.0, 60 * 40 / max(distance, 1))), 330.0
        super().show()

    async def pulse(self, direction, speed, duration_ms):
        self.steps.append((direction, duration_ms))
        if direction in ("left", "right"):
            self.heading += self.pilot.turn_deg(duration_ms) * (1 if direction == "right" else -1)
        elif direction == "forward":
            cm = self.pilot.forward_cm(duration_ms)
            self.pos[0] += cm * math.cos(math.radians(self.heading))
            self.pos[1] += cm * math.sin(math.radians(self.heading))
            assert 0 < self.pos[0] < self.ROOM[0] and 0 < self.pos[1] < self.ROOM[1], "drove into a wall"

    async def refresh_distance(self, timeout=2):
        c, s = math.cos(math.radians(self.heading)), math.sin(math.radians(self.heading))
        hits = [((self.ROOM[0] if c > 0 else 0) - self.pos[0]) / c if abs(c) > 1e-6 else math.inf,
                ((self.ROOM[1] if s > 0 else 0) - self.pos[1]) / s if abs(s) > 1e-6 else math.inf]
        distance, offset = self._sample()
        if abs(offset) < 5:
            hits.append(distance)
        self.distance_cm = round(min(150, min(hits)))
        return True


class RoomWorld(World):
    """Target hidden until the rover has driven toward open space; sonar depends on the heading."""

    def __init__(self, vision, open_heading=90.0, reveal_after_forward=True):
        super().__init__(vision, block_x=400, visible=False, wall_cm=None)
        self.pilot = Autopilot(None, vision)
        self.heading, self.open_heading, self.reveal = 0.0, open_heading, reveal_after_forward

    async def pulse(self, direction, speed, duration_ms):
        self.steps.append((direction, duration_ms))
        if direction in ("left", "right"):
            self.heading += self.pilot.turn_deg(duration_ms) * (1 if direction == "right" else -1)
        elif direction == "forward" and self.reveal:
            self.visible, self.bottom, self.size = True, 560.0, 120.0   # sample right in front now

    async def refresh_distance(self, timeout=2):
        delta = abs((self.heading - self.open_heading + 180) % 360 - 180)
        self.distance_cm = 120 if delta < 25 else 30
        return True


def reports_of(log):
    return lambda state, message: log.append(state)


def test_turn_and_forward_tables_round_trip(vision):
    pilot = Autopilot(None, vision)
    for deg in (5, 20.9, 30, 49):
        assert abs(pilot.turn_deg(pilot.turn_ms(deg)) - deg) < 0.5
    for cm in (2.7, 5, 15):
        assert abs(pilot.forward_cm(pilot.forward_ms(cm)) - cm) < 0.1


def test_mission_scans_approaches_and_stops_10_cm_away(vision):
    world, reports = SonarWorld(vision), []
    assert asyncio.run(world.drive(Autopilot(world, vision, reports_of(reports)).mission("red")))
    assert 20 <= world.cm <= 23                                     # about 20 cm, within one small step
    assert world.leds == [(255, 0, 0), (0, 255, 0)]
    assert reports[-1] == "manual_pickup" and "approaching" in reports
    assert vision.target == "red"


@pytest.mark.parametrize("overshoot", [1.0, 1.4])
def test_never_gets_closer_than_10_cm_even_if_steps_run_long(vision, overshoot):
    class LongSteps(SonarWorld):
        """Every forward pulse travels `overshoot` × the step table (low battery, slippery floor)."""
        async def pulse(self, direction, speed, duration_ms):
            before = self.cm
            await super().pulse(direction, speed, duration_ms)
            self.cm = before - (before - self.cm) * overshoot
            self.closest = min(getattr(self, "closest", self.cm), self.cm)

    world = LongSteps(vision)
    assert asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    assert world.closest >= 20 and world.cm <= 25


def test_stop_buffer_is_configurable(vision):
    vision.cfg["navigation"]["stop_buffer_cm"] = 3
    world = SonarWorld(vision)
    assert asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    assert 23 <= world.cm <= 26                                     # 20 cm + 3 cm extra leeway


def test_off_center_target_is_centered_before_driving(vision):
    world = SonarWorld(vision)
    world.x = 200.0                                                 # target on the left
    assert asyncio.run(world.drive(Autopilot(world, vision).mission("red")))
    first_forward = next(i for i, (d, _) in enumerate(world.steps) if d == "forward")
    assert "left" in [d for d, _ in world.steps[:first_forward]]


def test_sample_below_the_sonar_beam_arrives_at_the_bottom_of_the_view(vision):
    world = World(vision, block_x=400, wall_cm=150)                 # sonar only sees the far wall
    assert asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    assert world.bottom / 600 >= 0.85 and world.wall_cm > 100


def test_full_360_scan_then_drives_toward_the_most_open_space(vision):
    world, reports = RoomWorld(vision, open_heading=90.0), []
    pilot = Autopilot(world, vision, reports_of(reports))
    assert asyncio.run(world.drive(pilot.find("red")))
    first_forward = next(i for i, (d, _) in enumerate(world.steps) if d == "forward")
    scanned = sum(pilot.turn_deg(ms) for d, ms in world.steps[:first_forward] if d == "right")
    assert scanned >= 360                                           # every direction checked first
    assert abs((world.heading - 90 + 180) % 360 - 180) < 25         # then faced the open side
    assert "relocating" in reports


def test_sample_hidden_behind_a_wall_is_found_with_only_one_360(vision):
    world, messages = ArenaWorld(vision), []
    pilot = Autopilot(world, vision, lambda state, message: messages.append((state, message)))
    assert asyncio.run(world.drive(pilot.find("red")))
    distance, _ = world._sample()
    assert 19 <= distance <= 25                                     # stopped about 20 cm from it, never touching
    first_explore = next(i for i, (state, _) in enumerate(messages) if state == "relocating")
    assert not any("/360°" in message for _, message in messages[first_explore:])   # never a second 360
    assert any("Wall ahead" in message for _, message in messages)  # it checked the sides at the wall


def test_search_keeps_exploring_without_another_360(vision, monkeypatch):
    monkeypatch.setattr("bullseye.autopilot.SEARCH_LIMIT_S", 1.0)
    world, messages = RoomWorld(vision, reveal_after_forward=False), []    # never found: only the safety net ends it
    with pytest.raises(AutopilotError, match="safety limit"):
        asyncio.run(world.drive(Autopilot(world, vision, lambda s, m: messages.append(m)).find("red")))
    assert len([ms for d, ms in world.steps if d == "forward"]) >= 2
    first_explore = next(i for i, m in enumerate(messages) if "Full scan done" in m)
    assert not any("/360°" in m for m in messages[first_explore:])


def test_at_a_wall_it_turns_to_the_more_open_side(vision):
    class SideWorld(RoomWorld):
        """Facing 0° after the scan: a wall 30 cm ahead; left (−90°) 40 cm, right (+90°) 120 cm."""
        def __init__(self, vision):
            super().__init__(vision, open_heading=0.0, reveal_after_forward=False)
            self.ahead = 30.0

        async def pulse(self, direction, speed, duration_ms):
            await super().pulse(direction, speed, duration_ms)
            if direction == "forward" and abs((self.heading + 180) % 360 - 180) < 25:
                self.ahead -= self.pilot.forward_cm(duration_ms)
            elif direction == "forward":
                self.visible, self.bottom, self.size = True, 560.0, 120.0   # found once it went the open way

        async def refresh_distance(self, timeout=2):
            h = (self.heading + 180) % 360 - 180
            self.distance_cm = round(self.ahead if abs(h) < 25 else 40 if abs(h + 90) < 25 else 120 if abs(h - 90) < 25 else 20)
            return True

    world = SideWorld(vision)
    world.heading = 0.0
    assert asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    assert abs((world.heading - 90 + 180) % 360 - 180) < 25         # right was more open


def test_boxed_in_stops_after_a_few_wall_checks(vision):
    world = World(vision, visible=False, wall_cm=30)                # 5 cm of room past the 25 cm wall margin
    with pytest.raises(AutopilotError, match="Walls on every side"):
        asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    forward = [ms for d, ms in world.steps if d == "forward"]
    assert len(forward) == 1 and world.wall_cm >= 24.5              # never drove into the wall


def test_far_approach_uses_long_steps_then_short_ones(vision):
    world = SonarWorld(vision)
    assert asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    forward = [ms for d, ms in world.steps if d == "forward"]
    assert forward[0] == 1000 and forward[-1] < 1000                # 15 cm steps when far, smaller at the end
    assert len(forward) <= 8                                        # 80 cm → 20 cm in a handful of steps


def test_no_echo_never_counts_as_open_space(vision):
    world = World(vision, visible=False, wall_cm=None)
    with pytest.raises(AutopilotError, match="no readings"):
        asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    assert not any(d == "forward" for d, _ in world.steps)


def test_walls_all_around_stops_instead_of_driving(vision):
    world = World(vision, visible=False, wall_cm=12)
    with pytest.raises(AutopilotError, match="No open space"):
        asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    assert not any(d == "forward" for d, _ in world.steps)


def test_single_frame_flash_is_not_a_sighting(vision, monkeypatch):
    monkeypatch.setattr("bullseye.autopilot.SEARCH_LIMIT_S", 0.5)

    class FlashWorld(RoomWorld):
        async def pulse(self, direction, speed, duration_ms):
            await super().pulse(direction, speed, duration_ms)
            if direction == "right" and len(self.steps) == 1:
                self.visible = True
                self.show()
                self.visible = False

    world, reports = FlashWorld(vision, reveal_after_forward=False), []
    with pytest.raises(AutopilotError):
        asyncio.run(world.drive(Autopilot(world, vision, reports_of(reports)).find("red")))
    assert "approaching" not in reports


def test_needs_measured_steps_before_moving(vision):
    vision.cfg["navigation"]["steps"]["forward"] = {}
    world = World(vision, block_x=400)
    with pytest.raises(AutopilotError, match="Measure"):
        asyncio.run(world.drive(Autopilot(world, vision).find("red")))
    assert world.steps == []


def test_failed_arrival_stop_never_reports_success(vision):
    world, reports = SonarWorld(vision), []

    async def stop():
        return False

    world.stop = stop
    with pytest.raises(AutopilotError, match="STOP was not acknowledged"):
        asyncio.run(world.drive(Autopilot(world, vision, reports_of(reports)).find("red")))
    assert "manual_pickup" not in reports


def test_led_failure_stops_before_handover(vision):
    world, colors, reports = SonarWorld(vision), [], []

    async def led(rgb):
        colors.append(rgb)
        return rgb != (0, 255, 0)

    world.set_led = led
    with pytest.raises(AutopilotError, match="LED/STOP"):
        asyncio.run(world.drive(Autopilot(world, vision, reports_of(reports)).mission("red")))
    assert colors == [(255, 0, 0), (0, 255, 0)] and "manual_pickup" not in reports


@pytest.mark.parametrize("color", ["blue", "yellow", "home"])
def test_only_ruby_or_emerald_can_be_searched(vision, color):
    async def run():
        bench = BenchControl(Settings(mode="hardware", bench_enabled=True), vision.camera, bridge=World(vision))
        bench.vision = vision
        await bench.command("pilot", "claim", {})
        await bench.command("pilot", "bench_arm", {})
        with pytest.raises(CommandError, match="ruby or the emerald"):
            await bench.command("pilot", "auto_mission", {"color": color, "arm_token": bench.arm_token})
        assert bench.operation is None and not bench.bridge.steps
    asyncio.run(run())


def test_bench_requires_enabled_controls(vision):
    async def run():
        bench = BenchControl(Settings(mode="hardware", bench_enabled=True), vision.camera, bridge=World(vision))
        bench.vision = vision
        await bench.command("pilot", "claim", {})
        with pytest.raises(CommandError):
            await bench.command("pilot", "auto_mission", {"color": "red", "arm_token": None})
    asyncio.run(run())


def test_pickup_handover_shows_the_happy_face_and_clears_on_stop(vision):
    bench = BenchControl(Settings(mode="hardware", bench_enabled=True), vision.camera, bridge=World(vision))
    bench._report("manual_pickup", "green LEDs")
    assert bench.snapshot()["bench"]["auto"]["expression"] == "happy"
    bench._report("stopped", "STOP")
    assert bench.snapshot()["bench"]["auto"]["expression"] == "auto"
