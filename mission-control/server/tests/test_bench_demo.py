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
            self.reader.feed(b"11}")
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
    assert [command["N"] for command in commands] == [100, 21, 100, 2, 100]
    assert commands[-2]["D1"] == 3 and commands[-2]["D2"] == 40 and commands[-2]["T"] == 1000
    assert bridge.last_stop_acknowledged is True
    assert bridge.heartbeats == 1


async def test_bridge_missing_or_old_completion_reply_still_sends_stop(tcp):
    bridge, _, writer, _ = tcp
    writer.complete = False
    async with asyncio.timeout(.5):
        await bridge.pulse("left", 40, 50)
    assert [command["N"] for command in writer.commands].count(2) == 1
    assert writer.commands[-1]["N"] == 100


async def test_bridge_cancel_during_motion_stops_and_never_replays(tcp):
    bridge, _, writer, opened = tcp
    writer.complete = False
    job = asyncio.create_task(bridge.pulse("backward", 40, 1000))
    await writer.moved.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert writer.commands[-1]["N"] == 100
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

    @property
    def connected(self):
        return self.status == "ready"

    async def connect(self):
        self.status = "ready"
        self.last_stop_acknowledged = True

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
    await bench.command("pilot", "bench_arm", {"wheels_raised": True})


def payload(bench, **changes):
    return {"direction": "forward", "speed": 40, "duration_ms": 200, "arm_token": bench.arm_token, **changes}


async def test_exclusive_pilot_and_wheels_confirmation_before_arm(bench):
    await bench.command("pilot", "claim", {})
    with pytest.raises(CommandError, match="Another pilot"):
        await bench.command("viewer", "claim", {})
    with pytest.raises(CommandError, match="Claim"):
        await bench.command("viewer", "bridge_connect", {})
    with pytest.raises(CommandError, match="Connect"):
        await bench.command("pilot", "bench_arm", {"wheels_raised": True})
    await bench.command("pilot", "bridge_connect", {})
    await settle(bench)
    with pytest.raises(CommandError, match="all four wheels"):
        await bench.command("pilot", "bench_arm", {"wheels_raised": "true"})
    assert not bench.armed and bench.bridge.pulses == []


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
    await bench.command("pilot", "bench_arm", {"wheels_raised": True})
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
