"""Offline only: fake BLE clients and HTTP transport; deny real socket access."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from bullseye import ble_display as display


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("Live network/Bluetooth access is forbidden in these tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(display, "BleakRuntime", denied)


def state(**changes):
    data = {"mode": "hardware", "rover": "connected", "distance_cm": 31,
            "bench": {"distance_status": "live", "distance_age_ms": 180}}
    data.update(changes)
    return data


def packet(sequence=42, cm=31):
    return display.Observation(cm, "live", 180, True, "rover", 10).packet(sequence, 10)


def test_schema_age_and_uuid_match_firmware():
    observation = display.from_state(state(), 10)
    assert observation.packet(42, 11)["sample_age_ms"] == 1180
    assert observation.packet(43, 12)["status"] == "stale"
    wire = display.encode_packet(packet())
    assert wire.endswith(b"\n") and len(wire) <= 256
    assert json.loads(wire)["distance_cm"] == 31
    root = Path(__file__).resolve().parents[3]
    header = (root / "firmware/esp32-display/bullseye_display/DisplayProtocol.h").read_text()
    assert all(value in header for value in (display.SERVICE_UUID, display.RX_UUID, display.ACK_UUID))


@pytest.mark.parametrize("changes", [{"seq": 0}, {"seq": True}, {"seq": 0x100000000}, {"distance_cm": 0},
    {"distance_cm": 151}, {"distance_cm": 31.5}, {"distance_cm": True}, {"v": 2}, {"status": "other"},
    {"status": []}, {"source": "other"}, {"sample_age_ms": -1}, {"rover_connected": "true"}, {"extra": 1}])
def test_bad_wire_schema_is_rejected(changes):
    with pytest.raises(display.ProtocolError):
        display.encode_packet({**packet(), **changes})


def test_invalid_and_disconnected_are_not_converted_to_clear_space():
    invalid = display.from_state(state(distance_cm=0, bench={"distance_status": "invalid", "distance_age_ms": 30}), 10)
    assert invalid.distance_cm is None and invalid.status == "invalid"
    disconnected = display.from_state(state(rover="not_connected"), 10)
    assert disconnected.distance_cm is None and disconnected.status == "unavailable"
    with pytest.raises(display.ProtocolError):
        display.from_state(state(mode="simulation"), 10)
    with pytest.raises(display.ProtocolError):
        display.from_state(state(distance_cm=None), 10)


class FakeClient:
    mtu_size = 23
    is_connected = True

    def __init__(self):
        self.writes, self.packets = [], []
        self.buffer = bytearray()
        self.notification = None
        self.status = 0
        self.ack = True
        self.fail = False
        self.wrote = asyncio.Event()

    async def start_notify(self, uuid, callback):
        assert uuid == display.ACK_UUID
        self.notification = callback

    async def write_gatt_char(self, uuid, data, *, response):
        assert self.notification is not None and uuid == display.RX_UUID and response is True
        self.writes.append(bytes(data)); self.wrote.set()
        if self.fail:
            raise ConnectionError("Fixture link failed midway through a message")
        self.buffer.extend(data)
        if self.buffer.endswith(b"\n"):
            current = json.loads(self.buffer)
            self.packets.append(current)
            self.buffer.clear()
            if self.ack:
                self.notification(None, b"BS\x01" + bytes([self.status]) + struct.pack("<I", current["seq"]))
        await asyncio.sleep(0)


async def channel(client):
    acks = display.Acknowledgements()
    await client.start_notify(display.ACK_UUID, acks.callback)
    return acks


async def test_fragments_use_negotiated_size_and_early_ack_is_correlated():
    client = FakeClient()
    acks = await channel(client)
    assert await acks.deliver(client, packet()) == 42
    assert all(len(chunk) <= 20 for chunk in client.writes)
    assert client.packets == [packet()]
    client.mtu_size = 185
    client.writes.clear()
    await acks.deliver(client, packet(43))
    assert len(client.writes) == 1 and len(client.writes[0]) <= 180
    assert display.chunk_size(SimpleNamespace()) == 20
    assert display.chunk_size(SimpleNamespace(mtu_size=None)) == 20


async def test_missing_ack_has_absolute_deadline_and_clears_inflight(monkeypatch):
    monkeypatch.setattr(display, "ACK_TIMEOUT_SECONDS", .01)
    client = FakeClient(); client.ack = False
    acks = await channel(client)
    with pytest.raises(TimeoutError):
        await acks.deliver(client, packet())
    assert acks.pending is None


@pytest.mark.parametrize("data", [b"bad", b"BS\x02\x00\x2a\x00\x00\x00", b"BS\x01\x02\x00\x00\x00\x00"])
async def test_malformed_or_global_error_ack_rejects_current_message(data):
    acks = display.Acknowledgements()
    future = asyncio.get_running_loop().create_future()
    acks.pending = (42, future)
    acks.receive(data)
    with pytest.raises(display.ProtocolError):
        await future


async def test_unrelated_ack_and_duplicate_delivery_do_not_advance():
    client = FakeClient(); client.ack = False
    acks = await channel(client)
    job = asyncio.create_task(acks.deliver(client, packet()))
    await client.wrote.wait()
    with pytest.raises(display.ProtocolError, match="one display"):
        await acks.deliver(client, packet(43))
    acks.receive(b"BS\x01\x00" + struct.pack("<I", 41))
    assert not job.done()
    acks.receive(b"BS\x01\x00" + struct.pack("<I", 42))
    assert await job == 42


async def test_acquisition_only_gets_cached_state_and_outage_replaces_old_reading():
    mailbox = display.LatestMailbox()
    requests = []
    healthy = True
    def response(request):
        requests.append(request)
        return httpx.Response(200, json=state()) if healthy else httpx.Response(503)
    client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    task = asyncio.create_task(display.acquire(mailbox, client=client, interval=.01))
    try:
        await asyncio.sleep(.025)
        assert mailbox.value.distance_cm == 31
        healthy = False
        await asyncio.sleep(.025)
        assert mailbox.value.status == "unavailable" and mailbox.value.distance_cm is None
        assert all(request.method == "GET" and str(request.url) == display.DEFAULT_STATE_URL for request in requests)
    finally:
        task.cancel(); await asyncio.gather(task, return_exceptions=True)


async def test_actual_cached_endpoint_does_not_acquire_sensor_or_mutate_controls():
    from bullseye.app import create_app
    from bullseye.config import Settings
    bridge = SimpleNamespace(connected=True, distance_at=time.monotonic(), distance_cm=31,
        last_stop_acknowledged=True, status="ready", error=None, heartbeats=1,
        connect=AsyncMock(), stop=AsyncMock(), refresh_distance=AsyncMock(),
        pulse=AsyncMock(), drive_frame=AsyncMock())
    app = create_app(Settings(mode="hardware", bench_enabled=True), bench_bridge=bridge)
    controller = app.state.bench
    controller.pilot, controller.armed, controller.arm_token = "existing-pilot", True, "existing-token"
    before = (controller.pilot, controller.armed, controller.arm_token, controller.lease_until,
              controller.stopped, tuple(controller.events))
    # No lifespan/startup: only exercise the real in-process cached GET route.
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    mailbox = display.LatestMailbox()
    task = asyncio.create_task(display.acquire(mailbox, client=client, interval=.01))
    try:
        await asyncio.sleep(.03)
        assert mailbox.value.distance_cm == 31 and mailbox.value.source == "rover"
    finally:
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
    after = (controller.pilot, controller.armed, controller.arm_token, controller.lease_until,
             controller.stopped, tuple(controller.events))
    assert after == before
    for name in ("connect", "stop", "refresh_distance", "pulse", "drive_frame"):
        getattr(bridge, name).assert_not_awaited()


async def test_demo_never_constructs_http_or_uses_rover(monkeypatch):
    monkeypatch.setattr(display.httpx, "AsyncClient", Mock(side_effect=AssertionError("Demo must not use HTTP")))
    mailbox = display.LatestMailbox()
    task = asyncio.create_task(display.acquire(mailbox, demo=True, interval=.01))
    await asyncio.sleep(0)
    assert mailbox.value.source == "demo" and mailbox.value.distance_cm == 37 and not mailbox.value.rover_connected
    task.cancel(); await asyncio.gather(task, return_exceptions=True)


async def test_http_transit_delay_is_included_in_sample_age():
    mailbox = display.LatestMailbox()
    responded = asyncio.Event()
    async def response(_request):
        await asyncio.sleep(.03)
        responded.set()
        return httpx.Response(200, json=state())
    client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    task = asyncio.create_task(display.acquire(mailbox, client=client))
    try:
        await asyncio.wait_for(responded.wait(), 1)
        assert mailbox.value.packet(1, time.monotonic())["sample_age_ms"] >= 200
    finally:
        task.cancel(); await asyncio.gather(task, return_exceptions=True)


def test_selection_and_backoff():
    a, b = SimpleNamespace(address="UUID-A"), SimpleNamespace(address="UUID-B")
    assert display.select_device([a, b], "uuid-b") is b
    with pytest.raises(display.DeviceSelectionError):
        display.select_device([a, b])
    with pytest.raises(ConnectionError):
        display.select_device([])
    assert [display.reconnect_delay(i) for i in range(6)] == [1, 2, 4, 5, 5, 5]


async def test_reconnect_discards_partial_frame_resets_sequence_and_uses_latest(monkeypatch):
    first, second = FakeClient(), FakeClient()
    first.fail = True
    mailbox = display.LatestMailbox()
    mailbox.value = display.Observation(10, "live", 0, True, "rover", time.monotonic())
    producer_cancelled = asyncio.Event()
    applied = asyncio.Event()
    closed = []
    async def producer(_mailbox, **_):
        try:
            await first.wrote.wait()
            mailbox.value = display.Observation(55, "live", 0, True, "rover", time.monotonic())
            await asyncio.Event().wait()
        finally:
            producer_cancelled.set()
    original = second.write_gatt_char
    async def write(*args, **kwargs):
        await original(*args, **kwargs)
        if second.packets:
            applied.set()
    second.write_gatt_char = write
    class Runtime:
        count = 0
        async def discover(self):
            await asyncio.sleep(0)
            return [SimpleNamespace(address="DISPLAY", name="Bullseye-Display")]
        @asynccontextmanager
        async def connect(self, _device):
            self.count += 1
            current = first if self.count == 1 else second
            try:
                yield current
            finally:
                closed.append(current)
        def guidance(self, _error): return None
    monkeypatch.setattr(display, "acquire", producer)
    monkeypatch.setattr(display, "reconnect_delay", lambda _attempt: 0)
    task = asyncio.create_task(display.run_sender(display.arguments([]), runtime=Runtime(), mailbox=mailbox))
    try:
        async with asyncio.timeout(1): await applied.wait()
        assert second.packets[0]["seq"] == 1 and second.packets[0]["distance_cm"] == 55
        assert closed == [first]
    finally:
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
    assert closed == [first, second] and producer_cancelled.is_set()


async def test_permission_denial_gives_guidance_in_list_mode(capsys):
    class Runtime:
        async def discover(self): raise PermissionError()
        def guidance(self, _error): return "System Settings > Privacy & Security > Bluetooth"
    assert await display.run_sender(display.arguments(["--list"]), runtime=Runtime()) == 3
    assert "Privacy & Security > Bluetooth" in capsys.readouterr().err


def test_help_does_not_import_bleak_or_touch_hardware():
    result = subprocess.run([sys.executable, "-c", "import sys; from bullseye.ble_display import arguments\ntry: arguments(['--help'])\nfinally: assert 'bleak' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0 and "--demo" in result.stdout


@pytest.mark.parametrize("args", [["--demo", "--list"], ["--demo", "--distance-cm", "0"], ["--distance-cm", "42"], ["--state-url", "file:///tmp/state"]])
def test_invalid_options_fail_before_any_transport(args):
    with pytest.raises(SystemExit) as error: display.arguments(args)
    assert error.value.code == 2


def test_arrival_expression_and_legacy_packet_compatibility():
    normal = display.from_state(state(), 10).packet(1, 10)
    assert "expression" not in normal and len(normal) == 7
    data = state(bench={"distance_status": "live", "distance_age_ms": 180,
                        "auto": {"state": "sample_reached", "expression": "happy"}})
    observation = display.from_state(data, 10)
    happy = observation.packet(2, 10.5)
    assert happy["expression"] == "happy"
    assert json.loads(display.encode_packet(happy))["expression"] == "happy"
    assert len(display.encode_packet(happy)) <= 256
    assert "expression" not in observation.packet(3, 13)  # stale producer cannot latch celebration
    assert "expression" not in display.from_state({**data, "rover": "disconnected"}, 10).packet(4, 10)
    data["bench"]["auto"]["expression"] = "auto"
    assert "expression" not in display.from_state(data, 10).packet(5, 10)


@pytest.mark.parametrize("expression", ["grumpy", None, True, [], 1])
def test_invalid_expression_is_rejected(expression):
    with pytest.raises(display.ProtocolError):
        display.encode_packet({**packet(), "expression": expression})
    with pytest.raises(display.ProtocolError):
        display.from_state(state(bench={"distance_status": "live", "distance_age_ms": 0,
                                       "auto": {"expression": expression}}), 10)


def test_happy_is_independent_of_distance_sample_validity():
    for status in ("invalid", "unavailable", "stale"):
        data = state(distance_cm=None, bench={"distance_status": status, "distance_age_ms": None,
                                              "auto": {"expression": "happy"}})
        outgoing = display.from_state(data, 10).packet(1, 10)
        assert outgoing["expression"] == "happy" and outgoing["distance_cm"] is None
        display.encode_packet(outgoing)
