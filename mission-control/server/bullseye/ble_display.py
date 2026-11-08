"""Separate, read-only Mac -> BLE display sender. Importing never uses Bluetooth.

Live use is operator-only. --help and all tests work without BLE permission or a
rover; Bleak/CoreBluetooth is imported only when the operator starts discovery.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import json
import struct
import sys
import time

import httpx

SERVICE_UUID = "3f7c1000-6f54-4a9c-8b2d-56e39a18c201"
RX_UUID = "3f7c1001-6f54-4a9c-8b2d-56e39a18c201"
ACK_UUID = "3f7c1002-6f54-4a9c-8b2d-56e39a18c201"
DEFAULT_STATE_URL = "http://127.0.0.1:8001/api/bench/state"
STATUSES = {"live", "stale", "invalid", "unavailable"}
EXPRESSIONS = {"auto", "happy"}
UINT32_MAX = 0xFFFFFFFF
ACK_TIMEOUT_SECONDS = 3.0
WRITE_TIMEOUT_SECONDS = 2.0


class ProtocolError(ValueError):
    pass


class DeviceSelectionError(RuntimeError):
    pass


def uint(value, *, minimum=0):
    if type(value) is not int or not minimum <= value <= UINT32_MAX:
        raise ProtocolError("Expected a bounded unsigned integer")
    return value


@dataclass(frozen=True)
class Observation:
    distance_cm: int | None
    status: str
    sample_age_ms: int | None
    rover_connected: bool
    source: str
    observed_at: float
    expression: str = "auto"

    def packet(self, seq: int, now: float) -> dict:
        age = None if self.sample_age_ms is None else min(
            UINT32_MAX, self.sample_age_ms + max(0, int((now - self.observed_at) * 1000))
        )
        status = "stale" if self.status == "live" and age is not None and age >= 1500 else self.status
        packet = {"v": 1, "seq": seq, "distance_cm": self.distance_cm, "status": status,
                  "sample_age_ms": age, "rover_connected": self.rover_connected, "source": self.source}
        # Omit auto for compatibility with the original seven-field display protocol.
        if self.expression == "happy" and now - self.observed_at < 3:
            packet["expression"] = "happy"
        return packet


def unavailable(now: float) -> Observation:
    return Observation(None, "unavailable", None, False, "rover", now)


def from_state(state: dict, now: float) -> Observation:
    """Map only cached real-rover telemetry. Never query the UNO or claim a pilot."""
    if not isinstance(state, dict) or state.get("mode") != "hardware" or not isinstance(state.get("bench"), dict):
        raise ProtocolError("Expected the real-rover controller's cached state")
    status = state["bench"].get("distance_status")
    if not isinstance(status, str) or status not in STATUSES:
        raise ProtocolError("Unsupported distance state; update the rover controller")
    auto = state["bench"].get("auto") or {}
    expression = auto.get("expression", "auto") if isinstance(auto, dict) else "auto"
    if not isinstance(expression, str) or expression not in EXPRESSIONS:
        raise ProtocolError("Unsupported display expression")
    connected = state.get("rover") == "connected"
    cm, age = state.get("distance_cm"), state["bench"].get("distance_age_ms")
    if age is not None:
        uint(age)
    if not connected:
        return unavailable(now)
    if status == "unavailable":
        return Observation(None, status, age, connected, "rover", now, expression)
    if status == "invalid":
        cm = None
    if cm is not None and not 1 <= uint(cm) <= 150:
        raise ProtocolError("Distance outside the factory driver's range")
    if status == "live" and (cm is None or age is None):
        raise ProtocolError("Live distance requires a value and sample age")
    return Observation(cm, status, age, connected, "rover", now, expression)


def encode_packet(packet: dict) -> bytes:
    required = {"v", "seq", "distance_cm", "status", "sample_age_ms", "rover_connected", "source"}
    if not isinstance(packet, dict) or set(packet) not in (required, required | {"expression"}) or uint(packet["v"]) != 1:
        raise ProtocolError("Unsupported packet schema")
    expression = packet.get("expression", "auto")
    if not isinstance(expression, str) or expression not in EXPRESSIONS:
        raise ProtocolError("Invalid display expression")
    uint(packet["seq"], minimum=1)
    cm, age, status = packet["distance_cm"], packet["sample_age_ms"], packet["status"]
    if not isinstance(status, str) or status not in STATUSES or not isinstance(packet["source"], str) or packet["source"] not in {"rover", "demo"} or type(packet["rover_connected"]) is not bool:
        raise ProtocolError("Invalid packet fields")
    if cm is not None and not 1 <= uint(cm) <= 150:
        raise ProtocolError("Distance must be null or an integer from 1 to 150")
    if age is not None:
        uint(age)
    if status == "live" and (cm is None or age is None):
        raise ProtocolError("Live packet is missing data")
    if status in {"invalid", "unavailable"} and cm is not None:
        raise ProtocolError("Invalid/unavailable readings must be null")
    data = (json.dumps(packet, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > 256:
        raise ProtocolError("Packet exceeds the 256-byte frame limit")
    return data


class LatestMailbox:
    def __init__(self):
        self.value = unavailable(time.monotonic())


async def acquire(mailbox, *, state_url=DEFAULT_STATE_URL, demo=False, distance=37, client=None, interval=.5):
    """Independent acquisition task. Every publication replaces the previous one."""
    if demo:
        while True:
            mailbox.value = Observation(distance, "live", 0, False, "demo", time.monotonic())
            await asyncio.sleep(interval)
    context = httpx.AsyncClient(timeout=1, follow_redirects=False) if client is None else client
    async with context as http:
        while True:
            started = time.monotonic()
            try:
                response = await http.get(state_url)
                response.raise_for_status()
                # Anchor conservatively at request start: HTTP transit time must
                # not make the server's cached sample appear newer than it is.
                mailbox.value = from_state(response.json(), started)
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                mailbox.value = unavailable(time.monotonic())
            await asyncio.sleep(max(0, interval - (time.monotonic() - started)))


def chunk_size(client) -> int:
    try:
        mtu = client.mtu_size
        return min(180, mtu - 3) if type(mtu) is int and mtu >= 23 else 20
    except Exception:
        return 20


class Acknowledgements:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.pending: tuple[int, asyncio.Future] | None = None

    def callback(self, _characteristic, data):
        self.loop.call_soon_threadsafe(self.receive, bytes(data))

    def receive(self, data):
        if self.pending is None or self.pending[1].done():
            return
        expected, future = self.pending
        if len(data) != 8 or data[:3] != b"BS\x01" or data[3] > 3:
            future.set_exception(ProtocolError("Malformed display acknowledgement"))
            return
        sequence = struct.unpack_from("<I", data, 4)[0]
        if sequence != expected and not (sequence == 0 and data[3] != 0):
            return  # Ignore an unrelated, delayed ACK.
        if data[3] != 0:
            future.set_exception(ProtocolError(f"Display rejected update (status {data[3]})"))
        else:
            future.set_result(sequence)

    async def deliver(self, client, packet):
        if self.pending is not None:
            raise ProtocolError("Only one display update may be outstanding")
        data = encode_packet(packet)
        future = self.loop.create_future()
        self.pending = (packet["seq"], future)  # Register before the first chunk.
        try:
            size = chunk_size(client)
            async with asyncio.timeout(WRITE_TIMEOUT_SECONDS):
                for offset in range(0, len(data), size):
                    if future.done():
                        future.result()  # Stop sending immediately on a global error ACK.
                    await client.write_gatt_char(RX_UUID, data[offset:offset + size], response=True)
            return await asyncio.wait_for(future, ACK_TIMEOUT_SECONDS)
        finally:
            self.pending = None
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()


def select_device(devices, device_id=None):
    selected = [device for device in devices if device_id is None or device.address.lower() == device_id.lower()]
    if len(selected) > 1:
        raise DeviceSelectionError("Several displays found. Run --list, then select one with --device-id.")
    if not selected:
        raise ConnectionError("Display not found. Check USB power and that its BLE firmware is running.")
    return selected[0]


class BleakRuntime:
    def __init__(self):
        # This is the only import that loads CoreBluetooth; never used by tests.
        try:
            from bleak import BleakClient, BleakScanner
            from bleak.exc import BleakBluetoothNotAvailableError
        except ImportError as error:
            raise DeviceSelectionError("Install the optional BLE dependencies; see docs/BLE_DISPLAY.md.") from error
        self.Client, self.Scanner, self.NotAvailable = BleakClient, BleakScanner, BleakBluetoothNotAvailableError

    async def discover(self):
        results = await self.Scanner.discover(timeout=5, return_adv=True, service_uuids=[SERVICE_UUID])
        return [device for device, advertisement in results.values()
                if SERVICE_UUID in [value.lower() for value in advertisement.service_uuids]]

    @asynccontextmanager
    async def connect(self, device):
        client = self.Client(device, services=[SERVICE_UUID], timeout=15)
        try:
            await asyncio.wait_for(client.connect(), 15)
            yield client
        finally:
            with suppress(Exception):
                await asyncio.wait_for(client.disconnect(), 5)

    def guidance(self, error):
        if isinstance(error, self.NotAvailable):
            return ("Bluetooth is unavailable. Enable Bluetooth and allow your terminal application in "
                    "System Settings > Privacy & Security > Bluetooth, then rerun the sender.")
        return None


def reconnect_delay(attempt):
    return (1, 2, 4, 5)[min(attempt, 3)]


async def stream(client, mailbox, *, on_applied=lambda: None):
    acks = Acknowledgements()
    await client.start_notify(ACK_UUID, acks.callback)
    sequence = 1
    while client.is_connected:
        started = time.monotonic()
        packet = mailbox.value.packet(sequence, started)
        await acks.deliver(client, packet)
        if sequence == 1:
            on_applied()
            print("Display applied update #1. Sender connected; rover control remains independent.", flush=True)
        if sequence == UINT32_MAX:
            raise ProtocolError("Restarting the display session before sequence rollover")
        sequence += 1
        await asyncio.sleep(max(0, .5 - (time.monotonic() - started)))
    raise ConnectionError("Display disconnected")


async def run_sender(args, *, runtime=None, mailbox=None):
    runtime = runtime or BleakRuntime()
    if args.list:
        try:
            for device in await runtime.discover():
                print(f"{device.address}  {device.name or 'Bullseye display'}")
        except Exception as error:
            guidance = runtime.guidance(error)
            if not guidance:
                raise
            print(guidance, file=sys.stderr)
            return 3
        return 0
    mailbox = mailbox or LatestMailbox()
    producer = asyncio.create_task(acquire(mailbox, state_url=args.state_url, demo=args.demo, distance=args.distance_cm))
    attempt = 0
    def applied():
        nonlocal attempt
        attempt = 0
    try:
        while True:
            try:
                device = select_device(await runtime.discover(), args.device_id)
                print(f"Connecting to display {device.address}…", flush=True)
                async with runtime.connect(device) as client:
                    await stream(client, mailbox, on_applied=applied)
            except DeviceSelectionError:
                raise
            except Exception as error:
                guidance = runtime.guidance(error)
                if guidance:
                    print(guidance, file=sys.stderr)
                    return 3
                delay = reconnect_delay(attempt)
                reason = str(error) if isinstance(error, (ProtocolError, ConnectionError)) else type(error).__name__
                print(f"Display connection interrupted: {reason}; retrying in {delay}s.", flush=True)
                await asyncio.sleep(delay)
                attempt += 1
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--demo", action="store_true", help="Send explicitly labelled demo data without Mission Control")
    parser.add_argument("--distance-cm", type=int, help="Demo distance, 1–150 cm; default 37")
    modes.add_argument("--list", action="store_true", help="List matching displays and their macOS CoreBluetooth identifiers")
    parser.add_argument("--device-id", help="Select the identifier printed by --list; macOS uses a UUID, not a MAC address")
    parser.add_argument("--state-url", default=DEFAULT_STATE_URL, help="Read-only cached telemetry endpoint")
    args = parser.parse_args(argv)
    if args.distance_cm is not None and not args.demo:
        parser.error("--distance-cm is only valid with --demo")
    args.distance_cm = 37 if args.distance_cm is None else args.distance_cm
    if not 1 <= args.distance_cm <= 150:
        parser.error("--distance-cm must be 1–150")
    try:
        scheme = httpx.URL(args.state_url).scheme
    except httpx.InvalidURL:
        scheme = ""
    if scheme not in {"http", "https"}:
        parser.error("--state-url must use HTTP(S)")
    return args


def main(argv=None):
    args = arguments(argv)  # --help exits before any BLE import or access.
    try:
        return asyncio.run(run_sender(args))
    except DeviceSelectionError as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Display sender stopped. No rover commands were sent.")
        return 0
    except Exception as error:
        print(f"Display sender failed ({type(error).__name__}). Check Bluetooth permission and setup instructions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
