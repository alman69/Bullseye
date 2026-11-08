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

import httpx

DEFAULT_URL = "http://192.168.4.50"
REQUEST_TIMEOUT_S = 1.5
POLL_S = 0.5
MAX_MISSED_POLLS = 4  # About two seconds of silence before the link is marked lost.
COMMANDS = {"squeeze", "release", "home", "stop", "relax"}


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

    @property
    def ready(self):
        return self.status == "ready"

    def snapshot(self):
        return {"available": True, "status": self.status, "address": self.url, "error": self.error,
                "state": self.state, "sequence": self.sequence, "step": self.step,
                "angles": dict(self.angles), "busy": self.busy}

    def launch(self, job):
        """Run a link job in the background so a slow network never blocks the bench lock."""
        if self.task and not self.task.done():
            raise RuntimeError("busy")
        self.task = asyncio.create_task(job)
        return self.task

    def _client(self):
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.url, timeout=REQUEST_TIMEOUT_S, transport=self.transport)
        return self.client

    def _apply(self, status):
        """Copy a firmware status object in; returns True if anything changed."""
        if not isinstance(status, dict) or not isinstance(status.get("state"), str):
            raise ValueError("The scooper sent a status without a state.")
        angles = status.get("angles") if isinstance(status.get("angles"), dict) else {}
        angles = {str(name): int(value) for name, value in angles.items()
                  if isinstance(value, (int, float)) and not isinstance(value, bool)}
        new = (status["state"], status.get("sequence"), status.get("step"), angles)
        changed = new != (self.state, self.sequence, self.step, self.angles)
        self.state, self.sequence, self.step, self.angles = new
        return changed

    async def connect(self):
        await self.close()
        self.status, self.error = "connecting", None
        self.on_change()
        try:
            response = await self._client().get("/status")
            response.raise_for_status()
            self._apply(response.json())
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
        if not self.ready:
            raise ConnectionError("The scooper Wi-Fi link is not connected.")
        self.busy = name
        self.on_change()
        try:
            try:
                response = await self._client().post("/command", params={"name": name})
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ConnectionError(f"The scooper did not answer '{name}' ({describe(exc)}).") from exc
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
            self.busy = None
            self.on_change()

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
        await self._stop_polling()
        client, self.client = self.client, None
        if client is not None:
            await client.aclose()
        self.state = None
        if self.status in {"ready", "connecting"}:
            self.status = "disconnected"
