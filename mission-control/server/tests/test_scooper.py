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
    with pytest.raises(CommandError, match="retired"):
        await bench.command("pilot", "scooper_squeeze", {})
    assert fake.commands == []
    assert bench.snapshot()["bench"]["scooper"]["status"] == "disconnected"


@pytest.mark.parametrize("command", ["scooper_connect", "scooper_squeeze", "scooper_release", "scooper_home"])
async def test_retired_servo_commands_never_reach_hardware(scooper_bench, command):
    bench, fake = scooper_bench
    await bench.command("pilot", "claim", {})
    await bench.scooper.connect()  # even a legacy connection must not bypass the command guard
    with pytest.raises(CommandError, match="retired"):
        await bench.command("pilot", command, {})
    assert not fake.commands


async def test_bench_stop_still_stops_legacy_attached_scooper(scooper_bench):
    bench, fake = scooper_bench
    await arm(bench)
    await bench.scooper.connect()
    await bench.stop()
    await settle(bench)
    await wait_for(lambda: "stop" in fake.commands)
    assert fake.commands[-1] == "stop"


class OrderedScooper(FakeScooper):
    """Combined firmware protocol: board epoch plus increasing command IDs."""
    def __init__(self):
        super().__init__()
        self.boot = 123
        self.counter = 0
        self.rejected = []
        self.servo_ready = True

    def status(self):
        return {**super().status(), "firmware": "bullseye-companion", "api": 2,
                "boot_id": self.boot, "command_seq": self.counter, "servo_ready": self.servo_ready}

    def __call__(self, request):
        if request.method == "POST":
            sequence = int(request.url.params["seq"])
            if int(request.url.params["boot"]) != self.boot or sequence <= self.counter:
                self.rejected.append(request.url.params["name"])
                return httpx.Response(409, json={"ok": False, "error": "old command or board restarted; reconnect", "status": self.status()})
            self.counter = sequence
        return super().__call__(request)


async def test_combined_firmware_uses_ordered_commands_and_discards_old_status():
    fake = OrderedScooper(); link = link_for(fake)
    try:
        await link.connect()
        assert link.snapshot()["ordered_commands"] and link.snapshot()["firmware"] == "bullseye-companion"
        await link.command("squeeze")
        old = fake.status()
        await link.command("stop")
        assert fake.counter == 2 and link.state == "holding"
        assert link._apply(old) is False and link.state == "holding"
    finally:
        await link.close()


async def test_stop_cancels_inflight_request_and_late_squeeze_is_rejected():
    fake = OrderedScooper(); sent = asyncio.Event(); deliver_late = asyncio.Event(); late = []
    async def transport(request):
        if request.method == "POST" and request.url.params["name"] == "squeeze":
            sent.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                async def network_packet_already_sent():
                    await deliver_late.wait()
                    return fake(request)
                late.append(asyncio.create_task(network_packet_already_sent()))
                raise
        return fake(request)
    link = ScooperLink(transport=httpx.MockTransport(transport))
    try:
        await link.connect()
        link.launch(link.command("squeeze"))
        await sent.wait()
        stop = link.request_stop()
        assert link.snapshot()["busy"] == "stop"
        with pytest.raises(RuntimeError):
            job = link.command("home")
            try: link.launch(job)
            finally: job.close()
        assert await stop is True
        deliver_late.set()
        assert (await late[0]).status_code == 409
        assert fake.commands == ["stop"] and fake.rejected == ["squeeze"]
        assert link.state == "holding" and link.snapshot()["busy"] is None
    finally:
        deliver_late.set()
        await asyncio.gather(*late, return_exceptions=True)
        await link.close()


async def test_stop_before_a_queued_job_runs_never_sends_motion():
    fake = OrderedScooper(); link = link_for(fake)
    try:
        await link.connect()
        link.launch(link.command("squeeze"))
        assert await link.request_stop() is True
        assert fake.commands == ["stop"]
    finally:
        await link.close()


async def test_board_restart_requires_reconnect_and_never_replays_command():
    fake = OrderedScooper(); link = link_for(fake)
    try:
        await link.connect()
        fake.boot += 1
        with pytest.raises(ConnectionError, match="restarted"):
            await link.command("home")
        assert not link.ready and fake.commands == []
        await link.connect()
        assert fake.commands == []
        assert await link.command("home") is True
        assert fake.commands == ["home"]
    finally:
        await link.close()


async def test_watchdog_response_does_not_leave_busy_stuck():
    fake = OrderedScooper()
    def transport(request):
        response = fake(request)
        if request.method == "POST":
            fake.counter += 1  # The local watchdog fenced a queued request.
            return httpx.Response(409, json={"ok": False, "error": "old command", "status": fake.status()})
        return response
    link = ScooperLink(transport=httpx.MockTransport(transport))
    try:
        await link.connect()
        assert await link.command("home") == "old command"
        assert link.command_sequence == 2 and link.busy is None
    finally:
        await link.close()


async def test_pwm_failure_does_not_enable_scooper_controls():
    fake = OrderedScooper(); fake.servo_ready = False
    link = link_for(fake)
    await link.connect()
    assert not link.ready and "PWM initialization failed" in link.error
    assert fake.commands == []
    await link.close()


async def test_connect_stops_existing_motion_instead_of_continuing_it():
    fake = OrderedScooper()
    fake.state, fake.sequence, fake.step = "sequence", "squeeze", "out"
    link = link_for(fake)
    try:
        await link.connect()
        assert link.ready and link.state == "holding" and fake.commands == ["stop"]
    finally:
        await link.close()


def test_watchdog_warning_clears_only_after_device_clears_its_flag():
    fake = FakeScooper()
    link = link_for(fake)
    link._apply({**fake.status(), "watchdog_stopped": True})
    assert "losing controller contact" in link.error
    link._apply({**fake.status(), "watchdog_stopped": False})
    assert link.error is None
    link.error = "Some unrelated failure"
    link._apply({**fake.status(), "watchdog_stopped": False})
    assert link.error == "Some unrelated failure"
