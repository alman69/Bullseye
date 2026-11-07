import asyncio
from io import BytesIO
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

from .config import Settings


class JpegParser:
    """Bounded JPEG extraction, independent of multipart headers/chunk boundaries."""

    def __init__(self, max_bytes=2_000_000):
        self.buffer = bytearray()
        self.max_bytes = max_bytes

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buffer.extend(chunk)
        frames = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                self.buffer = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > self.max_bytes:
                    self.buffer.clear()
                    raise ValueError("Camera frame exceeded size limit")
                break
            if end + 2 > self.max_bytes:
                self.buffer.clear()
                raise ValueError("Camera frame exceeded size limit")
            frames.append(bytes(self.buffer[:end + 2]))
            del self.buffer[:end + 2]
        return frames


class CameraHub:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.source = "live" if settings.camera_url else (
            "simulation" if settings.mode == "simulation" else "unconfigured"
        )
        self.latest: bytes | None = None
        self.frame_at = 0.0
        self.sequence = 0
        self.error: str | None = None
        self.scene = {"phase": "idle", "target": "red", "phase_age": 0}
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None

    async def start(self):
        if self.source != "unconfigured":
            self.task = asyncio.create_task(self._live() if self.source == "live" else self._simulate())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        if self.latest:
            queue.put_nowait(self.latest)
        return queue

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)

    def publish(self, frame: bytes):
        self.latest = frame
        self.frame_at = time.monotonic()
        self.sequence += 1
        self.error = None
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(frame)

    def status(self):
        age = time.monotonic() - self.frame_at if self.latest else None
        status = "unconfigured" if self.source == "unconfigured" else (
            "streaming" if age is not None and age < 2 else "stale" if age is not None else "connecting"
        )
        return {"source": self.source, "status": status,
                "frame_age_ms": round(age * 1000) if age is not None else None,
                "error": self.error, "sequence": self.sequence}

    async def _live(self):
        while True:
            try:
                async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                    async with client.stream("GET", self.settings.camera_url) as response:
                        response.raise_for_status()
                        parser = JpegParser()
                        last_sent = 0.0
                        async for chunk in response.aiter_bytes():
                            frames = parser.feed(chunk)
                            now = time.monotonic()
                            if frames and now - last_sent >= 0.1:
                                self.publish(frames[-1])
                                last_sent = now
                self.error = "Camera stream ended; reconnecting."
            except (httpx.HTTPError, ValueError):
                # Do not expose a configured URL or its possible credentials to visitors.
                self.error = "Camera stream unavailable; reconnecting."
            await asyncio.sleep(1)

    async def _simulate(self):
        while True:
            self.publish(self.render_scene())
            await asyncio.sleep(0.1)

    def render_scene(self) -> bytes:
        """Procedural mock rover view. This is never represented as real footage."""
        image = Image.new("RGB", (800, 450), "#16151d")
        draw = ImageDraw.Draw(image)
        small = ImageFont.load_default(size=14)
        for y in range(450):
            k = y / 450
            draw.line((0, y, 800, y), fill=(int(26 + 35*k), int(24 + 18*k), int(35 + 14*k)))
        draw.polygon([(0, 170), (90, 135), (170, 183), (270, 120), (390, 181),
                      (490, 143), (620, 174), (730, 130), (800, 166), (800, 450), (0, 450)], fill="#42343c")
        draw.polygon([(0, 244), (190, 182), (430, 219), (610, 188), (800, 236),
                      (800, 450), (0, 450)], fill="#57434a")
        for i in range(65):
            x = (i * 137 + 23) % 800
            y = 225 + (i * 71) % 225
            r = 1 + i % 3
            draw.ellipse((x-r*2, y-r, x+r*2, y+r), fill="#6a5055" if i % 2 else "#3d3038")
        phase = self.scene["phase"]
        age = self.scene.get("phase_age", 0)
        approaching = phase in {"approaching", "awaiting_confirmation", "collecting", "collected"}
        progress = min(age / max(self.settings.approach_seconds, .01), 1) if phase == "approaching" else 1
        colors = {"red": ("#d64857", "#f79599"), "green": ("#499c77", "#8dd7ad"),
                  "blue": ("#588bc5", "#9ac2ee")}
        selected = self.scene["target"]
        for index, (name, (base, light)) in enumerate(colors.items()):
            x, y, r = [(225, 285, 29), (570, 260, 23), (635, 342, 38)][index]
            if name == selected and approaching:
                x = int(x + (400-x)*progress)
                y = int(y + (323-y)*progress)
                r = int(r + 35*progress)
            points = [(x-r, y+5), (x-int(r*.7), y-int(r*.65)), (x+int(r*.3), y-r),
                      (x+r, y-int(r*.15)), (x+int(r*.8), y+int(r*.7)), (x-int(r*.4), y+r)]
            draw.polygon(points, fill=base)
            draw.polygon([points[0], points[1], points[2], (x, y)], fill=light)
            draw.line([points[2], (x,y), points[4]], fill="#3a303b", width=2)
            draw.ellipse((x-r-7, y+r, x+r+9, y+r+7), fill="#3f333c")
        # A central reticle and genuinely simulated status, not fictitious telemetry.
        draw.line((375, 225, 391, 225), fill="#d6cbc7", width=1)
        draw.line((409, 225, 425, 225), fill="#d6cbc7", width=1)
        draw.line((400, 200, 400, 216), fill="#d6cbc7", width=1)
        draw.line((400, 234, 400, 250), fill="#d6cbc7", width=1)
        if approaching:
            draw.rounded_rectangle((310, 235, 490, 410), radius=5, outline=colors[selected][1], width=2)
            draw.text((318, 242), f"{selected.upper()} SAMPLE", font=small, fill="#ffffff")
        draw.rectangle((0, 0, 800, 43), fill="#15131d")
        draw.ellipse((21, 17, 29, 25), fill="#ef506d")
        draw.text((300, 13), "SIMULATED CAMERA", font=small, fill="#e7b9c3")
        draw.text((648, 13), "VIRTUAL ARENA", font=small, fill="#b9a9b4")
        draw.rectangle((0, 414, 800, 450), fill="#15131d")
        draw.text((20, 423), "BULLSEYE / TRAINING ENVIRONMENT", font=small, fill="#b2a4ae")
        draw.text((660, 422), "800 x 450", font=small, fill="#b2a4ae")
        output = BytesIO()
        image.save(output, "JPEG", quality=78)
        return output.getvalue()
