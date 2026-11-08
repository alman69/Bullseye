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
    labels = ("letting go",) * 2 if releasing else ("squeezing",) * 3
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
        self.control_write_lock = asyncio.Lock()
        self.heartbeats_sent = self.heartbeats_acknowledged = 0
        self.last_heartbeat_at = None
        self.max_heartbeat_gap_ms = 0
        self.last_device_boot_id = None
        self.device_boot_changes = 0

    def snapshot(self):
        return {**super().snapshot(), "transport": "ble", "display_updates": self.display_updates,
                "ble_connected": self.client is not None and self.client.is_connected, "servo_ready": self.servo_ready,
                "heartbeats_sent": self.heartbeats_sent, "heartbeats_acknowledged": self.heartbeats_acknowledged,
                "heartbeat_age_ms": None if self.last_heartbeat_at is None else max(0, int((time.monotonic() - self.last_heartbeat_at) * 1000)),
                "max_heartbeat_gap_ms": self.max_heartbeat_gap_ms,
                "device_boot_id": self.last_device_boot_id, "device_boot_changes": self.device_boot_changes}

    async def connect(self):
        await self.close()
        self.status, self.error, self.failure = "connecting", None, None
        self.display_updates = 0
        self.servo_ready = None
        self.heartbeats_sent = self.heartbeats_acknowledged = 0
        self.last_heartbeat_at = None
        self.max_heartbeat_gap_ms = 0
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
            boot = status["boot_id"]
            if self.last_device_boot_id is not None and boot != self.last_device_boot_id:
                self.device_boot_changes += 1
            self.last_device_boot_id = boot
            self._apply(status, allow_servo_fault=True)
            self.servo_ready = status["servo_ready"]
            if not self.servo_ready:
                self.error = PWM_FAULT
            elif self.error == PWM_FAULT:
                self.error = None
            self.last_status_at = time.monotonic()
            if result == 1 and reply == 0:
                self.heartbeats_acknowledged += 1
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
                    await self._write_control(client, encode_command("heartbeat", self.boot_id, 0))
                    sent_at = time.monotonic()
                    if self.last_heartbeat_at is not None:
                        self.max_heartbeat_gap_ms = max(self.max_heartbeat_gap_ms, int((sent_at - self.last_heartbeat_at) * 1000))
                    self.last_heartbeat_at = sent_at
                    self.heartbeats_sent += 1
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

    async def _write_control(self, client, data):
        """Serialize same-handle ATT writes, including an already-issued canceled write.

        CoreBluetooth/Bleak has one write-with-response future per characteristic.
        A heartbeat and an action must not overwrite that future. If STOP cancels
        its caller, the ATT transaction drains under the lock before STOP writes;
        a bounded timeout instead poisons/closes the session, never overlaps it.
        Application ACK waiting happens outside this lock.
        """
        token = self.session_token
        async with self.control_write_lock:
            if client is None or token is not self.session_token or client is not self.client or not client.is_connected or self.failure:
                raise ConnectionError(self.failure or "Companion BLE session changed before write")
            transaction = asyncio.create_task(asyncio.wait_for(
                client.write_gatt_char(COMMAND_UUID, data, response=True), COMMAND_TIMEOUT_S))
            canceled = False
            while not transaction.done():
                try:
                    await asyncio.shield(transaction)
                except asyncio.CancelledError:
                    # A second cancel must not release the lock over an ATT
                    # write that is still using CoreBluetooth's handle future.
                    canceled = True
                except Exception:
                    break
            error = None
            if transaction.cancelled():
                error = ConnectionError("BLE control write was canceled by the transport")
            else:
                try:
                    transaction.result()
                except Exception as exc:
                    error = exc
            if error is not None:
                self.failure = f"BLE control write failed: {str(error) or type(error).__name__}"
                self.status = "error"
            if canceled:
                raise asyncio.CancelledError
            if error is not None:
                raise ConnectionError(self.failure) from error

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
            await self._write_control(self.client, data)
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
