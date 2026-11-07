import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
import math
import time
from uuid import uuid4

from .adapters import RoverAdapter
from .camera import CameraHub
from .config import Settings


COLORS = {"red", "green", "blue"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class CommandError(ValueError):
    pass


class MissionControl:
    def __init__(self, settings: Settings, adapter: RoverAdapter, camera: CameraHub):
        self.settings, self.adapter, self.camera = settings, adapter, camera
        self.lock = asyncio.Lock()
        self.phase = "idle"
        self.phase_at = time.monotonic()
        self.target = "red"
        self.mission_id = None
        self.started_at = None
        self.finished_at = None
        self.review = None
        self.collection_error = None
        self.last_stop_acknowledged = None
        self.pilot = None
        self.lease_until = 0.0
        self.motion = {"linear": 0.0, "angular": 0.0}
        self.drive_until = 0.0
        self.events = deque(maxlen=100)
        self.snapshots: OrderedDict[str, bytes] = OrderedDict()
        self.subscribers: set[asyncio.Queue] = set()
        self.jobs: set[asyncio.Task] = set()
        self.watchdog = None
        self.log("Mission Control online. Simulator ready." if settings.mode == "simulation"
                 else "Hardware mode. Waiting for a rover adapter.")

    def log(self, message, level="info"):
        self.events.appendleft({"id": uuid4().hex, "at": utc_now(), "message": message, "level": level})

    def set_phase(self, phase):
        self.phase, self.phase_at = phase, time.monotonic()

    def snapshot(self):
        return {
            "mode": self.settings.mode, "phase": self.phase, "target": self.target,
            "mission_id": self.mission_id, "started_at": self.started_at,
            "finished_at": self.finished_at, "review": self.review,
            "collection_error": self.collection_error,
            "capabilities": self.adapter.capabilities,
            "rover": "simulated" if self.settings.mode == "simulation" else "connected" if self.adapter.connected else "not_connected",
            "camera": self.camera.status(), "pilot_id": self.pilot,
            "led_color": self.adapter.led_color, "motion": dict(self.motion),
            "distance_cm": (18 if self.phase in {"awaiting_confirmation", "collecting", "collected"} else 120)
                           if self.settings.mode == "simulation" else self.adapter.distance_cm,
            "last_stop_acknowledged": self.last_stop_acknowledged,
            "events": list(self.events),
        }

    def publish(self):
        self.camera.scene = {"phase": self.phase, "target": self.target,
                             "phase_age": time.monotonic() - self.phase_at}
        state = {"type": "state", "state": self.snapshot()}
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(state)

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait({"type": "state", "state": self.snapshot()})
        return queue

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)

    def spawn(self, operation):
        task = asyncio.create_task(operation)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        return task

    async def start(self):
        self.watchdog = asyncio.create_task(self._watch())

    async def close(self):
        jobs = list(self.jobs)
        if self.watchdog:
            jobs.append(self.watchdog)
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await self.adapter.stop()

    async def _halt(self, reason):
        for task in list(self.jobs):
            if task is not asyncio.current_task():
                task.cancel()
        self.motion = {"linear": 0.0, "angular": 0.0}
        self.drive_until = 0
        self.review = None
        self.collection_error = None
        self.last_stop_acknowledged = None
        self.set_phase("stopped")
        self.finished_at = utc_now()
        try:
            self.last_stop_acknowledged = await self.adapter.stop()
        except Exception:
            self.last_stop_acknowledged = False
        suffix = "" if self.last_stop_acknowledged else " Hardware stop is unverified."
        self.log(reason + suffix, "warning")

    async def stop(self, reason="STOP requested by a visitor."):
        async with self.lock:
            await self._halt(reason)
            self.publish()
            return self.snapshot()

    async def disconnect(self, session_id):
        async with self.lock:
            if self.pilot == session_id:
                self.pilot = None
                await self._halt("Pilot disconnected. Software stop latched.")
                self.publish()

    async def command(self, session_id: str, kind: str, payload: dict):
        async with self.lock:
            now = time.monotonic()
            if self.pilot and now > self.lease_until:
                self.pilot = None
                await self._halt("Pilot heartbeat expired. Software stop latched.")
                self.publish()
            if kind == "claim":
                if self.pilot and self.pilot != session_id:
                    raise CommandError("Another pilot has the controls.")
                if self.pilot != session_id:
                    self.log("Pilot controls claimed.")
                self.pilot, self.lease_until = session_id, now + self.settings.lease_seconds
            elif kind == "stop":
                await self._halt("STOP requested by a visitor.")
            else:
                if self.pilot != session_id:
                    raise CommandError("Claim pilot controls first.")
                if kind == "heartbeat":
                    self.lease_until = now + self.settings.lease_seconds
                    return
                if kind == "release":
                    self.pilot = None
                    await self._halt("Pilot released controls. Software stop latched.")
                elif kind == "reset":
                    if self.phase != "stopped":
                        raise CommandError("Reset is available after STOP.")
                    self.set_phase("idle")
                    self.mission_id = self.review = self.started_at = self.finished_at = None
                    self.collection_error = None
                    self.log("Stop reset. Ready for a new mission.")
                elif kind == "select_target":
                    self.require_phase("idle", "completed")
                    color = payload.get("color")
                    if color not in COLORS:
                        raise CommandError("Choose red, green, or blue.")
                    self.target = color
                elif kind == "start":
                    self.require_capability("mission")
                    self.require_phase("idle", "completed")
                    # Clear manual movement before handing control to navigation.
                    await self.adapter.stop()
                    self.motion = {"linear": 0.0, "angular": 0.0}
                    self.drive_until = 0
                    self.mission_id = uuid4().hex
                    self.started_at, self.finished_at = utc_now(), None
                    self.review = self.collection_error = None
                    self.set_phase("searching")
                    self.log(f"Mission launched. Searching for the {self.target} sample.")
                    self.spawn(self._search(self.mission_id))
                elif kind in {"confirm", "reject"}:
                    self.require_phase("awaiting_confirmation")
                    if not self.review or payload.get("review_id") != self.review["id"] or payload.get("mission_id") != self.mission_id:
                        raise CommandError("This sample review is no longer current.")
                    if kind == "confirm":
                        self.require_capability("collection")
                        self.set_phase("collecting")
                        self.log("Sample confirmed by pilot. Requesting RGB collection acknowledgement.")
                        self.spawn(self._collect(self.mission_id))
                    else:
                        self.review = None
                        self.set_phase("searching")
                        self.log("Candidate rejected. Resuming sample search.")
                        self.spawn(self._search(self.mission_id))
                elif kind == "retry_collection":
                    self.require_phase("collecting")
                    if not self.collection_error:
                        raise CommandError("Collection is already in progress.")
                    self.collection_error = None
                    self.log("Retrying sample collection.")
                    self.spawn(self._collect(self.mission_id))
                elif kind == "return":
                    self.require_capability("return")
                    self.require_phase("collected")
                    await self.adapter.begin_return()
                    self.set_phase("returning")
                    self.log("Assisted return started. Drive home, then select Mark Home.")
                elif kind == "mark_home":
                    self.require_phase("returning")
                    await self.adapter.stop()
                    self.motion = {"linear": 0.0, "angular": 0.0}
                    self.drive_until = 0
                    self.set_phase("completed")
                    self.finished_at = utc_now()
                    self.log("Home arrival confirmed by pilot. Mission complete.", "success")
                elif kind == "drive":
                    self.require_capability("manual")
                    self.require_phase("idle", "completed", "returning")
                    linear, angular = payload.get("linear"), payload.get("angular")
                    for value in (linear, angular):
                        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or abs(value) > 1:
                            raise CommandError("Drive values must be finite numbers between -1 and 1.")
                    await self.adapter.drive(linear, angular, round(self.settings.drive_seconds * 1000))
                    self.motion = {"linear": linear, "angular": angular}
                    self.drive_until = now + self.settings.drive_seconds if linear or angular else 0
                else:
                    raise CommandError("Unknown command.")
            self.publish()

    def require_phase(self, *phases):
        if self.phase not in phases:
            raise CommandError("That action is unavailable in the current mission stage.")

    def require_capability(self, capability):
        if not self.adapter.capabilities.get(capability):
            raise CommandError("Hardware adapter not connected. This action is unavailable.")

    async def _search(self, mission_id):
        try:
            async for progress in self.adapter.search(self.target):
                async with self.lock:
                    if self.mission_id != mission_id or self.phase not in {"searching", "approaching"}:
                        return
                    if progress == "approaching":
                        self.set_phase("approaching")
                        self.log("Candidate located. Approaching the selected sample.")
                    elif progress == "candidate":
                        await self.adapter.stop()
                        self.motion = {"linear": 0.0, "angular": 0.0}
                        self.set_phase("awaiting_confirmation")
                        review_id = uuid4().hex
                        frame = self.camera.latest if self.camera.status()["status"] == "streaming" else None
                        if frame:
                            self.snapshots[review_id] = frame
                            while len(self.snapshots) > 20:
                                self.snapshots.popitem(last=False)
                        self.review = {"id": review_id, "mission_id": mission_id,
                                       "captured_at": utc_now(), "requested_color": self.target,
                                       "image_url": f"/api/reviews/{review_id}/image" if frame else None,
                                       "simulated": self.settings.mode == "simulation",
                                       "image_source": self.camera.source}
                        self.log("Sample found. Stopped and waiting for pilot confirmation.")
                    self.publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.stop("Navigation failed. Software stop latched.")

    async def _collect(self, mission_id):
        try:
            await self.adapter.collect(self.target)
            async with self.lock:
                if self.mission_id != mission_id or self.phase != "collecting":
                    return
                self.set_phase("collected")
                self.collection_error = None
                self.log(f"Collection acknowledged. Sample indicator set to {self.target}.", "success")
                self.publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self.lock:
                if self.mission_id == mission_id and self.phase == "collecting":
                    self.collection_error = "Collection was not acknowledged. Retry or press STOP."
                    self.log(self.collection_error, "warning")
                    self.publish()

    async def _watch(self):
        last_publish = 0.0
        while True:
            await asyncio.sleep(0.05)
            async with self.lock:
                now = time.monotonic()
                if self.pilot and now > self.lease_until:
                    self.pilot = None
                    await self._halt("Pilot heartbeat expired. Software stop latched.")
                elif self.drive_until and now > self.drive_until:
                    self.drive_until = 0
                    try:
                        await self.adapter.stop()
                        self.motion = {"linear": 0.0, "angular": 0.0}
                    except Exception:
                        await self._halt("Drive timeout stop was not acknowledged.")
                if now - last_publish >= 0.25:
                    self.publish()
                    last_publish = now
