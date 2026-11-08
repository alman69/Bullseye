import asyncio

import httpx
import pytest
import pytest_asyncio

from bullseye.bench_demo import BenchControl
from bullseye.camera import CameraHub
from bullseye.config import Settings
from bullseye.mission import CommandError
from bullseye.scooper import ScooperLink
from test_bench_demo import FakeBridge, arm, settle


class FakeScooper:
    """Answers like bullseye_scooper.ino's HTTP API on the ELEGOO Wi-Fi."""

    def __init__(self, *, refuse=None):
        self.refuse = refuse or {}
        self.reachable = True
        self.commands = []
        self.polls = 0
        self.state, self.sequence, self.step = "holding", None, None
        self.angles = {"left": 90, "right": 90}

    def status(self):
        return {"state": self.state, "sequence": self.sequence, "step": self.step, "angles": dict(self.angles)}

    def __call__(self, request):
        if not self.reachable:
            raise httpx.ConnectTimeout("timed out", request=request)
        if request.method == "GET" and request.url.path == "/status":
            self.polls += 1
            return httpx.Response(200, json=self.status())
        if request.method == "POST" and request.url.path == "/command":
            name = request.url.params.get("name")
            self.commands.append(name)
            if name in self.refuse:
                return httpx.Response(409, json={"ok": False, "error": self.refuse[name], "status": self.status()})
            if name == "squeeze":
                self.state, self.sequence, self.step = "sequence", "squeeze", "out"
                self.angles = {"left": 60, "right": 60}
            elif name in {"stop", "home"}:
                self.state, self.sequence, self.step = "holding", None, None
            return httpx.Response(200, json={"ok": True, "status": self.status()})
        return httpx.Response(404, json={"ok": False, "error": "not found"})


def link_for(fake, url="http://192.168.4.50"):
    return ScooperLink(url, transport=httpx.MockTransport(fake))


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.005)


async def test_connect_reads_status_and_reports_address():
    fake = FakeScooper()
    link = link_for(fake)
    await link.connect()
    assert link.ready and fake.polls == 1
    snapshot = link.snapshot()
    assert snapshot["address"] == "http://192.168.4.50"
    assert snapshot["state"] == "holding" and snapshot["angles"] == {"left": 90, "right": 90}
    await link.close()
    assert link.status == "disconnected"


async def test_unreachable_scooper_explains_wifi():
    fake = FakeScooper()
    fake.reachable = False
    link = link_for(fake)
    await link.connect()
    assert link.status == "error"
    assert "192.168.4.50" in link.error and "Wi-Fi" in link.error


async def test_squeeze_returns_ok_and_applies_reported_status():
    fake = FakeScooper()
    link = link_for(fake)
    await link.connect()
    assert await link.command("squeeze") is True
    assert fake.commands == ["squeeze"]
    assert link.state == "sequence" and link.step == "out" and link.angles["left"] == 60
    assert link.busy is None
    await link.close()


async def test_firmware_refusal_is_returned_not_raised():
    link = link_for(FakeScooper(refuse={"home": "busy with a sequence; send stop first"}))
    await link.connect()
    assert await link.command("home") == "busy with a sequence; send stop first"
    assert link.ready and "busy" in link.error
    await link.close()


async def test_polling_picks_up_progress(monkeypatch):
    monkeypatch.setattr("bullseye.scooper.POLL_S", .01)
    fake = FakeScooper()
    link = link_for(fake)
    await link.connect()
    fake.state, fake.sequence, fake.step = "sequence", "squeeze", "in"
    await wait_for(lambda: link.step == "in")
    await link.close()


async def test_lost_wifi_marks_link_error(monkeypatch):
    monkeypatch.setattr("bullseye.scooper.POLL_S", .01)
    fake = FakeScooper()
    link = link_for(fake)
    await link.connect()
    fake.reachable = False  # Scooper powered off or out of range.
    await wait_for(lambda: link.status == "error")
    assert "stopped answering" in link.error and link.state is None


async def test_failed_command_raises_and_marks_error():
    fake = FakeScooper()
    link = link_for(fake)
    await link.connect()
    fake.reachable = False
    with pytest.raises(ConnectionError, match="did not answer 'squeeze'"):
        await link.command("squeeze")
    assert link.status == "error"
    await link.close()


async def test_unknown_command_never_reaches_the_scooper():
    fake = FakeScooper()
    link = link_for(fake)
    await link.connect()
    with pytest.raises(ValueError):
        await link.command("set 1 0")
    assert fake.commands == []
    await link.close()


def test_scooper_url_setting_is_validated():
    assert Settings().scooper_url == "http://192.168.4.50"
    with pytest.raises(ValueError, match="SCOOPER_URL"):
        Settings(scooper_url="COM5")


@pytest_asyncio.fixture
async def scooper_bench():
    fake = FakeScooper()
    settings = Settings(mode="hardware", bench_enabled=True)
    controller = BenchControl(settings, CameraHub(settings), FakeBridge(), scooper=link_for(fake))
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
    assert fake.commands == []
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
    assert fake.commands == ["squeeze"]
    assert bench.snapshot()["bench"]["scooper"]["step"] == "out"
    assert any("Squeeze started" in event["message"] for event in bench.events)


async def test_bench_rejects_overlapping_scooper_commands(scooper_bench):
    bench, _ = scooper_bench
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
    await wait_for(lambda: "stop" in fake.commands)
    assert fake.commands[-1] == "stop"
