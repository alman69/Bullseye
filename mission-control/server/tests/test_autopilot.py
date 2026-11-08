"""Demo autopilot in a tiny simulated world: real vision pipeline, fake rover bridge."""
import asyncio
import json

import cv2
import numpy as np
import pytest

from bullseye.autopilot import Autopilot, AutopilotError
from bullseye.bench_demo import BenchControl
from bullseye.camera import CameraHub
from bullseye.config import Settings
from bullseye.mission import CommandError
from bullseye.vision import PACKAGE_DIR, VisionService


def frame(block_x=None, size=60, bottom=420):
    img = np.full((600, 800, 3), 235, np.uint8)                  # white floor
    img[:200] = (90, 136, 176)                                    # cardboard wall
    if block_x is not None:
        cv2.rectangle(img, (int(block_x - size / 2), int(bottom - size)), (int(block_x + size / 2), int(bottom)),
                      (30, 30, 220), -1)                          # red block
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


@pytest.fixture
def vision(tmp_path):
    cfg = json.loads((PACKAGE_DIR / "config.json").read_text())
    cfg["camera"]["controls"], cfg["rover_mask"] = {}, {"boxes": []}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    settings = Settings(vision_enabled=True, vision_dir=tmp_path)
    return VisionService(settings, CameraHub(settings))


class World:
    """Fake bridge: each timed step moves the block in the next camera frame."""

    connected, status, error, distance_cm, heartbeats, last_stop_acknowledged = True, "ready", None, None, 0, True

    def __init__(self, vision, block_x=200.0, size=60.0, visible=True, wall_cm=None):
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
            return await coro
        finally:
            streaming.cancel()

    def show(self):
        self.seq += 1
        self.vision._process(frame(self.x if self.visible else None, self.size, self.bottom), self.seq)

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


def test_never_gives_up_and_explores_open_floor(vision):
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
