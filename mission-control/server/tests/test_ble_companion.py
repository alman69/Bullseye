import asyncio
from contextlib import asynccontextmanager
import json
import struct
from types import SimpleNamespace

import pytest

from bullseye.ble_companion import (BleCompanionLink, COMMAND_UUID, STATUS_UUID,
                                    decode_status, encode_command)
from bullseye.ble_display import ACK_UUID, RX_UUID, ProtocolError
from bullseye.config import Settings
from bullseye.bench_demo import BenchControl
from bullseye.camera import CameraHub
from test_bench_demo import FakeBridge


class Peripheral:
    def __init__(self):
        self.is_connected = True
        self.mtu_size = 23
        self.boot, self.seq, self.flags, self.left, self.right, self.step = 42, 0, 5, 90, 90, 0
        self.callbacks, self.commands, self.telemetry = {}, [], []
        self.buffer = bytearray()
        self.silent, self.delay_motion, self.resume = False, False, asyncio.Event()
        self.started = asyncio.Event()

    def packet(self, result=0, reply=0):
        return struct.pack("<2sBBIIIBBBB", b"BC", 1, result, self.boot, self.seq, reply, self.flags, self.left, self.right, self.step)

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback

    async def read_gatt_char(self, uuid):
        assert uuid == STATUS_UUID
        return self.packet()

    def notify(self, result=0, reply=0):
        self.callbacks[STATUS_UUID](None, self.packet(result, reply))

    async def write_gatt_char(self, uuid, data, response):
        assert response is True
        if uuid == RX_UUID:
            assert len(data) <= 20
            self.buffer.extend(data)
            if self.buffer.endswith(b"\n"):
                packet = json.loads(self.buffer)
                self.telemetry.append(packet)
                self.buffer.clear()
                self.callbacks[ACK_UUID](None, b"BS\x01\x00" + struct.pack("<I", packet["seq"]))
            return
        assert uuid == COMMAND_UUID and len(data) == 12
        magic, version, opcode, boot, seq = struct.unpack("<2sBBII", data)
        assert magic == b"BC" and version == 1
        self.commands.append((opcode, boot, seq))
        if self.delay_motion and opcode == 1:
            self.started.set()
            try:
                await self.resume.wait()
            except asyncio.CancelledError:
                # Save a packet that can arrive after STOP, without applying it.
                self.delayed = (boot, seq)
                raise
        if boot != self.boot or opcode and seq <= self.seq:
            if not self.silent:
                self.notify(3, seq)
            return
        if opcode:
            self.seq = seq
        if opcode in {3, 4}:
            self.flags, self.step = (self.flags & 3) | (4 if self.flags & 1 else 0), 0
        elif opcode == 1:
            self.flags, self.step = 13, 1
        elif opcode == 2:
            self.flags, self.step = 29, 1
        if not self.silent:
            self.notify(1, seq)


class Runtime:
    def __init__(self, peripheral=None):
        self.peripheral = peripheral or Peripheral()
        self.devices = [SimpleNamespace(address="mac-corebluetooth-id", name="Bullseye-Display")]
        self.connections = 0

    async def discover(self):
        return self.devices

    @asynccontextmanager
    async def connect(self, device):
        assert device in self.devices
        self.connections += 1
        self.peripheral.is_connected = True
        try:
            yield self.peripheral
        finally:
            self.peripheral.is_connected = False

    def guidance(self, error):
        return None


def state():
    return {"mode": "hardware", "rover": "connected", "distance_cm": 31,
            "bench": {"distance_status": "live", "distance_age_ms": 123}}


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.005)


def test_binary_contract_and_validation():
    assert encode_command("squeeze", 42, 1).hex() == "424301012a00000001000000"
    result, reply, value = decode_status(bytes.fromhex("424301012a0000000100000001000000055a5a00"))
    assert (result, reply, value["state"], value["angles"]) == (1, 1, "holding", {"left": 90, "right": 90})
    for data in (b"", bytes(20), b"BC\x01\xff" + bytes(16)):
        with pytest.raises(ProtocolError):
            decode_status(data)
    for seq in (0, -1, 2**32):
        with pytest.raises(ProtocolError):
            encode_command("squeeze", 42, seq)


async def test_one_connection_for_display_and_scooper_without_http_or_rover_requests():
    calls = []
    runtime = Runtime()
    def cached():
        calls.append("cache")
        return state()
    link = BleCompanionLink(cached, runtime_factory=lambda: runtime)
    try:
        await link.connect()
        assert link.ready and runtime.connections == 1
        await until(lambda: link.display_updates > 0)
        assert runtime.peripheral.telemetry[0]["distance_cm"] == 31
        assert calls == ["cache"]
        assert runtime.peripheral.commands[0][0] == 4  # Startup STOP only.
        assert await link.command("squeeze") is True
        assert link.state == "sequence" and link.sequence == "squeeze" and link.step == "down"
        assert link.snapshot()["transport"] == "ble"
        assert await link.request_stop() is True
        assert link.state == "holding"
    finally:
        await link.close()
    assert not runtime.peripheral.is_connected


async def test_cached_outage_is_unavailable_not_replayed():
    runtime = Runtime()
    link = BleCompanionLink(lambda: {}, runtime_factory=lambda: runtime)
    try:
        await link.connect()
        await until(lambda: bool(runtime.peripheral.telemetry))
        packet = runtime.peripheral.telemetry[0]
        assert packet["status"] == "unavailable" and packet["distance_cm"] is None
    finally:
        await link.close()


async def test_pwm_failure_keeps_ble_telemetry_connected_but_never_sends_motion():
    runtime = Runtime()
    runtime.peripheral.flags = 0  # Real BLE status, failed/relaxed servo output.
    link = BleCompanionLink(state, runtime_factory=lambda: runtime)
    try:
        await link.connect()
        await until(lambda: link.display_updates > 0)
        snapshot = link.snapshot()
        assert link.ready and snapshot["ble_connected"] and snapshot["servo_ready"] is False
        assert "BLE display connected" in link.error and "PWM" in link.error
        before = len(runtime.peripheral.commands)
        for name in ("squeeze", "release", "home"):
            assert "Motion is disabled" in await link.command(name)
        assert len(runtime.peripheral.commands) == before
        assert await link.command("stop") is True  # STOP remains available.
        assert runtime.peripheral.is_connected
    finally:
        await link.close()


async def test_runtime_pwm_fault_disables_actions_without_losing_the_display():
    runtime = Runtime()
    link = BleCompanionLink(state, runtime_factory=lambda: runtime)
    try:
        await link.connect()
        await until(lambda: link.display_updates > 0)
        runtime.peripheral.flags = 0
        runtime.peripheral.notify()
        await until(lambda: link.servo_ready is False)
        assert link.ready and link.snapshot()["ble_connected"]
        before = len(runtime.peripheral.commands)
        assert "PWM unavailable" in await link.command("squeeze")
        assert len(runtime.peripheral.commands) == before
        assert runtime.connections == 1
    finally:
        await link.close()


async def test_stop_cancels_delayed_motion_and_uses_newer_sequence():
    runtime = Runtime()
    link = BleCompanionLink(state, runtime_factory=lambda: runtime)
    try:
        await link.connect()
        runtime.peripheral.delay_motion = True
        move = link.launch(link.command("squeeze"))
        await runtime.peripheral.started.wait()
        assert await link.request_stop() is True
        assert move.cancelled() and link.state == "holding"
        _, old_seq = runtime.peripheral.delayed
        assert old_seq < runtime.peripheral.seq
        runtime.peripheral.notify(3, old_seq)  # A delayed rejected ACK cannot restart anything.
        await asyncio.sleep(0)
        assert link.state == "holding" and link.busy is None
    finally:
        await link.close()


async def test_missing_ack_closes_session_without_retrying_motion(monkeypatch):
    monkeypatch.setattr("bullseye.ble_companion.COMMAND_TIMEOUT_S", .03)
    runtime = Runtime()
    link = BleCompanionLink(state, runtime_factory=lambda: runtime)
    try:
        await link.connect()
        runtime.peripheral.silent = True
        with pytest.raises(ConnectionError, match="not acknowledged"):
            await link.command("squeeze")
        await until(lambda: link.client is None)
        assert runtime.connections == 1
        assert sum(op == 1 for op, _, _ in runtime.peripheral.commands) == 1
    finally:
        await link.close()


async def test_reconnect_does_not_replay_movement_and_ignores_old_callback():
    runtime = Runtime()
    link = BleCompanionLink(state, runtime_factory=lambda: runtime)
    try:
        await link.connect()
        old_callback = runtime.peripheral.callbacks[STATUS_UUID]
        assert await link.command("squeeze") is True
        await link.connect()
        assert link.ready and link.state == "holding" and runtime.connections == 2
        old_callback(None, b"invalid old connection")
        await asyncio.sleep(0)
        assert link.failure is None
        assert sum(op == 1 for op, _, _ in runtime.peripheral.commands) == 1
    finally:
        await link.close()


async def test_multiple_devices_require_explicit_corebluetooth_id():
    runtime = Runtime()
    runtime.devices.append(SimpleNamespace(address="second-id"))
    link = BleCompanionLink(state, runtime_factory=lambda: runtime)
    await link.connect()
    assert not link.ready and "Several displays" in link.error
    link = BleCompanionLink(state, device_id="second-id", runtime_factory=lambda: runtime)
    try:
        await link.connect()
        assert link.ready
    finally:
        await link.close()


async def test_stop_during_connect_does_not_deadlock():
    runtime = Runtime()
    runtime.peripheral.silent = True
    link = BleCompanionLink(state, runtime_factory=lambda: runtime)
    task = link.launch(link.connect())
    await until(lambda: link.client is not None)
    stop = link.request_stop()
    try:
        await asyncio.wait_for(stop, 2)
    except ConnectionError:
        pass  # Disconnect locally fences the firmware; no ACK can be claimed.
    assert task.cancelled() and not runtime.peripheral.is_connected
    await link.close()


def test_bench_defaults_to_ble_with_no_bluetooth_side_effects():
    settings = Settings(mode="hardware", bench_enabled=True)
    bench = BenchControl(settings, CameraHub(settings), bridge=FakeBridge())
    assert isinstance(bench.scooper, BleCompanionLink)
    assert bench.snapshot()["bench"]["scooper"]["transport"] == "ble"


async def test_viewers_cannot_connect_or_move_companion():
    from bullseye.mission import CommandError
    settings = Settings(mode="hardware", bench_enabled=True)
    bench = BenchControl(settings, CameraHub(settings), bridge=FakeBridge())
    for command in ("scooper_connect", "scooper_squeeze", "scooper_release"):
        with pytest.raises(CommandError, match="Claim pilot"):
            await bench.command("viewer", command, {})
    assert bench.scooper.worker is None
