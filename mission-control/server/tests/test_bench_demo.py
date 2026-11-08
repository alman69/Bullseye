import asyncio
from collections import deque
import json
import time
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from bullseye.app import create_app
from bullseye.bench_demo import BenchControl, FactoryBridge
from bullseye.camera import CameraHub
from bullseye.config import Settings
from bullseye.mission import CommandError


class StreamReader:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def read(self, _):
        return await self.queue.get()

    def feed(self, data):
        self.queue.put_nowait(data)


class StreamWriter:
    def __init__(self, reader):
        self.reader = reader
        self.sent = []
        self.complete = True
        self.closed = False
        self.moved = asyncio.Event()
        self.distance = 11
        self.fence_reply = True
        self.fence_value = "512"

    def write(self, data):
        self.sent.append(data)
        if data == b"{Heartbeat}":
            return
        command = json.loads(data)
        if command["N"] == 100:
            self.reader.feed(b"{ok}")
        elif command["N"] == 21:
            self.reader.feed(b"{old_99}")
            self.reader.feed(("{" + command["H"] + "_").encode())
            self.reader.feed((str(self.distance) + "}").encode())
        elif command["N"] == 8:
            self.reader.feed(("{" + command["H"] + "_ok}").encode())
        elif command["N"] == 22 and self.fence_reply:
            self.reader.feed(("{" + command["H"] + "_" + self.fence_value + "}").encode())
        elif command["N"] == 2:
            self.moved.set()
            self.reader.feed(b"{Heartbeat}")
            self.reader.feed(b"{old_ok}")
            if self.complete:
                self.reader.feed(("{" + command["H"] + "_ok}").encode())

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.closed = True
        self.reader.feed(b"")

    @property
    def commands(self):
        return [json.loads(frame) for frame in self.sent if frame != b"{Heartbeat}"]


def assert_stopped_after_motion(commands):
    """After the last N2 the very next command is STOP; LED (N8) commands only ever follow a STOP."""
    numbers = [command["N"] for command in commands]
    last_move = max(i for i, n in enumerate(numbers) if n == 2)
    assert numbers[last_move + 1] == 100
    assert all(n in (100, 8) for n in numbers[last_move + 1:])
    assert all(numbers[i - 1] in (100, 8) for i, n in enumerate(numbers) if n == 8)


@pytest_asyncio.fixture
async def tcp(monkeypatch):
    reader = StreamReader()
    writer = StreamWriter(reader)
    open_connection = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr("bullseye.bench_demo.asyncio.open_connection", open_connection)
    bridge = FactoryBridge("rover.test", 100)
    await bridge.connect()
    yield bridge, reader, writer, open_connection
    await bridge.close()


async def test_bridge_connect_proves_unique_sensor_reply_and_echoes_idle_heartbeat(tcp):
    bridge, reader, writer, _ = tcp
    assert bridge.connected and bridge.distance_cm == 11
    assert bridge.last_stop_acknowledged is True
    reader.feed(b"{Heart")
    reader.feed(b"beat}")
    await asyncio.sleep(.01)
    assert writer.sent[-1] == b"{Heartbeat}" and bridge.heartbeats == 1
    assert [command["N"] for command in writer.commands] == [100, 21]


async def test_bridge_one_finite_pulse_with_correlated_completion_then_stop(tcp):
    bridge, _, writer, _ = tcp
    await bridge.pulse("forward", 40, 1000)
    commands = writer.commands
    assert [command["N"] for command in commands] == [100, 21, 100, 8, 2, 100, 8]   # LEDs re-set after each STOP
    assert (commands[3]["D2"], commands[3]["D3"], commands[3]["D4"]) == (255, 0, 0)  # red by default
    assert commands[-3]["D1"] == 3 and commands[-3]["D2"] == 40 and commands[-3]["T"] == 1000
    assert bridge.last_stop_acknowledged is True
    assert bridge.heartbeats == 1


async def test_bridge_missing_or_old_completion_reply_still_sends_stop(tcp):
    bridge, _, writer, _ = tcp
    writer.complete = False
    async with asyncio.timeout(.5):
        await bridge.pulse("left", 40, 50)
    assert [command["N"] for command in writer.commands].count(2) == 1
    assert_stopped_after_motion(writer.commands)


async def test_bridge_cancel_during_motion_stops_and_never_replays(tcp):
    bridge, _, writer, opened = tcp
    writer.complete = False
    job = asyncio.create_task(bridge.pulse("backward", 40, 1000))
    await writer.moved.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert_stopped_after_motion(writer.commands)
    assert sum(command["N"] == 2 for command in writer.commands) == 1
    opened.assert_awaited_once()


async def test_link_loss_fails_pending_motion_and_does_not_reconnect(tcp):
    bridge, reader, writer, opened = tcp
    writer.complete = False
    job = asyncio.create_task(bridge.pulse("forward", 40, 1000))
    await writer.moved.wait()
    reader.feed(b"")
    with pytest.raises(ConnectionError):
        await job
    assert not bridge.connected and bridge.last_stop_acknowledged is False
    opened.assert_awaited_once()


async def test_concurrent_stops_share_one_generic_vendor_ack_waiter(tcp):
    bridge, _, writer, _ = tcp
    before = len(writer.commands)
    assert all(await asyncio.gather(*(bridge.stop() for _ in range(8))))
    assert len(writer.commands) == before + 1


async def test_failed_preflight_stop_prevents_motion(tcp, monkeypatch):
    bridge, _, writer, _ = tcp
    monkeypatch.setattr(bridge, "stop", AsyncMock(return_value=False))
    with pytest.raises(ConnectionError, match="no movement"):
        await bridge.pulse("forward", 40, 200)
    assert not any(command["N"] == 2 for command in writer.commands)


async def test_held_drive_uses_finite_commands_and_waits_for_cached_read_ack(tcp):
    bridge, _, writer, _ = tcp
    await bridge.drive_frame("forward", 40)
    assert [command["N"] for command in writer.commands][-2:] == [2, 22]
    assert writer.commands[-2]["T"] == 300 and writer.commands[-2]["D1"] == 3
    assert writer.commands[-1]["D1"] == 0


async def test_missing_drive_ack_stops_without_sending_more_movement(tcp):
    bridge, _, writer, _ = tcp
    writer.fence_reply = False
    with pytest.raises(TimeoutError):
        await bridge.drive_frame("forward", 40)
    assert sum(command["N"] == 2 for command in writer.commands) == 1
    assert writer.commands[-1]["N"] == 100


@pytest.mark.parametrize("value", ["ok", "-1", "1024"])
async def test_malformed_cached_ack_stops_instead_of_continuing(tcp, value):
    bridge, _, writer, _ = tcp
    writer.fence_value = value
    with pytest.raises(ConnectionError):
        await bridge.drive_frame("forward", 40)
    assert writer.commands[-1]["N"] == 100


async def test_expired_input_waiting_on_sensor_lock_never_sends_late_motion(tcp):
    bridge, _, writer, _ = tcp
    await bridge.io_lock.acquire()
    valid = True
    job = asyncio.create_task(bridge.drive_frame("forward", 40, valid=lambda: valid))
    await asyncio.sleep(0)
    valid = False
    bridge.io_lock.release()
    assert await job is False
    assert not any(command["N"] == 2 for command in writer.commands)


async def test_distance_queries_wait_for_motion_transaction_and_cancellation_sends_stop(tcp):
    bridge, _, writer, _ = tcp
    writer.fence_reply = False
    motion = asyncio.create_task(bridge.drive_frame("forward", 40))
    await writer.moved.wait()
    distance = asyncio.create_task(bridge.refresh_distance())
    await asyncio.sleep(.01)
    assert sum(command["N"] == 21 for command in writer.commands) == 1  # Connection-time read only.
    motion.cancel()
    with pytest.raises(asyncio.CancelledError):
        await motion
    assert await distance is True
    commands = writer.commands
    assert [command["N"] for command in commands][-2:] == [100, 21]


async def test_live_distance_zero_is_invalid_and_failure_marks_old_value_stale(tcp, monkeypatch):
    bridge, _, writer, _ = tcp
    writer.distance = 0
    assert await bridge.refresh_distance()
    assert bridge.distance_cm is None and bridge.distance_at > 0
    settings = Settings(mode="hardware", bench_enabled=True)
    controller = BenchControl(settings, CameraHub(settings), bridge)
    assert controller.snapshot()["bench"]["distance_status"] == "invalid"
    monkeypatch.setattr(bridge, "request", AsyncMock(side_effect=TimeoutError))
    assert await bridge.refresh_distance() is False
    assert controller.snapshot()["bench"]["distance_status"] == "stale"


class FakeBridge:
    def __init__(self):
        self.status = "disconnected"
        self.error = None
        self.distance_cm = 11
        self.heartbeats = 0
        self.last_stop_acknowledged = None
        self.pulses = []
        self.stops = 0
        self.release_pulse = asyncio.Event()
        self.pulse_started = asyncio.Event()
        self.distance_at = 0
        self.distance_error = None
        self.measurements = 0
        self.frames = []
        self.frame_started = asyncio.Event()

    @property
    def connected(self):
        return self.status == "ready"

    async def connect(self):
        self.status = "ready"
        self.last_stop_acknowledged = True
        self.distance_at = time.monotonic()

    async def stop(self):
        self.stops += 1
        self.last_stop_acknowledged = self.connected
        return self.connected

    async def pulse(self, direction, speed, duration_ms):
        self.pulses.append((direction, speed, duration_ms))
        self.pulse_started.set()
        try:
            await self.release_pulse.wait()
        finally:
            await self.stop()

    async def close(self):
        self.status = "disconnected"

    async def refresh_distance(self):
        self.measurements += 1
        self.distance_at = time.monotonic()
        return True

    async def drive_frame(self, direction, speed, *, valid=lambda: True):
        if not valid():
            return False
        self.frames.append((direction, speed))
        self.frame_started.set()
        await asyncio.sleep(0)
        return True


async def settle(control):
    async with asyncio.timeout(1):
        while control.operation or control.stop_pending:
            await asyncio.sleep(.001)


@pytest_asyncio.fixture
async def bench():
    settings = Settings(mode="hardware", bench_enabled=True)
    controller = BenchControl(settings, CameraHub(settings), FakeBridge())
    await controller.start()
    yield controller
    await controller.close()


async def arm(bench):
    await bench.command("pilot", "claim", {})
    await bench.command("pilot", "bridge_connect", {})
    await settle(bench)
    await bench.command("pilot", "bench_arm", {})


def payload(bench, **changes):
    return {"direction": "forward", "speed": 40, "duration_ms": 200, "arm_token": bench.arm_token, **changes}


async def test_exclusive_pilot_and_connected_bridge_before_enable(bench):
    await bench.command("pilot", "claim", {})
    with pytest.raises(CommandError, match="Another pilot"):
        await bench.command("viewer", "claim", {})
    with pytest.raises(CommandError, match="Claim"):
        await bench.command("viewer", "bridge_connect", {})
    with pytest.raises(CommandError, match="Connect"):
        await bench.command("pilot", "bench_arm", {})
    await bench.command("pilot", "bridge_connect", {})
    await settle(bench)
    await bench.command("pilot", "bench_arm", {})
    assert bench.armed and bench.bridge.pulses == []


async def test_steps_do_not_queue_and_old_token_is_rejected_after_completion(bench):
    await arm(bench)
    old = payload(bench)
    await bench.command("pilot", "bench_pulse", old)
    with pytest.raises(CommandError, match="not queued"):
        await bench.command("pilot", "bench_pulse", old)
    await bench.bridge.pulse_started.wait()
    bench.bridge.release_pulse.set()
    await settle(bench)
    with pytest.raises(CommandError, match="outdated"):
        await bench.command("pilot", "bench_pulse", old)
    assert bench.bridge.pulses == [("forward", 40, 200)]


async def test_viewer_stop_interrupts_step_and_requires_explicit_rearm(bench):
    await arm(bench)
    old = payload(bench)
    await bench.command("pilot", "bench_pulse", old)
    await bench.bridge.pulse_started.wait()
    async with asyncio.timeout(.1):
        result = await bench.stop()
    assert result["phase"] == "stopped" and not result["bench"]["armed"]
    await settle(bench)
    with pytest.raises(CommandError):
        await bench.command("pilot", "bench_pulse", old)
    await bench.command("pilot", "bench_arm", {})
    with pytest.raises(CommandError, match="outdated"):
        await bench.command("pilot", "bench_pulse", old)


async def test_stop_before_job_first_runs_clears_busy_without_leaking_coroutine(bench):
    await arm(bench)
    await bench.command("pilot", "bench_pulse", payload(bench))
    await bench.stop()
    await settle(bench)
    assert bench.operation is None and bench.bridge.pulses == []


async def test_ping_does_not_renew_lease_and_expiry_disables_controls(bench):
    await arm(bench)
    original = bench.lease_until
    await bench.command("pilot", "ping", {})
    await bench.command("viewer", "ping", {})
    assert bench.lease_until == original
    bench.lease_until = time.monotonic() - 1
    await asyncio.sleep(.15)
    assert bench.pilot is None and not bench.armed and bench.stopped


async def test_disconnect_and_hardware_loss_disable_steps_without_auto_reconnect(bench):
    await arm(bench)
    bench.bridge.status = "disconnected"
    await asyncio.sleep(.15)
    assert not bench.armed and bench.stopped
    await bench.disconnect("pilot")
    assert bench.pilot is None


def hold_payload(bench, hold_id="hold-1", **changes):
    return {"direction": "forward", "speed": 40, "hold_id": hold_id, "arm_token": bench.arm_token, **changes}


async def test_keyboard_renews_movement_beyond_one_step_then_release_stops(bench):
    await arm(bench)
    held = hold_payload(bench)
    await bench.command("pilot", "hold_start", held)
    for _ in range(6):
        await asyncio.sleep(.1)
        await bench.command("pilot", "hold_update", held)
    assert len(bench.bridge.frames) >= 4 and bench.armed
    await bench.command("pilot", "hold_end", held)
    await settle(bench)
    count = len(bench.bridge.frames)
    await asyncio.sleep(.2)
    assert len(bench.bridge.frames) == count and bench.armed
    assert bench.hold_id is None and bench.bridge.stops > 0


async def test_stale_keyboard_input_stops_and_disables_without_waiting_for_pilot_expiry(bench):
    await arm(bench)
    await bench.command("pilot", "hold_start", hold_payload(bench))
    await asyncio.sleep(.5)
    assert bench.pilot == "pilot" and not bench.armed and bench.stopped
    assert bench.operation is None and bench.hold_id is None


async def test_old_hold_renewals_and_end_events_cannot_restart_or_cancel_new_hold(bench):
    await arm(bench)
    old = hold_payload(bench)
    await bench.command("pilot", "hold_start", old)
    await bench.command("pilot", "hold_end", old)
    await settle(bench)
    fresh = hold_payload(bench, "hold-2")
    await bench.command("pilot", "hold_start", fresh)
    await bench.command("pilot", "hold_update", old)
    await bench.command("pilot", "hold_end", old)
    assert bench.hold_id == "hold-2"
    await bench.command("pilot", "hold_end", fresh)
    await settle(bench)
    with pytest.raises(CommandError, match="outdated"):
        await bench.command("pilot", "hold_start", old)


async def test_stop_cancels_hold_before_first_frame_and_delayed_renewal_does_nothing(bench):
    await arm(bench)
    held = hold_payload(bench)
    await bench.command("pilot", "hold_start", held)
    await bench.stop()
    await settle(bench)
    await bench.command("pilot", "hold_update", held)
    assert not bench.armed and bench.bridge.frames == []


async def test_viewer_cannot_renew_keyboard_input(bench):
    await arm(bench)
    with pytest.raises(CommandError, match="Claim"):
        await bench.command("viewer", "hold_update", hold_payload(bench))


async def test_idle_telemetry_refreshes_without_claiming_controls(bench):
    await bench.command("pilot", "claim", {})
    await bench.command("pilot", "bridge_connect", {})
    await settle(bench)
    before = bench.bridge.measurements
    await asyncio.sleep(.6)
    assert bench.bridge.measurements > before and not bench.armed
    snapshot = bench.snapshot()
    assert snapshot["bench"]["distance_status"] == "live"
    assert snapshot["bench"]["distance_age_ms"] < 1000


@pytest.mark.parametrize("changes", [{"speed": 101}, {"speed": True}, {"duration_ms": 0}, {"duration_ms": 1001}, {"direction": "diagonal"}])
async def test_invalid_steps_never_reach_bridge(bench, changes):
    await arm(bench)
    with pytest.raises(CommandError):
        await bench.command("pilot", "bench_pulse", payload(bench, **changes))
    assert bench.bridge.pulses == []


def test_bench_api_opt_in_shared_pilot_and_duplicate_ids():
    assert TestClient(create_app()).get("/api/bench/state").status_code == 404
    app = create_app(Settings(mode="hardware", bench_enabled=True), bench_bridge=FakeBridge())
    with TestClient(app) as client:
        assert client.get("/bench").status_code == 200
        with client.websocket_connect("/ws/bench") as first, client.websocket_connect("/ws/bench") as second:
            first.receive_json()
            second.receive_json()
            def command(ws, request_id, kind):
                ws.send_json({"id": request_id, "type": kind, "payload": {}})
                while True:
                    reply = ws.receive_json()
                    if reply.get("type") == "ack" and reply.get("id") == request_id:
                        return reply
            assert command(first, "1", "claim")["status"] == "accepted"
            assert command(first, "1", "claim")["status"] == "rejected"
            assert command(second, "2", "claim")["status"] == "rejected"
            assert command(second, "3", "ping")["status"] == "accepted"
            assert client.post("/api/bench/stop").json()["phase"] == "stopped"
            client.post("/api/stop")
            assert client.get("/api/bench/state").json()["bench"]["armed"] is False


async def test_set_led_stops_first_then_sets_the_color(tcp):
    bridge, _, writer, _ = tcp
    assert await bridge.set_led((0, 255, 0))
    assert [command["N"] for command in writer.commands][-2:] == [100, 8]
    assert (writer.commands[-1]["D2"], writer.commands[-1]["D3"]) == (0, 255) and not bridge.led_dirty
