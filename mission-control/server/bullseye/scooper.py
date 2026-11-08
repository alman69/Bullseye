"""Wi-Fi link to the scooper ESP32-S3 (firmware/esp32-scooper).

The scooper joins the rover's ELEGOO access point at a fixed address, the same network
this laptop already uses for the camera and rover commands, and serves a tiny HTTP API
(see bullseye_scooper.ino):

    GET  /status              -> {"state", "sequence", "step", "angles": {"left": 90, ...}}
    POST /command?name=<cmd>  -> 200 {"ok": true, "status": {...}}
                                 409 {"ok": false, "error": "...", "status": {...}}  (refused)
                                 400 {"ok": false, "error": "..."}  (unknown command)

While connected, /status is polled so the dashboard shows each squeeze step.
"""
import asyncio
import math

import httpx

DEFAULT_URL = "http://192.168.4.50"
REQUEST_TIMEOUT_S = 1.5
POLL_S = 0.5
MAX_MISSED_POLLS = 4  # Four misses including request latency; device watchdog is independent.
COMMANDS = {"squeeze", "release", "home", "stop", "relax"}


class BoardRestarted(ConnectionError):
    pass


def describe(exc):
    return str(exc) or type(exc).__name__


class ScooperLink:
    def __init__(self, url=DEFAULT_URL, *, transport=None):
        self.url = (url or DEFAULT_URL).rstrip("/")
        self.transport = transport  # Tests inject an httpx.MockTransport.
        self.client = None
        self.status = "disconnected"  # disconnected | connecting | ready | error
        self.error = None
        self.state = self.sequence = self.step = None
        self.angles = {}
        self.busy = None  # Command in flight, if any.
        self.on_change = lambda: None
        self.task = None  # The bench's one background job (connect or a command).
        self.poller = None
        self.stop_task = None
        self.boot_id = None
        self.firmware = None
        self.command_sequence = 0
        self.reported_sequence = 0
        self.generation = 0
        self.busy_request = None

    @property
    def ready(self):
        return self.status == "ready"

    def snapshot(self):
        return {"available": True, "status": self.status, "address": self.url, "error": self.error,
                "state": self.state, "sequence": self.sequence, "step": self.step,
                "angles": dict(self.angles), "busy": "stop" if self.stop_task and not self.stop_task.done() else self.busy,
                "firmware": self.firmware, "ordered_commands": self.boot_id is not None}

    def launch(self, job):
        """Run a link job in the background so a slow network never blocks the bench lock."""
        if (self.task and not self.task.done()) or (self.stop_task and not self.stop_task.done()) or self.busy:
            raise RuntimeError("busy")
        self.task = asyncio.create_task(job)
        return self.task

    def _client(self):
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.url, timeout=REQUEST_TIMEOUT_S, transport=self.transport, trust_env=False)
        return self.client

    def _apply(self, status):
        """Copy a firmware status object in; returns True if anything changed."""
        if not isinstance(status, dict) or status.get("state") not in {"relaxed", "holding", "moving", "sequence"}:
            raise ValueError("The scooper sent a status without a state.")
        if status.get("servo_ready") is False:
            raise ValueError("Scooper servo PWM initialization failed; inspect GPIO41/42 and restart the S3.")
        boot, sequence = status.get("boot_id"), status.get("command_seq")
        if status.get("api") == 2 or self.boot_id is not None:
            if type(boot) is not int or not 1 <= boot <= 0xFFFFFFFF or type(sequence) is not int or not 0 <= sequence <= 0xFFFFFFFF:
                raise BoardRestarted("Scooper protocol changed; press Reconnect.")
            if self.boot_id is not None and boot != self.boot_id:
                raise BoardRestarted("Scooper restarted; press Reconnect before sending another command.")
            if sequence < self.reported_sequence:
                return False  # A delayed poll/command response cannot overwrite a newer STOP.
            self.boot_id, self.reported_sequence = boot, sequence
            self.command_sequence = max(self.command_sequence, sequence)
        self.firmware = status.get("firmware")
        angles = status.get("angles") if isinstance(status.get("angles"), dict) else {}
        angles = {str(name): int(value) for name, value in angles.items()
                  if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 180}
        if not all(name in angles for name in ("left", "right")):
            raise ValueError("Scooper status is missing valid commanded angles.")
        new = (status["state"], status.get("sequence"), status.get("step"), angles)
        changed = new != (self.state, self.sequence, self.step, self.angles)
        self.state, self.sequence, self.step, self.angles = new
        previous_error = self.error
        if status.get("watchdog_stopped"):
            self.error = "Scooper stopped after losing controller contact. Check the link before moving again."
        return changed or previous_error != self.error

    async def connect(self):
        await self.close()
        self.status, self.error = "connecting", None
        self.on_change()
        try:
            response = await self._client().get("/status")
            response.raise_for_status()
            self._apply(response.json())
            if self.state in {"moving", "sequence"}:
                result = await self.command("stop")
                if result is not True:
                    raise ConnectionError("Scooper did not accept the reconnect STOP: " + str(result))
            self.status = "ready"
            self.poller = asyncio.create_task(self._poll())
        except Exception as exc:
            await self.close()
            self.status = "error"
            self.error = (f"Scooper not reachable at {self.url} ({describe(exc)}). "
                          "Is it powered and joined to the rover's ELEGOO Wi-Fi?")
        finally:
            self.on_change()

    async def command(self, name):
        if name not in COMMANDS:
            raise ValueError(name)
        if not self.ready and not (name == "stop" and self.client is not None):
            raise ConnectionError("The scooper Wi-Fi link is not connected.")
        if name != "stop" and self.stop_task and not self.stop_task.done():
            raise ConnectionError("Scooper STOP is still in progress.")
        self.command_sequence += 1
        request_sequence, generation = self.command_sequence, self.generation
        if request_sequence > 0xFFFFFFFF:
            raise ConnectionError("Scooper command counter exhausted; restart the S3 and reconnect.")
        params = {"name": name}
        if self.boot_id is not None:
            params.update(boot=self.boot_id, seq=request_sequence)
        self.busy = name
        self.busy_request = request_sequence
        self.on_change()
        try:
            try:
                response = await self._client().post("/command", params=params)
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ConnectionError(f"The scooper did not answer '{name}' ({describe(exc)}).") from exc
            if generation != self.generation and name != "stop":
                return "canceled by STOP"
            if isinstance(body, dict) and isinstance(body.get("status"), dict):
                self._apply(body["status"])
            if response.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
                self.error = None
                return True
            reason = str(body.get("error") if isinstance(body, dict) and body.get("error") else f"HTTP {response.status_code}")
            self.error = reason
            return reason
        except ConnectionError as exc:
            await self._stop_polling()
            self.status, self.error, self.state = "error", str(exc), None
            raise
        finally:
            if request_sequence == self.busy_request:
                self.busy = None
                self.busy_request = None
            self.on_change()

    def request_stop(self):
        """Cancel queued/in-flight motion, then send a newer ordered STOP once."""
        self.generation += 1
        if self.stop_task and not self.stop_task.done():
            return self.stop_task
        pending = self.task if self.task and not self.task.done() and self.task is not asyncio.current_task() else None
        if pending:
            pending.cancel()
        async def finish():
            try:
                if pending:
                    await asyncio.gather(pending, return_exceptions=True)
                return await self.command("stop")
            finally:
                self.busy = None
                self.on_change()
        self.stop_task = asyncio.create_task(finish())
        self.stop_task.add_done_callback(lambda _task: self.on_change())
        self.on_change()
        return self.stop_task

    async def _poll(self):
        missed = 0
        while True:
            await asyncio.sleep(POLL_S)
            try:
                response = await self._client().get("/status")
                response.raise_for_status()
                changed = self._apply(response.json())
            except asyncio.CancelledError:
                raise
            except BoardRestarted as exc:
                self.status, self.state, self.error = "error", None, str(exc)
                self.poller = None
                self.on_change()
                return
            except Exception as exc:
                missed += 1
                if missed >= MAX_MISSED_POLLS:
                    self.status, self.state = "error", None
                    self.error = f"Scooper stopped answering at {self.url} ({describe(exc)}). Press Connect to retry."
                    self.poller = None
                    self.on_change()
                    return
                continue
            missed = 0
            if changed:
                self.on_change()

    async def _stop_polling(self):
        poller, self.poller = self.poller, None
        if poller and poller is not asyncio.current_task():
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)

    async def close(self):
        if self.stop_task and self.stop_task is not asyncio.current_task():
            await asyncio.gather(self.stop_task, return_exceptions=True)
        self.stop_task = None
        await self._stop_polling()
        client, self.client = self.client, None
        if client is not None:
            await client.aclose()
        self.state = None
        self.boot_id = self.firmware = None
        self.command_sequence = self.reported_sequence = 0
        if self.status in {"ready", "connecting"}:
            self.status = "disconnected"
