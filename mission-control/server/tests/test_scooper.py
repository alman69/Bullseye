import asyncio
import queue

import pytest
import pytest_asyncio

from bullseye.bench_demo import BenchControl
from bullseye.camera import CameraHub
from bullseye.config import Settings
from bullseye.mission import CommandError
from bullseye.scooper import ScooperLink, parse_status
from test_bench_demo import FakeBridge, arm, settle


class FakeSerial:
    """Speaks the bullseye_scooper.ino line protocol from a background reader thread."""

    def __init__(self, *, silent=False, refuse=None):
        self.lines = queue.Queue()
        self.sent = []
        self.silent = silent
        self.refuse = refuse or {}
        self.closed = False
        self.state, self.angles = "holding", {"left": 90, "right": 90}
        self.lines.put(b"Bullseye scooper. boot chatter\r\n")

    def status_line(self, step=None):
        extra = f" {step}" if step else ""
        return f"state={self.state}{extra} left={self.angles['left']} right={self.angles['right']}\r\n".encode()

    def write(self, data):
        command = data.decode().strip()
        self.sent.append(command)
        if self.silent:
            return len(data)
        if command in self.refuse:
            self.lines.put(f"error: {self.refuse[command]}\r\n".encode())
        elif command == "status":
            self.lines.put(self.status_line())
            self.lines.put(b"ok\r\n")
        else:
            self.lines.put(b"ok\r\n")
            if command == "squeeze":
                self.state = "sequence"
                self.angles = {"left": 60, "right": 60}
                self.lines.put(self.status_line("squeeze:out"))
        return len(data)

    def readline(self):
        if self.closed:
            raise OSError("port closed")
        try:
            return self.lines.get(timeout=.02)
        except queue.Empty:
            return b""

    def close(self):
        self.closed = True


def link_with(fake, ports=None):
    return ScooperLink(opener=lambda port: fake,
                       list_ports=lambda: ports if ports is not None else [("COM7", 0x1A86, "USB-Enhanced-SERIAL CH343")])


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.005)


def test_parse_status_lines():
    assert parse_status("state=sequence squeeze:out left=60 right=60") == {
        "state": "sequence", "sequence": "squeeze", "step": "out", "angles": {"left": 60, "right": 60}}
    assert parse_status("state=holding left=90 right=90")["sequence"] is None
    assert parse_status("Commands:") is None
    assert parse_status("state=holding left=x") is None


async def test_connect_auto_picks_the_only_esp32_port_and_reads_status():
    fake = FakeSerial()
    link = link_with(fake, [("COM3", None, "Bluetooth"), ("COM7", 0x1A86, "CH343")])
    await link.connect()
    assert link.ready and link.port == "COM7"
    assert fake.sent == ["status"]
    assert link.state == "holding" and link.angles == {"left": 90, "right": 90}
    await link.close()
    assert fake.closed and link.status == "disconnected"


async def test_connect_explains_missing_or_ambiguous_ports():
    link = link_with(FakeSerial(), [("COM3", None, "Bluetooth")])
    await link.connect()
    assert link.status == "error" and "SCOOPER_PORT" in link.error and "COM3" in link.error
    link = link_with(FakeSerial(), [("COM7", 0x1A86, "a"), ("COM8", 0x10C4, "b")])
    await link.connect()
    assert link.status == "error" and "Several" in link.error


async def test_connect_fails_when_firmware_never_answers(monkeypatch):
    monkeypatch.setattr("bullseye.scooper.REPLY_TIMEOUT_S", .05)
    fake = FakeSerial(silent=True)
    link = link_with(fake)
    await link.connect()
    assert link.status == "error" and "bullseye_scooper" in link.error
    assert fake.closed


async def test_squeeze_reports_ok_and_progress():
    fake = FakeSerial()
    link = link_with(fake)
    await link.connect()
    assert await link.command("squeeze") is True
    await wait_for(lambda: link.step == "out")
    assert link.state == "sequence" and link.sequence == "squeeze" and link.angles["left"] == 60
    assert link.busy is None
    await link.close()


async def test_firmware_refusal_is_returned_not_raised():
    link = link_with(FakeSerial(refuse={"home": "busy with a sequence; send stop first"}))
    await link.connect()
    assert await link.command("home") == "busy with a sequence; send stop first"
    assert link.ready and "busy" in link.error
    await link.close()


async def test_lost_port_marks_link_error():
    fake = FakeSerial()
    link = link_with(fake)
    await link.connect()
    fake.closed = True  # Unplugged: readline starts raising.
    await wait_for(lambda: link.status == "error")
    assert "lost" in link.error and link.state is None


async def test_unknown_command_never_reaches_the_port():
    fake = FakeSerial()
    link = link_with(fake)
    await link.connect()
    with pytest.raises(ValueError):
        await link.command("set 1 0")
    assert fake.sent == ["status"]
    await link.close()


@pytest_asyncio.fixture
async def scooper_bench():
    fake = FakeSerial()
    settings = Settings(mode="hardware", bench_enabled=True)
    controller = BenchControl(settings, CameraHub(settings), FakeBridge(), scooper=link_with(fake))
    yield controller, fake
    await controller.close()


async def settle_scooper(control):
    async with asyncio.timeout(2):
        while control.scooper.task and not control.scooper.task.done():
            await asyncio.sleep(.005)


async def test_bench_squeeze_needs_pilot_and_connected_link(scooper_bench):
    bench, fake = scooper_bench
    with pytest.raises(CommandError, match="Claim"):
        await bench.command("viewer", "scooper_squeeze", {})
    await bench.command("pilot", "claim", {})
    with pytest.raises(CommandError, match="Connect the scooper"):
        await bench.command("pilot", "scooper_squeeze", {})
    assert fake.sent == []
    assert bench.snapshot()["bench"]["scooper"]["status"] == "disconnected"


async def test_bench_squeeze_works_without_enabling_rover_controls(scooper_bench):
    bench, fake = scooper_bench
    await bench.command("pilot", "claim", {})
    await bench.command("pilot", "scooper_connect", {})
    await settle_scooper(bench)
    assert bench.snapshot()["bench"]["scooper"]["status"] == "ready"
    assert not bench.armed
    await bench.command("pilot", "scooper_squeeze", {})
    await settle_scooper(bench)
    assert fake.sent == ["status", "squeeze"]
    await wait_for(lambda: bench.snapshot()["bench"]["scooper"]["step"] == "out")
    assert any("Squeeze started" in event["message"] for event in bench.events)


async def test_bench_rejects_overlapping_scooper_commands(scooper_bench):
    bench, fake = scooper_bench
    await bench.command("pilot", "claim", {})
    await bench.command("pilot", "scooper_connect", {})
    with pytest.raises(CommandError, match="still answering"):
        await bench.command("pilot", "scooper_connect", {})
    await settle_scooper(bench)


async def test_bench_stop_also_freezes_the_scooper(scooper_bench):
    bench, fake = scooper_bench
    await arm(bench)
    await bench.command("pilot", "scooper_connect", {})
    await settle_scooper(bench)
    await bench.stop()
    await settle(bench)
    await wait_for(lambda: "stop" in fake.sent)
    assert fake.sent[-1] == "stop"
