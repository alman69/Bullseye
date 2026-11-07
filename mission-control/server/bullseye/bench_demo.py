"""Manual factory-firmware control: timed button steps, leased keyboard drive, telemetry."""
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
        self.distance_at = 0.0
        self.distance_error = None
        self.heartbeats = 0
        self.last_stop_acknowledged = None
        self.writer = self.reader_task = self.stop_task = None
        self.waiters = {}
        self.write_lock = asyncio.Lock()
        self.io_lock = asyncio.Lock()

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
            if not await self.refresh_distance(timeout=5):
                raise ConnectionError("UNO sensor reply did not match the expected protocol.")
            self.status = "ready"
        except BaseException as error:
            await self.close()
            self.error = "Connection cancelled; controls remain disabled." if isinstance(error, asyncio.CancelledError) else (
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

    async def refresh_distance(self, timeout=2):
        # N21 blocks inside the UNO. Serialize it with motion transactions; held
        # driving explicitly stops before calling this between drive updates.
        async with self.io_lock:
            request_id = "p" + uuid4().hex[:8]
            prefix = "{" + request_id + "_"
            try:
                frame = await self.request({"N": 21, "D1": 2, "H": request_id}, prefix, timeout)
                value = frame[len(prefix):-1]
                if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= 150:
                    raise ValueError("Invalid ultrasonic response")
                self.distance_cm = int(value) or None  # Zero is unknown, never clear space.
                self.distance_at = time.monotonic()
                self.distance_error = None
                return True
            except (TimeoutError, OSError, ValueError):
                self.distance_error = "Distance update unavailable"
                return False

    async def drive_frame(self, direction, speed, *, valid=lambda: True):
        validate_pulse(direction, speed, 300)
        async with self.io_lock:
            if not valid():
                return False
            # N2 has no immediate ACK. A cached N22 read fences each update:
            # wait for UNO processing before transmitting another movement.
            # N22 reads cached IR data without changing the current motor mode.
            try:
                request_id = "h" + uuid4().hex[:8]
                await self.send({"N": 2, "D1": DIRECTIONS[direction], "D2": speed, "T": 300, "H": request_id})
                fence = "f" + uuid4().hex[:8]
                prefix = "{" + fence + "_"
                frame = await self.request({"N": 22, "D1": 0, "H": fence}, prefix, .35)
                value = frame[len(prefix):-1]
                if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= 1023:
                    raise ConnectionError("UNO drive update was not acknowledged by the cached sensor reply.")
                return True
            except BaseException:
                await self.stop()
                raise

    async def pulse(self, direction, speed, duration_ms):
        validate_pulse(direction, speed, duration_ms)
        async with self.io_lock:
            await self._pulse(direction, speed, duration_ms)

    async def _pulse(self, direction, speed, duration_ms):
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
        self.telemetry_task = None
        self.drive_mode = None
        self.hold_id = None
        self.hold_until = 0
        self.hold_speed = 40
        self.direction = None
        self.events = deque(maxlen=100)
        self.subscribers = set()
        self.stop_jobs = set()
        self.log("Rover control online. Connect the bridge, then enable controls.")

    def log(self, message, level="info"):
        self.events.appendleft({"id": uuid4().hex, "at": utc_now(), "message": message, "level": level})

    def snapshot(self):
        distance_at = getattr(self.bridge, "distance_at", 0)
        distance_age = round((time.monotonic() - distance_at) * 1000) if distance_at else None
        distance_status = "unavailable" if distance_age is None else (
            "stale" if not self.bridge.connected or distance_age > 1500 or getattr(self.bridge, "distance_error", None)
            else "invalid" if self.bridge.distance_cm is None else "live"
        )
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
                      "heartbeats": self.bridge.heartbeats, "max_duration_ms": 1000, "max_speed": 100,
                      "drive_mode": self.drive_mode, "hold_id": self.hold_id,
                      "distance_age_ms": distance_age, "distance_status": distance_status},
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
        self.hold_id, self.hold_until = None, 0
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

    async def stop(self, reason="STOP requested. Enable controls to continue."):
        async with self.lock:
            self.latch(reason)
            return self.snapshot()  # Publish/latch before waiting on hardware.

    async def disconnect(self, session_id):
        async with self.lock:
            if self.pilot == session_id:
                self.pilot = None
                self.latch("Pilot disconnected. Controls disabled and STOP requested.")

    def launch(self, operation, success="Command bridge ready; STOP and a unique sensor reply received."):
        async def run():
            try:
                await operation()
                self.log(success, "success")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.latch("Bridge action failed. STOP requested; motor state is unverified.")

        def finished(task):
            if self.operation is task:
                self.operation = None
                self.direction = None
                self.drive_mode = None
                self.hold_id, self.hold_until = None, 0
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
                elif kind in {"hold_update", "hold_end"}:
                    # Late renewals/end events never start motion or stop a new
                    # gesture. Each hold is scoped to its enable token and ID.
                    if not self.hold_id or payload.get("hold_id") != self.hold_id or payload.get("arm_token") != self.arm_token:
                        return
                    if kind == "hold_end":
                        self.latch("Keyboard released. STOP requested.", latched=False)
                    else:
                        self.validate_drive(payload)
                        self.direction, self.hold_speed = payload["direction"], payload["speed"]
                        self.hold_until = time.monotonic() + .35
                elif kind == "bridge_connect":
                    if self.operation or self.stop_pending or self.armed:
                        raise CommandError("Stop and wait for the current action before reconnecting.")
                    self.launch(self.bridge.connect)
                elif kind == "bench_arm":
                    if not self.bridge.connected or self.operation or self.stop_pending:
                        raise CommandError("Connect the command bridge and wait for STOP acknowledgement first.")
                    self.armed, self.stopped, self.arm_token = True, False, uuid4().hex
                    self.log("Pilot enabled manual controls.")
                elif kind in {"bench_pulse", "hold_start"}:
                    if not self.armed or not self.bridge.connected or self.stopped:
                        raise CommandError("Connect and enable controls first.")
                    if self.operation or self.stop_pending:
                        raise CommandError("A step or STOP is in progress. Commands are not queued.")
                    if payload.get("arm_token") != self.arm_token:
                        raise CommandError("This step request is outdated. Use the current controls.")
                    self.validate_drive(payload, timed=kind == "bench_pulse")
                    if kind == "hold_start":
                        hold_id = payload.get("hold_id")
                        if not isinstance(hold_id, str) or not 1 <= len(hold_id) <= 80:
                            raise CommandError("A unique keyboard hold ID is required.")
                        self.direction = payload["direction"]
                        self.drive_mode, self.hold_id = "hold", hold_id
                        self.hold_speed, self.hold_until = payload["speed"], time.monotonic() + .35
                        self.log(f"Keyboard drive started: {self.direction}, PWM {self.hold_speed}/255.")
                        self.launch(lambda: self._hold(hold_id), "Keyboard drive finished. STOP acknowledged in software.")
                    else:
                        self.direction = payload["direction"]
                        self.drive_mode = "step"
                        self.log(f"One {self.direction} step requested: PWM {payload['speed']}/255, {payload['duration_ms']} ms.")
                        self.launch(lambda: self.bridge.pulse(payload["direction"], payload["speed"], payload["duration_ms"]), "Step finished. STOP acknowledged in software.")
                else:
                    raise CommandError("Unknown rover command. Autonomous missions are unavailable.")
            self.publish()

    def validate_drive(self, payload, *, timed=False):
        try:
            validate_pulse(payload.get("direction"), payload.get("speed"), payload.get("duration_ms") if timed else 300)
        except (ValueError, TypeError):
            raise CommandError("Choose a direction, PWM 1–100, and a button duration of 50–1000 ms.")

    async def _hold(self, hold_id):
        def current():
            return self.armed and self.hold_id == hold_id and time.monotonic() < self.hold_until
        try:
            if not await self.bridge.stop():
                raise ConnectionError("Preflight STOP was not acknowledged.")
            while current():
                # Sample only after a STOP ACK: pulseIn can block the UNO. This
                # introduces a brief pause rather than delaying an active stop.
                if time.monotonic() - getattr(self.bridge, "distance_at", 0) >= 1:
                    if not await self.bridge.stop():
                        raise ConnectionError("Telemetry STOP was not acknowledged.")
                    await self.bridge.refresh_distance()
                if not current():
                    break
                if not await self.bridge.drive_frame(self.direction, self.hold_speed, valid=current):
                    break
                await asyncio.sleep(.08)
        finally:
            if self.hold_id == hold_id and self.armed and time.monotonic() >= self.hold_until:
                self.latch("Keyboard input expired. STOP requested; controls disabled.")
            if not await self.bridge.stop():
                raise ConnectionError("Keyboard STOP was not acknowledged.")

    async def start(self):
        async def watch():
            next_publish = 0
            while True:
                await asyncio.sleep(.05)
                async with self.lock:
                    if self.pilot and time.monotonic() > self.lease_until:
                        self.pilot = None
                        self.latch("Pilot heartbeat expired. STOP requested.")
                    elif self.armed and not self.bridge.connected:
                        self.latch("Command connection lost. Reconnect explicitly; controls disabled.")
                    elif self.hold_id and time.monotonic() > self.hold_until:
                        self.latch("Keyboard input expired. STOP requested; controls disabled.")
                    if time.monotonic() >= next_publish:
                        self.publish()
                        next_publish = time.monotonic() + .1
        self.watchdog = asyncio.create_task(watch())
        async def telemetry():
            while True:
                if self.bridge.connected and self.operation is None and not self.stop_pending:
                    await self.bridge.refresh_distance()
                    self.publish()
                await asyncio.sleep(.5)
        self.telemetry_task = asyncio.create_task(telemetry())

    async def close(self):
        self.armed, self.arm_token = False, None
        for task in (self.watchdog, self.operation, self.telemetry_task):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self.watchdog, self.operation, self.telemetry_task) if task), return_exceptions=True)
        await self.bridge.stop()
        await self.bridge.close()
        await asyncio.gather(*self.stop_jobs, return_exceptions=True)
