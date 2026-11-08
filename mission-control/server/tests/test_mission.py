import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from bullseye.adapters import HardwareStub, Simulator
from bullseye.camera import CameraHub
from bullseye.config import Settings
from bullseye.mission import CommandError, MissionControl


async def wait_until(predicate, timeout=1):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.005)


@pytest_asyncio.fixture
async def control():
    settings = Settings(search_seconds=.015, approach_seconds=.015, collection_seconds=.025, lease_seconds=2)
    camera = CameraHub(settings)
    camera.publish(camera.render_scene())
    controller = MissionControl(settings, Simulator(settings), camera)
    await controller.start()
    yield controller
    await controller.close()


async def launch_to_review(control):
    await control.command("pilot", "claim", {})
    await control.command("pilot", "start", {})
    await wait_until(lambda: control.phase == "awaiting_confirmation")
    return dict(control.review)


def review_payload(review):
    return {"review_id": review["id"], "mission_id": review["mission_id"]}


async def test_ping_is_non_mutating_for_viewer_and_pilot(control):
    control.adapter.stop = AsyncMock(wraps=control.adapter.stop)
    before = control.snapshot()
    await control.command("viewer", "ping", {})
    # Frame age varies with time; everything the coordinator owns is unchanged.
    after = control.snapshot()
    before.pop("camera")
    after.pop("camera")
    assert before == after
    await control.command("pilot", "claim", {})
    deadline, events = control.lease_until, list(control.events)
    await control.command("pilot", "ping", {})
    await control.command("viewer", "ping", {})
    assert control.pilot == "pilot"
    assert control.lease_until == deadline and list(control.events) == events
    control.adapter.stop.assert_not_awaited()


async def test_pings_do_not_prevent_pilot_lease_expiry(control):
    control.settings = replace(control.settings, lease_seconds=.015)
    await control.command("pilot", "claim", {})
    for _ in range(12):
        await control.command("pilot", "ping", {})
        await control.command("viewer", "ping", {})
        await asyncio.sleep(.005)
    assert control.pilot is None and control.phase == "stopped"


async def test_full_mission_rejects_stale_and_duplicate_decisions(control):
    await control.command("pilot", "claim", {})
    await control.command("pilot", "select_target", {"color": "green"})
    old = await launch_to_review(control)
    frozen = control.snapshots[old["id"]]
    control.camera.publish(b"new camera frame")
    assert control.snapshots[old["id"]] == frozen
    await control.command("pilot", "reject", review_payload(old))
    await wait_until(lambda: control.phase == "awaiting_confirmation")
    current = dict(control.review)
    assert current["id"] != old["id"]
    with pytest.raises(CommandError, match="no longer current"):
        await control.command("pilot", "confirm", review_payload(old))
    with pytest.raises(CommandError, match="Claim pilot"):
        await control.command("viewer", "confirm", review_payload(current))
    await control.command("pilot", "confirm", review_payload(current))
    assert control.phase == "collecting"
    assert control.adapter.led_color is None
    with pytest.raises(CommandError):
        await control.command("pilot", "confirm", review_payload(current))
    await wait_until(lambda: control.phase == "collected")
    assert control.adapter.led_color == "green"
    await control.command("pilot", "return", {})
    await control.command("pilot", "drive", {"linear": .25, "angular": 0})
    await control.command("pilot", "mark_home", {})
    assert control.phase == "completed"
    assert control.motion == {"linear": 0, "angular": 0}
    assert control.finished_at


async def test_atomic_pilot_claim_and_public_stop(control):
    outcomes = await asyncio.gather(control.command("one", "claim", {}), control.command("two", "claim", {}), return_exceptions=True)
    assert sum(isinstance(result, CommandError) for result in outcomes) == 1
    pilot = control.pilot
    await control.command(pilot, "drive", {"linear": .5, "angular": 0})
    await control.command("viewer", "stop", {})
    assert control.phase == "stopped"
    assert control.adapter.motion == (0, 0)
    with pytest.raises(CommandError):
        await control.command(pilot, "drive", {"linear": .5, "angular": 0})
    await control.command(pilot, "reset", {})
    assert control.phase == "idle"


async def test_drive_expires_without_more_commands(control):
    control.settings = replace(control.settings, drive_seconds=.03)
    await control.command("pilot", "claim", {})
    await control.command("pilot", "drive", {"linear": .5, "angular": 0})
    await wait_until(lambda: control.motion["linear"] == 0)
    assert control.adapter.motion == (0, 0)
    assert control.phase == "idle"


async def test_lease_expiry_cancels_navigation_and_does_not_resume(control):
    control.settings = replace(control.settings, lease_seconds=.01)
    await control.command("pilot", "claim", {})
    await control.command("pilot", "start", {})
    await wait_until(lambda: control.phase == "stopped")
    await asyncio.sleep(.05)
    assert control.review is None and control.pilot is None
    await control.command("new", "claim", {})
    assert control.phase == "stopped"
    with pytest.raises(CommandError):
        await control.command("pilot", "heartbeat", {})


async def test_disconnect_stops_and_collection_retry_requires_failure(control):
    review = await launch_to_review(control)
    control.adapter.fail_next_collection = True
    await control.command("pilot", "confirm", review_payload(review))
    with pytest.raises(CommandError):
        await control.command("pilot", "retry_collection", {})
    await wait_until(lambda: control.collection_error is not None)
    assert control.phase == "collecting" and control.adapter.led_color is None
    await control.command("pilot", "retry_collection", {})
    await wait_until(lambda: control.phase == "collected")
    await control.disconnect("pilot")
    assert control.phase == "stopped" and control.pilot is None


async def test_stop_during_collection_cannot_finish_later(control):
    review = await launch_to_review(control)
    await control.command("pilot", "confirm", review_payload(review))
    await control.stop()
    await asyncio.sleep(.06)
    assert control.phase == "stopped"
    assert control.adapter.led_color is None


@pytest.mark.parametrize("linear,angular", [(2, 0), (float('nan'), 0), (True, 0), ("1", 0), (0, None)])
async def test_invalid_drive_input_rejected(control, linear, angular):
    await control.command("pilot", "claim", {})
    with pytest.raises(CommandError):
        await control.command("pilot", "drive", {"linear": linear, "angular": angular})
    assert control.adapter.motion == (0, 0)


async def test_hardware_stub_never_claims_movement_or_stop():
    settings = Settings(mode="hardware")
    control = MissionControl(settings, HardwareStub(), CameraHub(settings))
    await control.command("pilot", "claim", {})
    for kind, payload in [("start", {}), ("drive", {"linear": .2, "angular": 0})]:
        with pytest.raises(CommandError, match="not connected"):
            await control.command("pilot", kind, payload)
    await control.stop()
    assert control.last_stop_acknowledged is False
    assert control.snapshot()["distance_cm"] is None
    assert control.camera.status()["status"] == "unconfigured"
