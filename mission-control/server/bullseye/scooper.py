"""USB serial link to the scooper ESP32-S3 (firmware/esp32-scooper).

The server is the port's single owner, like the rover bridge. Line protocol (see
bullseye_scooper.ino): each command gets one "ok" or "error: <reason>" line, and
"state=<state> [<sequence>:<step>] left=<deg> right=<deg>" lines report progress.
"""
import asyncio

BAUD = 115200
REPLY_TIMEOUT_S = 1.5
CONNECT_ATTEMPTS = 4
# USB-UART bridges on ESP32-S3 dev boards, plus Espressif's own USB.
ESP_USB_VIDS = {0x1A86, 0x10C4, 0x0403, 0x303A}
COMMANDS = {"squeeze", "release", "home", "stop", "relax", "status"}

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # Optional until the scooper is used; snapshot reports it.
    serial = None


def open_serial(port):
    link = serial.Serial()
    link.port, link.baudrate, link.timeout = port, BAUD, 0.2
    # Leave DTR/RTS released: on ESP32 dev boards they drive EN/BOOT, and
    # asserting them on open would reset the board and jerk the sticks home.
    link.dtr = link.rts = False
    link.open()
    return link


def list_serial_ports():
    return [(port.device, port.vid, port.description) for port in serial.tools.list_ports.comports()]


def parse_status(line):
    """'state=sequence squeeze:out left=60 right=60' -> dict, or None."""
    parts = line.split()
    if not parts or not parts[0].startswith("state="):
        return None
    status = {"state": parts[0][6:], "sequence": None, "step": None, "angles": {}}
    for part in parts[1:]:
        if ":" in part:
            status["sequence"], status["step"] = part.split(":", 1)
        elif "=" in part:
            name, _, value = part.partition("=")
            try:
                status["angles"][name] = int(value)
            except ValueError:
                return None
    return status


class ScooperLink:
    def __init__(self, port="", *, opener=None, list_ports=None):
        self.port_setting = port
        self.opener = opener or open_serial
        self.list_ports = list_ports or list_serial_ports
        self.available = serial is not None or opener is not None
        self.link = None
        self.port = None
        self.status = "disconnected"  # disconnected | connecting | ready | error
        self.error = None if self.available else "pyserial is not installed in the server venv."
        self.state = self.sequence = self.step = None
        self.angles = {}
        self.busy = None  # Command in flight, if any.
        self.on_change = lambda: None
        self.pending = None
        self.reader = None
        self.task = None
        self.request_lock = asyncio.Lock()

    @property
    def ready(self):
        return self.status == "ready"

    def snapshot(self):
        return {"available": self.available, "status": self.status, "port": self.port, "error": self.error,
                "state": self.state, "sequence": self.sequence, "step": self.step,
                "angles": dict(self.angles), "busy": self.busy}

    def pick_port(self):
        if self.port_setting:
            return self.port_setting
        ports = self.list_ports()
        candidates = [device for device, vid, _ in ports if vid in ESP_USB_VIDS]
        if len(candidates) == 1:
            return candidates[0]
        found = ", ".join(f"{device} ({description})" for device, _, description in ports) or "none"
        reason = "No ESP32 USB serial port found" if not candidates else "Several ESP32 USB ports found"
        raise ConnectionError(f"{reason}. Set SCOOPER_PORT (for example COM5). Ports: {found}.")

    def launch(self, job):
        """Run a link job in the background so a slow port never blocks the bench lock."""
        if self.task and not self.task.done():
            raise RuntimeError("busy")
        self.task = asyncio.create_task(job)
        return self.task

    async def connect(self):
        await self.close()
        self.status, self.error = "connecting", None
        self.on_change()
        try:
            if not self.available:
                raise ConnectionError("pyserial is not installed in the server venv.")
            port = self.pick_port()
            self.link = await asyncio.to_thread(self.opener, port)
            self.port = port
            self.reader = asyncio.create_task(self._read_loop(self.link))
            # The board may still be booting (some adapters reset it anyway): retry.
            for _ in range(CONNECT_ATTEMPTS):
                if await self._request("status") is True:
                    break
            else:
                raise ConnectionError(f"No reply from the scooper firmware on {port}. Is bullseye_scooper uploaded?")
            self.status = "ready"
        except Exception as exc:
            await self.close()
            self.status, self.error = "error", str(exc)
        finally:
            self.on_change()

    async def command(self, name):
        if name not in COMMANDS:
            raise ValueError(name)
        self.busy = name
        self.on_change()
        try:
            result = await self._request(name)
            if result is None:
                raise ConnectionError(f"The scooper did not answer '{name}'.")
            self.error = None if result is True else result
            return result
        except ConnectionError as exc:
            await self.close()
            self.status, self.error = "error", str(exc)
            raise
        finally:
            self.busy = None
            self.on_change()

    async def _request(self, name):
        """True on 'ok', the reason string on 'error: ...', None on timeout."""
        async with self.request_lock:
            link = self.link
            if link is None:
                raise ConnectionError("The scooper USB link is not connected.")
            self.pending = asyncio.get_running_loop().create_future()
            try:
                await asyncio.to_thread(link.write, f"{name}\n".encode())
                return await asyncio.wait_for(self.pending, REPLY_TIMEOUT_S)
            except asyncio.TimeoutError:
                return None
            except Exception as exc:
                raise ConnectionError(f"Scooper USB write failed: {exc}") from exc
            finally:
                self.pending = None

    def _resolve(self, value):
        if self.pending and not self.pending.done():
            self.pending.set_result(value)

    async def _read_loop(self, link):
        try:
            while True:
                raw = await asyncio.to_thread(link.readline)
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                if line == "ok":
                    self._resolve(True)
                elif line.startswith("error:"):
                    self._resolve(line[6:].strip() or "unknown error")
                elif (status := parse_status(line)) is not None:
                    self.state, self.sequence, self.step = status["state"], status["sequence"], status["step"]
                    self.angles = status["angles"]
                    self.on_change()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.link is link:
                self.link = None
                self.status, self.error = "error", f"Scooper USB link lost: {exc}"
                self.state = None
                self.on_change()

    async def close(self):
        link, self.link = self.link, None
        reader, self.reader = self.reader, None
        if reader and reader is not asyncio.current_task():
            reader.cancel()
        if link is not None:
            await asyncio.to_thread(link.close)
        if reader and reader is not asyncio.current_task():
            await asyncio.gather(reader, return_exceptions=True)
        self.state = None
        if self.status in {"ready", "connecting"}:
            self.status = "disconnected"
