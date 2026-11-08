"""Demo autopilot in a tiny simulated world: real vision pipeline, fake rover bridge."""
import asyncio
import copy
import json

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
            self.size = min(260, self.size + duration_ms * 0.15)
            self.bottom = min(590, self.bottom + duration_ms * 0.05)

    async def stop(self):
        return True

    async def refresh_distance(self, timeout=2):
        self.distance_cm = self.wall_cm
        return True


def test_drives_to_the_color_and_arrives(vision):
    world = World(vision)
    assert asyncio.run(world.drive(Autopilot(world, vision).go_to("red"))) is True
    assert world.steps[0][0] == "left"                              # block starts on the left: aim first
    assert any(d == "forward" for d, _ in world.steps)


def test_turn_around_uses_measured_steps(vision):
    pilot = Autopilot(None, vision)
    plan = pilot.turn_plan(180)
    table = {int(k): v for k, v in vision.cfg["navigation"]["steps"]["turn"].items()}
    assert abs(sum(table[ms] for ms in plan) - 180) < 15           # within one small step of 180 degrees


def test_search_explores_open_floor_within_step_budget(vision):
    world = World(vision, visible=False)                          # target hidden (e.g. behind an obstacle)
    with pytest.raises(AutopilotError, match="Test step limit"):  # only the test's step cap ends it
        asyncio.run(world.drive(Autopilot(world, vision).go_to("red", max_steps=25)))
    assert world.steps[0][0] in ("left", "right")                  # a full search spin first ...
    assert any(d == "forward" for d, _ in world.steps)            # ... then drives into open floor


def test_wall_ahead_turns_instead_of_driving(vision):
    world = World(vision, visible=False, wall_cm=12)              # ultrasonic: wall 12 cm ahead
    with pytest.raises(AutopilotError):
        asyncio.run(world.drive(Autopilot(world, vision).go_to("red", max_steps=25)))
    assert not any(d == "forward" for d, _ in world.steps)


def test_bench_requires_enabled_controls(vision):
    async def run():
        bench = BenchControl(Settings(mode="hardware", bench_enabled=True), vision.camera, bridge=World(vision))
        bench.vision = vision
        await bench.command("pilot", "claim", {})
        with pytest.raises(CommandError):
            await bench.command("pilot", "auto_goto", {"color": "red", "arm_token": None})
    asyncio.run(run())


def test_turn_table_roundtrip(vision):
    pilot = Autopilot(None, vision)
    planner = pilot._planner()
    for deg in (5, 20.9, 30, 49):
        assert abs(pilot._turn_deg(planner, pilot._turn_ms(planner, deg)) - deg) < 0.5


def test_glimpse_bearing_accounts_for_the_rest_of_the_turn(vision):
    pilot = Autopilot(None, vision)
    planner = pilot._planner()
    vision.last_seen = {"seq": 10, "t": 5.0, "target": "red", "err": 0.0}
    move = {"kind": "turn_right", "ms": 1000, "start": 4.5, "end": 5.5}
    bearing = pilot._glimpse("red", 9, move, planner)               # seen centered 0.35 s into a 1 s right turn
    assert -40 < bearing < -25                                      # so it's now ~30 degrees to the left
    assert pilot._glimpse("red", 10, move, planner) is None         # already used
    assert pilot._glimpse("blue", 9, move, planner) is None         # a different color


class GlimpseWorld(World):
    """The target only flashes into view once, in a frame taken during the first search turn."""

    def __init__(self, vision):
        super().__init__(vision, block_x=400, visible=False)
        self.flashed = False

    async def pulse(self, direction, speed, duration_ms):
        await super().pulse(direction, speed, duration_ms)
        if not self.flashed and direction in ("left", "right"):
            self.flashed, self.visible = True, True
            self.show()
            self.visible = False


def test_turns_back_to_a_brief_sighting(vision):
    world = GlimpseWorld(vision)
    with pytest.raises(AutopilotError):
        asyncio.run(world.drive(Autopilot(world, vision).go_to("red", max_steps=3)))
    first, second = world.steps[0][0], world.steps[1][0]
    assert first in ("left", "right") and second == ("left" if first == "right" else "right")


class SonarWorld(World):
    """The ultrasonic sees the target: 80 cm away at the start, closer with every forward step."""

    def __init__(self, vision, color=(30, 30, 220)):
        self.color = color
        super().__init__(vision, block_x=400, size=40)
        self.cm, self.leds = 80.0, []

    async def pulse(self, direction, speed, duration_ms):
        await super().pulse(direction, speed, duration_ms)
        if direction == "forward":
            self.cm -= {200: 2.7, 500: 7.5, 1000: 15.0}.get(duration_ms, duration_ms * 0.015)
            self.size, self.bottom = 40 * 80 / self.cm, 420.0          # looks bigger as it gets closer

    async def refresh_distance(self, timeout=2):
        self.distance_cm = round(self.cm)
        return True

    async def set_led(self, rgb=None):
        self.leds.append(tuple(rgb))
        return True


def test_collect_stops_at_10_cm_by_ultrasonic_and_turns_leds_green(vision):
    world, reports = SonarWorld(vision), []
    pilot = Autopilot(world, vision, lambda state, message: reports.append(state))
    assert asyncio.run(world.drive(pilot.collect("red")))
    assert 10 <= world.cm < 12.7                                     # at 10 cm, within one smallest step
    assert world.leds == [(255, 0, 0), (0, 255, 0)] and reports[-1] == "sample_reached"


def test_return_to_base_stops_15_cm_from_the_flag_wall(vision, monkeypatch):
    world, reports = SonarWorld(vision, color=(20, 20, 20)), []      # the black flag
    pilot = Autopilot(world, vision, lambda state, message: reports.append(state))
    monkeypatch.setattr(pilot, "turn_around", lambda: asyncio.sleep(0))
    assert asyncio.run(world.drive(pilot.return_to_base()))
    assert 15 <= world.cm < 17.7 and reports[-1] == "home_arrived" and world.leds[-1] == (255, 0, 0)


def test_arrival_requires_three_fresh_centered_views_and_stop_acks(vision):
    world = World(vision, block_x=400, size=200, wall_cm=10)
    stopped_at = []

    async def stop():
        stopped_at.append(vision.latest['seq'])
        return True

    world.stop = stop
    assert asyncio.run(world.drive(Autopilot(world, vision).go_to('red')))
    assert len(stopped_at) == 3
    assert all(b >= a + 2 for a, b in zip(stopped_at, stopped_at[1:]))
    assert world.steps == []


def test_failed_arrival_stop_never_reports_success(vision):
    world, reports = World(vision, block_x=400, size=200, wall_cm=10), []

    async def stop():
        return False

    world.stop = stop
    with pytest.raises(AutopilotError, match='STOP was not acknowledged'):
        asyncio.run(world.drive(Autopilot(world, vision, lambda s, m: reports.append(s)).go_to('red')))
    assert 'arrived' not in reports
    assert world.steps == []


@pytest.mark.parametrize('distance', [None, 0])
def test_home_never_drives_forward_without_sonar(vision, distance):
    world = World(vision, block_x=400, wall_cm=distance)
    world.color = (20, 20, 20)
    with pytest.raises(AutopilotError, match='valid ultrasonic'):
        asyncio.run(world.drive(Autopilot(world, vision).go_to('home')))
    assert not any(d == 'forward' for d, _ in world.steps)


def test_no_echo_does_not_mean_open_floor(vision):
    world = World(vision, visible=False, wall_cm=None)
    with pytest.raises(AutopilotError, match='valid wall-distance'):
        asyncio.run(world.drive(Autopilot(world, vision).go_to('red', max_steps=25)))
    assert not any(d == 'forward' for d, _ in world.steps)


def test_empty_step_calibration_is_not_arrival(vision):
    vision.cfg['navigation']['steps']['forward'] = {}
    world, reports = World(vision, block_x=400, wall_cm=30), []
    with pytest.raises(AutopilotError, match='Measure forward steps'):
        asyncio.run(world.drive(Autopilot(world, vision, lambda s, m: reports.append(s)).go_to('red')))
    assert 'arrived' not in reports and not world.steps


def test_unreachable_small_step_is_not_arrival(vision):
    world, reports = World(vision, block_x=400, wall_cm=11.2), []
    with pytest.raises(AutopilotError, match='Stopped short'):
        asyncio.run(world.drive(Autopilot(world, vision, lambda s, m: reports.append(s)).go_to('red')))
    assert 'arrived' not in reports and not world.steps


def test_mission_celebrates_then_returns_home_without_gripper_actions(vision):
    world, reports = World(vision, block_x=400, size=200, wall_cm=10), []

    def report(state, message):
        reports.append(state)
        if state == 'sample_reached':
            world.color = (20, 20, 20)  # camera can now see the home flag
            world.wall_cm = 15

    pilot = Autopilot(world, vision, report)
    assert asyncio.run(world.drive(pilot.mission('red', pause_s=0)))
    assert 'sample_reached' in reports and reports[-1] == 'home_arrived'
    assert reports.index('sample_reached') < reports.index('home_arrived')
    assert vision.target == 'home' and not world.steps


def test_exploration_shortens_step_before_wall_clearance(vision):
    world = World(vision, visible=False, wall_cm=30)
    with pytest.raises(AutopilotError, match='Test step limit'):
        asyncio.run(world.drive(Autopilot(world, vision).go_to('red', max_steps=25)))
    forward = [ms for d, ms in world.steps if d == 'forward']
    assert forward and max(forward) == 200  # measured 2.7 cm, within the 4 cm available


def test_search_timeout_is_bounded_even_for_legacy_config(vision):
    vision.cfg['navigation']['explore_timeout_s'] = 0
    assert Autopilot(None, vision)._planner().s.explore_timeout_s == 120


def test_stop_during_celebration_prevents_automatic_return(vision):
    async def run():
        world = World(vision, block_x=400, size=200, wall_cm=10)
        reached = asyncio.Event()
        reports = []
        def report(state, message):
            reports.append(state)
            if state == 'sample_reached':
                reached.set()
        task = asyncio.create_task(world.drive(Autopilot(world, vision, report).mission('red', pause_s=10)))
        await asyncio.wait_for(reached.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert vision.target == 'red' and 'home_arrived' not in reports and not world.steps
    asyncio.run(run())


def test_led_failure_cannot_start_celebration_or_return(vision):
    world = World(vision, block_x=400, size=200, wall_cm=10)
    colors, reports = [], []
    async def led(rgb):
        colors.append(rgb)
        return rgb != (0, 255, 0)
    world.set_led = led
    with pytest.raises(AutopilotError, match='LED/STOP'):
        asyncio.run(world.drive(Autopilot(world, vision, lambda s, m: reports.append(s)).mission('red', pause_s=0)))
    assert colors == [(255, 0, 0), (0, 255, 0)]
    assert 'sample_reached' not in reports and vision.target == 'red'


@pytest.mark.parametrize('color', ['blue', 'yellow'])
def test_non_sample_colors_are_rejected_by_bench(vision, color):
    async def run():
        bench = BenchControl(Settings(mode='hardware', bench_enabled=True), vision.camera, bridge=World(vision))
        bench.vision = vision
        await bench.command('pilot', 'claim', {})
        await bench.command('pilot', 'bench_arm', {})
        with pytest.raises(CommandError, match='red or green'):
            await bench.command('pilot', 'auto_mission', {'color': color, 'arm_token': bench.arm_token})
        assert bench.operation is None and not bench.bridge.steps
    asyncio.run(run())


def test_sample_signal_drives_display_expression_and_clears_on_stop(vision):
    bench = BenchControl(Settings(mode='hardware', bench_enabled=True), vision.camera, bridge=World(vision))
    bench._report('sample_reached', 'green LEDs')
    assert bench.snapshot()['bench']['auto']['expression'] == 'happy'
    bench._report('stopped', 'STOP')
    assert bench.snapshot()['bench']['auto']['expression'] == 'auto'


class FlickerWorld(World):
    """A false target (e.g. a shadow read as home) flickers into view during every turn, but is
    never there in the frame the autopilot decides on."""

    def __init__(self, vision):
        super().__init__(vision, block_x=400, visible=False)

    async def pulse(self, direction, speed, duration_ms):
        await super().pulse(direction, speed, duration_ms)
        if direction in ("left", "right"):
            self.visible = True
            self.show()
            self.visible = False


def test_flickering_false_target_does_not_cause_endless_back_and_forth(vision):
    world = FlickerWorld(vision)
    with pytest.raises(AutopilotError):
        asyncio.run(world.drive(Autopilot(world, vision).go_to("red", max_steps=30)))
    turns = [d for d, _ in world.steps if d in ("left", "right")]
    reversals = sum(a != b for a, b in zip(turns, turns[1:]))
    assert reversals <= len(turns) // 3, turns                     # mostly keeps searching one way
    assert any(d == "forward" for d, _ in world.steps)            # and still gets to explore
