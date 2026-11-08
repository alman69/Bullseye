"""Manual factory-firmware control: timed button steps, leased keyboard drive, telemetry."""
import asyncio
from collections import deque
import time
from uuid import uuid4

from .autopilot import Autopilot, AutopilotError
from .elegoo_bench import DIRECTIONS, validate_pulse
from .elegoo_probe import FrameParser
from .mission import CommandError, utc_now
from .scooper import ScooperLink
from .ble_companion import BleCompanionLink

# Bench command kind -> scooper firmware command.
SCOOPER_ACTIONS = {"scooper_squeeze": "squeeze", "scooper_release": "release", "scooper_home": "home"}
SCOOPER_LABELS = {"squeeze": "Squeeze", "release": "Release", "home": "Straight down"}


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
        # Shield RGB LEDs: red by default, green after reaching the sample. N100 STOP clears the
        # LEDs, so the color is re-sent after every STOP we send (only ever while stopped: an LED
        # command during a timed N2 move would cancel the move's auto-stop).
        self.led = (255, 0, 0)
        self.led_dirty = True

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
            self.led_dirty = True
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

    async def _send_led(self):
        """N8 all LEDs to self.led. Call only right after an acknowledged STOP. Cosmetic: never fails."""
        request_id = "l" + uuid4().hex[:8]
        r, g, b = self.led
        try:
            await self.request({"N": 8, "D1": 0, "D2": r, "D3": g, "D4": b, "H": request_id}, "{" + request_id + "_ok}", .5)
            self.led_dirty = False
        except (TimeoutError, OSError, RuntimeError):
            pass

    async def set_led(self, rgb=None):
        """Set (or re-send) the shield LED color: STOP first so it never lands during a move."""
        async with self.io_lock:
            if rgb is not None:
                self.led = tuple(int(c) for c in rgb)
            if self.writer is None or not await self.stop():
                return False
            await self._send_led()
            return not self.led_dirty

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
        await self._send_led()                      # stopped: safe to set the LEDs before moving
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
            await self._send_led()


class BenchControl:
    def __init__(self, settings, camera, bridge=None, scooper=None):
        self.settings, self.camera = settings, camera
        self.bridge = bridge or FactoryBridge(settings.rover_host, settings.rover_port)
        # One BLE session carries both cached display readings and scooper actions.
        self.scooper = scooper or (ScooperLink(settings.scooper_url) if settings.scooper_transport == "http" else
                                   BleCompanionLink(lambda: self.snapshot(), device_id=settings.ble_display_device_id))
        self.scooper.on_change = self.publish
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
        self.vision = None   # VisionService, attached by create_app when enabled
        self.auto = {"state": "idle", "message": "Choose a sample color, then start the search.", "target": None, "run_id": None}
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
            "mode": "hardware", "phase": "stopped" if self.stopped else "idle", "target": self.auto.get("target") or "red",
            "mission_id": None, "started_at": None, "finished_at": None, "review": None,
            "collection_error": None, "capabilities": {key: False for key in ("mission", "manual", "collection", "return")},
            "rover": "connected" if self.bridge.connected else "not_connected", "camera": self.camera.status(),
            "pilot_id": self.pilot, "led_color": "green" if getattr(self.bridge, "led", None) == (0, 255, 0) else "red", "motion": {"linear": 0, "angular": 0},
            "distance_cm": self.bridge.distance_cm, "last_stop_acknowledged": self.bridge.last_stop_acknowledged,
            "events": list(self.events),
            "vision": self.vision.summary() if self.vision else None,
            "bench": {"enabled": True, "status": self.bridge.status, "error": self.bridge.error,
                      "armed": self.armed, "arm_token": self.arm_token, "busy": self.operation is not None,
                      "direction": self.direction, "stop_pending": self.stop_pending,
                      "heartbeats": self.bridge.heartbeats, "max_duration_ms": 1000, "max_speed": 100,
                      "drive_mode": self.drive_mode, "hold_id": self.hold_id,
                      "distance_age_ms": distance_age, "distance_status": distance_status,
                      "scooper": self.scooper.snapshot(), "auto": dict(self.auto)},
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
        if self.auto["state"] in {"running", "scanning", "relocating", "approaching", "arrived"}:
            self._report("stopped", "Search stopped. Enable controls to start a new search.")
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
        if latched and (self.scooper.ready or self.scooper.client is not None):
            # A real STOP also freezes the sticks where they are (they keep holding).
            stop = self.scooper.request_stop()  # Fence queued motion before releasing the bench lock.
            async def scooper_stop():
                try:
                    if await stop is not True:
                        self.log("Scooper refused STOP; inspect its reported state.", "warning")
                except Exception:
                    self.log("Scooper STOP was not acknowledged; check the scooper.", "warning")
            job = asyncio.create_task(scooper_stop())
            self.stop_jobs.add(job)
            job.add_done_callback(self.stop_jobs.discard)
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
                result = await operation()
                if result is not False:
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
                    if self.auto["state"] == "awaiting_pickup":
                        raise CommandError("Pick up the material and confirm pickup before enabling movement.")
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
                elif kind == "scooper_connect":
                    self.log(f"Companion: connecting to {self.scooper.url}…")
                    self._scooper_job(self._scooper_connect())
                elif kind in SCOOPER_ACTIONS:
                    # Independent of the rover controls: the pilot seat is enough.
                    if not self.scooper.ready:
                        raise CommandError("Connect the scooper first.")
                    name = SCOOPER_ACTIONS[kind]
                    self._scooper_job(self._scooper_command(name))
                    self.log(f"Scooper: {SCOOPER_LABELS[name]} requested.")
                elif kind in {"auto_pickup_confirm", "auto_home_confirm"}:
                    expected = "awaiting_pickup" if kind == "auto_pickup_confirm" else "manual_return"
                    if self.auto["state"] != expected or not self.auto.get("run_id") or payload.get("run_id") != self.auto["run_id"]:
                        raise CommandError("This mission confirmation is no longer current.")
                    if self.operation or self.stop_pending:
                        raise CommandError("Wait for the rover to stop before confirming.")
                    if kind == "auto_pickup_confirm":
                        self._report("manual_return", "Pickup confirmed. Carry the material to home base, then confirm delivery. Enable controls only if you need to drive the rover manually.")
                        self.log("Pilot confirmed manual pickup. Waiting for delivery to home base.", "success")
                    else:
                        self.armed, self.arm_token, self.stopped = False, None, True
                        self.launch(self._confirm_home, "Pilot confirmed material delivered to home base.")
                elif kind == "auto_return":
                    raise CommandError("Bring the material to home base manually, then confirm delivery.")
                elif kind in {"auto_goto", "auto_mission"}:
                    if self.auto["state"] in {"awaiting_pickup", "manual_return"}:
                        raise CommandError("Finish manual pickup and delivery before starting another search.")
                    if not self.armed or not self.bridge.connected or self.stopped:
                        raise CommandError("Connect and enable controls first.")
                    if self.operation or self.stop_pending:
                        raise CommandError("A step or STOP is in progress. Commands are not queued.")
                    if payload.get("arm_token") != self.arm_token:
                        raise CommandError("This request is outdated. Use the current controls.")
                    if not self.vision or not self.vision.available:
                        raise CommandError("Vision is not running, so the autopilot can't see.")
                    avoid = True  # physical autonomous moves always use obstacle avoidance
                    color = payload.get("color")
                    if color not in ("red", "green"):
                        raise CommandError("Choose a red or green sample. Blue is an obstacle.")
                    label = f"Search for {color}: scan 360°, relocate using ultrasonic clearance, approach to 10 cm, then wait for manual pickup"
                    job = lambda pilot: pilot.mission(color, avoid=avoid)
                    self.auto = {"state": "running", "message": label, "target": color, "run_id": uuid4().hex}
                    self.direction, self.drive_mode = "auto", "auto"
                    self.log(label + ".")
                    self.launch(lambda: self._autopilot(job), "Autopilot finished. STOP acknowledged in software.")
                else:
                    raise CommandError("Unknown rover command.")
            self.publish()

    def _report(self, state, message):
        if state == "awaiting_pickup":
            # Arrival is already STOP-acknowledged. Require a human handoff before any rearm.
            self.armed, self.arm_token, self.stopped = False, None, True
        self.auto = {**self.auto, "state": state, "message": message,
                     "expression": "happy" if state in {"sample_reached", "awaiting_pickup", "manual_return"} else "auto"}
        self.publish()

    async def _confirm_home(self):
        if await self.bridge.stop() is not True:
            self.latch("Delivery confirmation needs an acknowledged STOP. Reconnect the bridge and confirm again.")
            return False
        setter = getattr(self.bridge, "set_led", None)
        if setter is not None and await setter((255, 0, 0)) is not True:
            self.latch("Delivery indicator was not acknowledged. Reconnect the bridge and confirm again.")
            return False
        self._report("home_arrived", "Material delivered to home base. Mission complete; rover controls disabled.")

    async def _autopilot(self, job):
        pilot = Autopilot(self.bridge, self.vision, self._report)
        self._report("running", "Autopilot started.")
        try:
            await job(pilot)
        except asyncio.CancelledError:
            self._report("stopped", "Autopilot stopped.")
            raise
        except AutopilotError as error:          # target lost etc.: stop, keep controls enabled
            self._report("failed", str(error))
            self.log(str(error), "warning")
            if await self.bridge.stop() is not True:
                self.latch("Autopilot STOP was not acknowledged. Controls disabled.")
            return False
        except Exception:
            self._report("failed", "Autopilot bridge action failed. STOP requested; motor state is unverified.")
            raise
        if self.auto["state"] == "running":
            self._report("done", "Autopilot finished.")

    def validate_drive(self, payload, *, timed=False):
        try:
            validate_pulse(payload.get("direction"), payload.get("speed"), payload.get("duration_ms") if timed else 300)
        except (ValueError, TypeError):
            raise CommandError("Choose a direction, PWM 1–100, and a button duration of 50–1000 ms.")

    def _scooper_job(self, job):
        try:
            self.scooper.launch(job)
        except RuntimeError:
            job.close()
            raise CommandError("The scooper is still answering the last command.")

    async def _scooper_connect(self):
        await self.scooper.connect()
        if self.scooper.ready:
            self.log(f"Scooper connected at {self.scooper.url}; sticks report {self.scooper.state}.", "success")
        else:
            self.log(f"Scooper not connected: {self.scooper.error}", "warning")
        self.publish()

    async def _scooper_command(self, name):
        try:
            result = await self.scooper.command(name)
        except ConnectionError as exc:
            self.log(f"Scooper link failed: {exc}", "warning")
        else:
            if result is True:
                self.log(f"Scooper: {SCOOPER_LABELS[name]} started.", "success")
            else:
                self.log(f"Scooper refused {SCOOPER_LABELS[name]}: {result}", "warning")
        self.publish()

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
                    if getattr(self.bridge, "led_dirty", False):
                        await self.bridge.set_led()     # keep the shield LEDs lit after STOPs
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
        if self.scooper.ready:
            await asyncio.gather(self.scooper.request_stop(), return_exceptions=True)
        await self.bridge.close()
        await asyncio.gather(*self.stop_jobs, return_exceptions=True)
        if self.scooper.task:
            self.scooper.task.cancel()
            await asyncio.gather(self.scooper.task, return_exceptions=True)
        await self.scooper.close()
