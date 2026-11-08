"""One operator-initiated BLE session for scooper control and cached display data.

No Bluetooth imports or discovery until Connect. No automatic motion reconnect,
no extra rover requests, and no separate send-display process is needed.
"""
import asyncio
import struct
import time

from .ble_display import (ACK_UUID, Acknowledgements, BleakRuntime, ProtocolError,
                          from_state, select_device, unavailable)
from .scooper import ScooperLink

COMMAND_UUID = "3f7c1003-6f54-4a9c-8b2d-56e39a18c201"
STATUS_UUID = "3f7c1004-6f54-4a9c-8b2d-56e39a18c201"
COMMANDS = {"heartbeat": 0, "squeeze": 1, "release": 2, "home": 3, "stop": 4, "relax": 5}
STATES = ("relaxed", "holding", "moving", "sequence")
RESULTS = {2: "Invalid BLE command", 3: "Outdated command; reconnect", 4: "Scooper refused the action; stop the current sequence first", 5: "Scooper command mailbox overflow"}
HEARTBEAT_S = .5
STATUS_TIMEOUT_S = 2.0
COMMAND_TIMEOUT_S = 1.0
PWM_FAULT = "BLE display connected; scooper PWM unavailable. Motion is disabled. Check the S3 startup log for the PWM attach/write error."


def encode_command(name, boot, sequence):
    if name not in COMMANDS or type(boot) is not int or not 1 <= boot <= 0xFFFFFFFF:
        raise ProtocolError("Invalid companion command or boot ID")
    if type(sequence) is not int or not 0 <= sequence <= 0xFFFFFFFF or (sequence == 0) != (name == "heartbeat"):
        raise ProtocolError("Invalid companion sequence")
    return struct.pack("<2sBBII", b"BC", 1, COMMANDS[name], boot, sequence)


def decode_status(data):
    if len(data) != 20:
        raise ProtocolError("Missing combined BLE firmware: expected a 20-byte companion status")
    magic, version, result, boot, sequence, reply, flags, left, right, step = struct.unpack("<2sBBIIIBBBB", data)
    if magic != b"BC" or version != 1 or result > 5 or not boot or flags & ~31 or left > 180 or right > 180:
        raise ProtocolError("Invalid companion status")
    state = STATES[(flags >> 2) & 3]
    releasing = bool(flags & 16)
    if state == "sequence" and not 1 <= step <= 3 or state != "sequence" and step != 0:
        raise ProtocolError("Invalid companion sequence step")
    labels = ("out", "down") if releasing else ("down", "out", "in")
    if state == "sequence" and step > len(labels):
        raise ProtocolError("Invalid companion step index")
    return result, reply, {
        "firmware": "bullseye-companion-ble", "api": 2, "boot_id": boot, "command_seq": sequence,
        "servo_ready": bool(flags & 1), "watchdog_stopped": bool(flags & 2), "state": state,
        "sequence": ("release" if releasing else "squeeze") if state == "sequence" else None,
        "step": labels[step - 1] if state == "sequence" else None, "angles": {"left": left, "right": right},
    }


class BleCompanionLink(ScooperLink):
    def __init__(self, state, *, device_id=None, runtime_factory=BleakRuntime):
        super().__init__("BLE · Bullseye-Display")
        self.state_source, self.device_id, self.runtime_factory = state, device_id, runtime_factory
        self.worker = None
        self.pending = None
        self.session_token = None
        self.failure = None
        self.last_status_at = 0
        self.display_updates = 0
        self.servo_ready = None

    def snapshot(self):
        return {**super().snapshot(), "transport": "ble", "display_updates": self.display_updates,
                "ble_connected": self.client is not None and self.client.is_connected, "servo_ready": self.servo_ready}

    async def connect(self):
        await self.close()
        self.status, self.error, self.failure = "connecting", None, None
        self.display_updates = 0
        self.servo_ready = None
        self.on_change()
        ready = asyncio.get_running_loop().create_future()
        self.worker = asyncio.create_task(self._run(ready))
        try:
            await asyncio.wait_for(asyncio.shield(ready), 30)
        except asyncio.CancelledError:
            # request_stop() may be awaiting this connect job: never await that
            # stop task from inside the job it just canceled.
            if self.worker:
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)
                self.worker = None
            self.status = "disconnected"
            raise
        except Exception as exc:
            await self.close()
            self.status, self.error = "error", str(exc) or type(exc).__name__
        finally:
            # Retrieve a late discovery failure even after cancellation/timeout.
            if ready.done() and not ready.cancelled():
                ready.exception()
            elif not ready.done():
                ready.cancel()
            self.on_change()

    def _receive(self, data, token):
        if token is not self.session_token or self.client is None:
            return
        try:
            result, reply, status = decode_status(data)
            self._apply(status, allow_servo_fault=True)
            self.servo_ready = status["servo_ready"]
            if not self.servo_ready:
                self.error = PWM_FAULT
            elif self.error == PWM_FAULT:
                self.error = None
            self.last_status_at = time.monotonic()
            if self.pending and not self.pending[1].done() and (reply == self.pending[0] or reply == 0 and result >= 2):
                self.pending[1].set_result(True if result == 1 else RESULTS.get(result, "Missing command acknowledgement"))
            self.on_change()
        except (ValueError, ConnectionError) as exc:
            self.failure = str(exc)

    async def _run(self, ready):
        telemetry = None
        runtime = None
        try:
            runtime = self.runtime_factory()
            device = select_device(await runtime.discover(), self.device_id)
            async with runtime.connect(device) as client:
                self.client = client
                token = self.session_token = object()
                loop = asyncio.get_running_loop()
                def notification(_characteristic, data):
                    loop.call_soon_threadsafe(self._receive, bytes(data), token)
                await client.start_notify(STATUS_UUID, notification)
                self._receive(bytes(await client.read_gatt_char(STATUS_UUID)), token)
                if self.failure:
                    raise ConnectionError(self.failure)
                # Every session starts by holding the last pose, never replaying motion.
                if await self.command("stop") is not True:
                    raise ConnectionError("Companion did not acknowledge the connection STOP")
                self.status = "ready"
                acks = Acknowledgements()
                await client.start_notify(ACK_UUID, acks.callback)
                telemetry = asyncio.create_task(self._telemetry(client, acks))
                self.on_change()
                ready.set_result(True)
                while client.is_connected:
                    if self.failure:
                        raise ConnectionError(self.failure)
                    if telemetry.done():
                        await telemetry
                        raise ConnectionError("Display telemetry stopped")
                    if time.monotonic() - self.last_status_at > STATUS_TIMEOUT_S:
                        raise ConnectionError("Companion BLE status timed out; reconnect")
                    await asyncio.wait_for(client.write_gatt_char(COMMAND_UUID, encode_command("heartbeat", self.boot_id, 0), response=True), COMMAND_TIMEOUT_S)
                    await asyncio.sleep(HEARTBEAT_S)
                raise ConnectionError("Companion BLE disconnected; press Reconnect")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            hint = runtime.guidance(exc) if runtime else None
            message = hint or (str(exc) or type(exc).__name__)
            if "not found" in message.lower():
                message += " Stop send-display.sh: Mission Control must own the one BLE connection."
            self.status, self.error, self.state = "error", message, None
            if not ready.done():
                ready.set_exception(ConnectionError(message))
        finally:
            self.session_token = None
            if telemetry:
                telemetry.cancel()
                await asyncio.gather(telemetry, return_exceptions=True)
            self.client = None
            if self.pending and not self.pending[1].done():
                self.pending[1].set_exception(ConnectionError(self.error or "Companion BLE session closed"))
            self.on_change()

    async def _telemetry(self, client, acks):
        sequence = 1
        while client.is_connected:
            started = time.monotonic()
            try:
                reading = from_state(self.state_source(), started)
            except (ValueError, TypeError, KeyError):
                reading = unavailable(started)
            await acks.deliver(client, reading.packet(sequence, time.monotonic()))
            self.display_updates += 1
            if sequence == 0xFFFFFFFF:
                raise ProtocolError("Telemetry sequence exhausted; reconnect")
            sequence += 1
            await asyncio.sleep(max(0, .5 - (time.monotonic() - started)))

    async def command(self, name):
        if name not in COMMANDS or name == "heartbeat":
            raise ValueError(name)
        if not self.ready and not (name == "stop" and self.client is not None):
            raise ConnectionError("Connect the companion over BLE first")
        if name in {"squeeze", "release", "home"} and self.servo_ready is not True:
            # Keep the BLE display session alive, but never send a positive move.
            self.error = PWM_FAULT
            self.on_change()
            return PWM_FAULT
        if self.pending is not None or name != "stop" and self.stop_task and not self.stop_task.done():
            raise ConnectionError("Companion command or STOP still in progress")
        self.command_sequence += 1
        sequence, generation = self.command_sequence, self.generation
        data = encode_command(name, self.boot_id, sequence)
        future = asyncio.get_running_loop().create_future()
        self.pending, self.busy = (sequence, future), name
        self.on_change()
        try:
            await asyncio.wait_for(self.client.write_gatt_char(COMMAND_UUID, data, response=True), COMMAND_TIMEOUT_S)
            result = await asyncio.wait_for(future, COMMAND_TIMEOUT_S)
            if generation != self.generation and name != "stop":
                return "canceled by STOP"
            self.error = (None if self.servo_ready else PWM_FAULT) if result is True else result
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failure = self.error = f"BLE '{name}' was not acknowledged: {str(exc) or type(exc).__name__}"
            self.status = "error"
            raise ConnectionError(self.failure) from exc
        finally:
            if self.pending and self.pending[0] == sequence:
                self.pending, self.busy = None, None
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()
            self.on_change()

    async def close(self):
        # Stop task is awaited before closing the underlying BLE session.
        if self.stop_task and self.stop_task is not asyncio.current_task():
            await asyncio.gather(self.stop_task, return_exceptions=True)
        self.stop_task = None
        if self.worker and self.worker is not asyncio.current_task():
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        self.worker = None
        self.client = None
        await super().close()
