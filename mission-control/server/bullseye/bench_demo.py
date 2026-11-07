"""Opt-in, wheels-raised factory-firmware demo. No continuous drive or autonomy."""
import asyncio
from collections import deque
import time
from uuid import uuid4

from .elegoo_bench import DIRECTIONS, validate_pulse
from .elegoo_probe import FrameParser
from .mission import CommandError, utc_now


class FactoryBridge:
    """One TCP connection, one reader, atomic writes; never reconnect/replay motion."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.status = "disconnected"
        self.error = None
        self.distance_cm = None
        self.heartbeats = 0
        self.last_stop_acknowledged = None
        self.writer = self.reader_task = self.stop_task = None
        self.waiters = {}
        self.write_lock = asyncio.Lock()

    @property
    def connected(self):
        return self.status == "ready"

    def fail(self, message):
        self.status, self.error = "disconnected", message
        if self.writer:
            self.writer.close()
            self.writer = None
        for future in list(self.waiters.values()):
            if not future.done():
                future.set_exception(ConnectionError(message))

    async def close(self):
        self.fail("Bridge disconnected. Reconnect explicitly; movement is never replayed.")
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
            self.reader_task = None
        if self.stop_task and self.stop_task is not asyncio.current_task():
            await asyncio.gather(self.stop_task, return_exceptions=True)
            self.stop_task = None

    async def connect(self):
        await self.close()
        self.status, self.error = "connecting", None
        self.last_stop_acknowledged = None
        self.distance_cm = None
        stage = "ESP32 TCP connection"
        try:
            reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 3)
            self.reader_task = asyncio.create_task(self.read(reader))
            stage = "UNO STOP acknowledgement"
            if not await self.stop():
                raise ConnectionError("UNO did not acknowledge the connection STOP.")
            stage = "unique UNO sensor reply"
            request_id = "p" + uuid4().hex[:8]
            prefix = "{" + request_id + "_"
            frame = await self.request({"N": 21, "D1": 2, "H": request_id}, prefix, 5)
            value = frame[len(prefix):-1]
            if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= 150:
                raise ConnectionError("UNO sensor reply did not match the expected protocol.")
            # Zero is unknown/no echo. Neither value is used for avoidance here.
            self.distance_cm = int(value) or None
            self.status = "ready"
        except BaseException as error:
            await self.close()
            self.error = "Connection cancelled; steps remain disabled." if isinstance(error, asyncio.CancelledError) else (
                f"Connection check failed at {stage}. Verify ELEGOO Wi-Fi, Cam, power, and no other controller."
            )
            raise

    async def send(self, command):
        import json
        async with self.write_lock:
            writer = self.writer
            if writer is None:
                raise ConnectionError("ESP32 command bridge is disconnected.")
            writer.write(json.dumps(command, separators=(",", ":")).encode("ascii"))
            await asyncio.wait_for(writer.drain(), .25)

    async def request(self, command, key, timeout):
        if key in self.waiters:
            raise RuntimeError("Overlapping vendor reply waiters")
        future = asyncio.get_running_loop().create_future()
        self.waiters[key] = future
        try:
            await self.send(command)
            return await asyncio.wait_for(future, timeout)
        finally:
            self.waiters.pop(key, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Retrieve any failure during cancellation.

    async def read(self, reader):
        parser = FrameParser()
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), 3)
                if not chunk:
                    raise ConnectionError("ESP32 disconnected.")
                for frame in parser.feed(chunk):
                    if frame == "{Heartbeat}":
                        async with self.write_lock:
                            if self.writer is None:
                                return
                            self.writer.write(b"{Heartbeat}")
                            await asyncio.wait_for(self.writer.drain(), .25)
                        self.heartbeats += 1
                    else:
                        for key, future in list(self.waiters.items()):
                            if not future.done() and (frame == key or (key.endswith("_") and frame.startswith(key))):
                                future.set_result(frame)
                                break
        except asyncio.CancelledError:
            raise
        except Exception:
            self.fail("ESP32 reply/heartbeat connection was lost. Use physical power if needed.")

    async def _stop(self):
        self.last_stop_acknowledged = None
        try:
            await self.request({"N": 100, "H": "stop"}, "{ok}", 5)
            self.last_stop_acknowledged = True
            return True
        except Exception:
            self.last_stop_acknowledged = False
            self.fail("STOP was not acknowledged. Physical stopping is unverified.")
            return False

    async def stop(self):
        if self.writer is None:
            self.last_stop_acknowledged = False
            return False
        # Coalesce visitor STOP, pulse cleanup, and pilot-loss requests. Never
        # queue several five-second generic {ok} waiters behind one another.
        if self.stop_task is None or self.stop_task.done():
            self.stop_task = asyncio.create_task(self._stop())
        return await asyncio.shield(self.stop_task)

    async def pulse(self, direction, speed, duration_ms):
        validate_pulse(direction, speed, duration_ms)
        if not await self.stop():
            raise ConnectionError("Preflight STOP was not acknowledged; no movement sent.")
        request_id = "b" + uuid4().hex[:8]
        try:
            try:
                await self.request(
                    {"N": 2, "D1": DIRECTIONS[direction], "D2": speed, "T": duration_ms, "H": request_id},
                    "{" + request_id + "_ok}", duration_ms / 1000 + .1,
                )
            except TimeoutError:
                pass  # Still request STOP immediately, never retry N2.
        finally:
            if not await self.stop():
                raise ConnectionError("Post-step STOP was not acknowledged.")


class BenchControl:
    def __init__(self, settings, camera, bridge=None):
        self.settings, self.camera = settings, camera
        self.bridge = bridge or FactoryBridge(settings.rover_host, settings.rover_port)
        self.lock = asyncio.Lock()
        self.pilot = None
        self.lease_until = 0
        self.armed = False
        self.arm_token = None
        self.stopped = True
        self.stop_pending = False
        self.stop_generation = 0
        self.operation = self.watchdog = None
        self.direction = None
        self.events = deque(maxlen=100)
        self.subscribers = set()
        self.stop_jobs = set()
        self.log("Factory rover bench demo. Raise all wheels before enabling steps.")

    def log(self, message, level="info"):
        self.events.appendleft({"id": uuid4().hex, "at": utc_now(), "message": message, "level": level})

    def snapshot(self):
        return {
            "mode": "hardware", "phase": "stopped" if self.stopped else "idle", "target": "red",
            "mission_id": None, "started_at": None, "finished_at": None, "review": None,
            "collection_error": None, "capabilities": {key: False for key in ("mission", "manual", "collection", "return")},
            "rover": "connected" if self.bridge.connected else "not_connected", "camera": self.camera.status(),
            "pilot_id": self.pilot, "led_color": None, "motion": {"linear": 0, "angular": 0},
            "distance_cm": self.bridge.distance_cm, "last_stop_acknowledged": self.bridge.last_stop_acknowledged,
            "events": list(self.events),
            "bench": {"enabled": True, "status": self.bridge.status, "error": self.bridge.error,
                      "armed": self.armed, "arm_token": self.arm_token, "busy": self.operation is not None,
                      "direction": self.direction, "stop_pending": self.stop_pending,
                      "heartbeats": self.bridge.heartbeats, "max_duration_ms": 1000, "max_speed": 100},
        }

    def publish(self):
        message = {"type": "state", "state": self.snapshot()}
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait({"type": "state", "state": self.snapshot()})
        return queue

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)

    def latch(self, reason, *, latched=True):
        if latched:
            self.armed, self.arm_token, self.stopped = False, None, True
        if self.operation and self.operation is not asyncio.current_task():
            self.operation.cancel()
        self.direction = None
        self.stop_pending = True
        self.stop_generation += 1
        generation = self.stop_generation
        self.log(reason, "warning" if latched else "info")

        async def stop_request():
            try:
                await self.bridge.stop()
            finally:
                if generation == self.stop_generation:
                    self.stop_pending = False
                self.publish()

        task = asyncio.create_task(stop_request())
        self.stop_jobs.add(task)
        task.add_done_callback(self.stop_jobs.discard)
        self.publish()

    async def stop(self, reason="STOP requested. Re-enable steps explicitly to continue."):
        async with self.lock:
            self.latch(reason)
            return self.snapshot()  # Publish/latch before waiting on hardware.

    async def disconnect(self, session_id):
        async with self.lock:
            if self.pilot == session_id:
                self.pilot = None
                self.latch("Pilot disconnected. Steps disabled and STOP requested.")

    def launch(self, operation):
        async def run():
            try:
                await operation()
                self.log("Command bridge ready; STOP and a unique sensor reply received." if not self.direction
                         else "Step finished. STOP acknowledged in software; inspect wheels.", "success")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.latch("Bridge action failed. STOP requested; inspect wheels.")

        def finished(task):
            if self.operation is task:
                self.operation = None
                self.direction = None
                if self.armed:
                    self.arm_token = uuid4().hex  # Reject delayed/duplicate steps from the previous state.
                self.publish()
        self.operation = asyncio.create_task(run())
        self.operation.add_done_callback(finished)

    async def command(self, session_id, kind, payload):
        if kind == "ping":
            return
        async with self.lock:
            if self.pilot and time.monotonic() > self.lease_until:
                self.pilot = None
                self.latch("Pilot heartbeat expired. STOP requested.")
            if kind == "stop":
                self.latch("STOP requested by a visitor.")
            elif kind == "claim":
                if self.pilot and self.pilot != session_id:
                    raise CommandError("Another pilot has the controls.")
                if self.pilot != session_id:
                    self.log("Pilot controls claimed. Movement remains disabled until explicitly enabled.")
                self.pilot, self.lease_until = session_id, time.monotonic() + 3
            else:
                if self.pilot != session_id:
                    raise CommandError("Claim pilot controls first.")
                if kind == "heartbeat":
                    self.lease_until = time.monotonic() + 3
                    return
                if kind == "release":
                    self.pilot = None
                    self.latch("Pilot released controls. STOP requested.")
                elif kind == "halt":
                    self.latch("Input released. STOP requested.", latched=False)
                elif kind == "bridge_connect":
                    if self.operation or self.stop_pending or self.armed:
                        raise CommandError("Stop and wait for the current action before reconnecting.")
                    self.launch(self.bridge.connect)
                elif kind == "bench_arm":
                    if payload.get("wheels_raised") is not True:
                        raise CommandError("Securely support the chassis with all four wheels clear first.")
                    if not self.bridge.connected or self.operation or self.stop_pending:
                        raise CommandError("Connect the command bridge and wait for STOP acknowledgement first.")
                    self.armed, self.stopped, self.arm_token = True, False, uuid4().hex
                    self.log("Pilot enabled single steps with wheels raised.")
                elif kind == "bench_pulse":
                    if not self.armed or not self.bridge.connected or self.stopped:
                        raise CommandError("Connect and enable wheels-raised steps first.")
                    if self.operation or self.stop_pending:
                        raise CommandError("A step or STOP is in progress. Commands are not queued.")
                    if payload.get("arm_token") != self.arm_token:
                        raise CommandError("This step request is outdated. Use the current controls.")
                    try:
                        validate_pulse(payload.get("direction"), payload.get("speed"), payload.get("duration_ms"))
                    except (ValueError, TypeError):
                        raise CommandError("Choose a direction, PWM 1–100, and a duration of 50–1000 ms.")
                    self.direction = payload["direction"]
                    self.log(f"One {self.direction} step requested: PWM {payload['speed']}/255, {payload['duration_ms']} ms.")
                    self.launch(lambda: self.bridge.pulse(payload["direction"], payload["speed"], payload["duration_ms"]))
                else:
                    raise CommandError("Unknown bench command. Continuous drive and missions are unavailable.")
            self.publish()

    async def start(self):
        async def watch():
            while True:
                await asyncio.sleep(.1)
                async with self.lock:
                    if self.pilot and time.monotonic() > self.lease_until:
                        self.pilot = None
                        self.latch("Pilot heartbeat expired. STOP requested.")
                    elif self.armed and not self.bridge.connected:
                        self.latch("Command connection lost. Reconnect explicitly; steps disabled.")
                    self.publish()
        self.watchdog = asyncio.create_task(watch())

    async def close(self):
        self.armed, self.arm_token = False, None
        for task in (self.watchdog, self.operation):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self.watchdog, self.operation) if task), return_exceptions=True)
        await self.bridge.stop()
        await self.bridge.close()
        await asyncio.gather(*self.stop_jobs, return_exceptions=True)
