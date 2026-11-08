"""Frame sources for Bullseye vision.

CameraStream is the ONE component that connects to the ESP32 MJPEG stream.
The ESP32 stream server serves a single client at a time, so every consumer
(OpenCV test GUI today; navigation and Mission Control later) must share one
CameraStream instance inside one process instead of opening its own connection:

    cam = CameraStream(url).start()
    frame = cam.wait_for_frame(after_id=last_id, timeout=1.0)   # newest frame only
    frame.image   # BGR numpy array, read-only (copy before drawing on it)
    frame.jpeg    # original JPEG bytes, e.g. to re-serve to a web page later

Only the newest frame is kept, so a slow consumer skips frames instead of
falling behind (no latency build-up).

Stream format note (firmware app_httpd.cpp): each part is
"Content-Type: image/jpeg\\r\\nContent-Length: N\\r\\n\\r\\n<jpeg>\\r\\n--BOUNDARY\\r\\n",
and the very first part is NOT preceded by a boundary. MjpegParser therefore
does not depend on boundaries: it uses Content-Length when present and falls
back to JPEG start/end markers otherwise.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("bullseye.camera")

SOI = b"\xff\xd8"  # JPEG start-of-image marker
EOI = b"\xff\xd9"  # JPEG end-of-image marker
_CONTENT_LENGTH = re.compile(rb"content-length:\s*(\d+)", re.IGNORECASE)
_ROTATIONS = {
    0: None,
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


@dataclass(frozen=True)
class Frame:
    image: np.ndarray      # BGR, read-only
    jpeg: bytes            # JPEG bytes as received (before any rotation)
    frame_id: int          # increases by 1 per decoded frame
    received_at: float     # time.monotonic() when the last JPEG byte arrived on the laptop
    received_wall: float   # time.time() at the same moment (for logs / JSON)


class RateMeter:
    """Events per second over a sliding time window. Thread-safe."""

    def __init__(self, window_s: float = 2.0):
        self.window_s = window_s
        self._times: deque = deque()
        self._lock = threading.Lock()

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._times.append(now)
            self._trim(now)

    def rate(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._trim(now)
            if len(self._times) < 2:
                return 0.0
            span = self._times[-1] - self._times[0]
            return (len(self._times) - 1) / span if span > 0 else 0.0

    def _trim(self, now: float) -> None:
        while self._times and now - self._times[0] > self.window_s:
            self._times.popleft()


class MjpegParser:
    """Incremental MJPEG parser: feed() raw bytes, get back complete JPEGs."""

    MAX_BUFFER = 4_000_000   # resync if this much data arrives without a frame
    MAX_FRAME = 2_000_000

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list:
        self._buf += data
        frames = []
        while True:
            jpeg = self._next_frame()
            if jpeg is None:
                break
            frames.append(jpeg)
        if len(self._buf) > self.MAX_BUFFER:
            log.warning("MJPEG parser buffer overflow; resyncing")
            self._buf.clear()
        return frames

    def _next_frame(self) -> bytes | None:
        buf = self._buf
        start = buf.find(SOI)
        if start < 0:
            if len(buf) > 4096:          # no image start yet; keep only a short tail
                del buf[:-4096]
            return None
        match = None
        for match in _CONTENT_LENGTH.finditer(buf, 0, start):
            pass                          # keep the last header before this image
        length = int(match.group(1)) if match else None
        if length is not None and 4 <= length <= self.MAX_FRAME:
            end = start + length
            if len(buf) < end:
                return None
            jpeg = bytes(buf[start:end])
            del buf[:end]
            return jpeg
        end = buf.find(EOI, start + 2)
        if end < 0:
            return None
        jpeg = bytes(buf[start:end + 2])
        del buf[:end + 2]
        return jpeg


def decode_jpeg(jpeg: bytes, rotate: int = 0) -> np.ndarray | None:
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    code = _ROTATIONS.get(rotate)
    if code is not None:
        image = cv2.rotate(image, code)
    image.flags.writeable = False
    return image


def send_camera_controls(control_url: str, controls: dict, timeout: float = 2.0) -> None:
    """Apply runtime sensor settings via the firmware's /control endpoint.

    These live in ESP32 RAM only (reset on power cycle); no firmware change.
    """
    for var, val in controls.items():
        url = f"{control_url}?{urllib.parse.urlencode({'var': var, 'val': val})}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                resp.read()
            log.info("camera control %s=%s OK", var, val)
        except Exception as exc:  # non-fatal: keep streaming with defaults
            log.warning("camera control %s=%s failed: %s", var, val, exc)


class _FrameStore:
    """Shared newest-frame slot plus the waiting logic used by all sources."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._latest: Frame | None = None
        self._next_id = 0
        self._stopped = False

    def _publish(self, image: np.ndarray, jpeg: bytes) -> Frame:
        with self._cond:
            frame = Frame(image, jpeg, self._next_id, time.monotonic(), time.time())
            self._next_id += 1
            self._latest = frame
            self._cond.notify_all()
        return frame

    def latest(self) -> Frame | None:
        with self._cond:
            return self._latest

    def wait_for_frame(self, after_id: int = -1, timeout: float = 1.0) -> Frame | None:
        """Return the newest frame with frame_id > after_id, or None on timeout."""
        with self._cond:
            self._cond.wait_for(
                lambda: self._stopped or (self._latest is not None and self._latest.frame_id > after_id),
                timeout,
            )
            if self._latest is not None and self._latest.frame_id > after_id:
                return self._latest
            return None

    def _wake_all(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()


class CameraStream(_FrameStore):
    """Background reader for the ESP32 MJPEG stream with automatic reconnect."""

    def __init__(self, url: str, timeout_s: float = 3.0, reconnect_min_s: float = 0.5,
                 reconnect_max_s: float = 5.0, rotate: int = 0,
                 control_url: str | None = None, controls: dict | None = None):
        super().__init__()
        if rotate not in _ROTATIONS:
            raise ValueError(f"rotate must be one of {sorted(_ROTATIONS)}")
        self.url = url
        self.timeout_s = timeout_s
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self.rotate = rotate
        self.control_url = control_url
        self.controls = dict(controls or {})
        self.state = "idle"           # idle | connecting | streaming | reconnecting | stopped
        self.last_error = ""
        self.connects = 0             # successful HTTP connections
        self.disconnects = 0          # connection losses / failed attempts
        self.decode_errors = 0
        self._stream_rate = RateMeter()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_config(cls, cam_cfg: dict, url: str | None = None) -> "CameraStream":
        return cls(
            url or cam_cfg["stream_url"],
            timeout_s=cam_cfg["timeout_s"],
            reconnect_min_s=cam_cfg["reconnect_min_s"],
            reconnect_max_s=cam_cfg["reconnect_max_s"],
            rotate=cam_cfg["rotate"],
            control_url=cam_cfg.get("control_url"),
            controls=cam_cfg.get("controls"),
        )

    # -- lifecycle -------------------------------------------------------
    def start(self) -> "CameraStream":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="camera-reader", daemon=True)
            self._thread.start()
        return self

    def stop(self, wait_s: float = 1.0) -> None:
        """Ask the reader to stop. Waits at most wait_s: the reader is a daemon
        thread, so a read blocked on a dead connection never delays program exit."""
        self._stop.set()
        self._wake_all()
        if self._thread is not None:
            self._thread.join(timeout=wait_s)
        self.state = "stopped"

    def __enter__(self) -> "CameraStream":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- metrics -----------------------------------------------------------
    def stream_fps(self) -> float:
        """Decoded frames per second received from the ESP32 (2 s window)."""
        return self._stream_rate.rate()

    def stats(self) -> dict:
        latest = self.latest()
        return {
            "state": self.state,
            "stream_fps": round(self.stream_fps(), 2),
            "frames": 0 if latest is None else latest.frame_id + 1,
            "connects": self.connects,
            "disconnects": self.disconnects,
            "decode_errors": self.decode_errors,
            "last_error": self.last_error,
        }

    # -- reader thread -----------------------------------------------------
    def _run(self) -> None:
        delay = self.reconnect_min_s
        while not self._stop.is_set():
            self.state = "connecting"
            try:
                req = urllib.request.Request(self.url, headers={"User-Agent": "bullseye-vision"})
                # The timeout also applies to every read, so a silent Wi-Fi drop
                # raises after timeout_s instead of hanging forever.
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    self.connects += 1
                    self.state = "streaming"
                    log.info("connected to %s", self.url)
                    if self.control_url and self.controls:
                        # Stream start re-enables face detection in the firmware,
                        # so controls such as face_detect=0 must follow each connect.
                        send_camera_controls(self.control_url, self.controls)
                    parser = MjpegParser()
                    while not self._stop.is_set():
                        chunk = resp.read1(65536)
                        if not chunk:
                            raise ConnectionError("stream closed by camera")
                        for jpeg in parser.feed(chunk):
                            image = decode_jpeg(jpeg, self.rotate)
                            if image is None:
                                self.decode_errors += 1
                                continue
                            self._publish(image, jpeg)
                            self._stream_rate.tick()
                            delay = self.reconnect_min_s
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("camera stream lost: %s", self.last_error)
            if self._stop.is_set():
                break
            self.disconnects += 1
            self.state = "reconnecting"
            self._stop.wait(delay)
            delay = min(delay * 2, self.reconnect_max_s)
        self.state = "stopped"


class FileSource(_FrameStore):
    """Offline source with the same interface: an image, a folder of images, or a video.

    Images repeat at `fps` so calibration works on a still frame; call
    next_image() to advance through a folder. Videos play and loop.
    """

    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}

    def __init__(self, path: str, fps: float = 15.0):
        super().__init__()
        p = Path(path)
        self.fps = fps
        self.state = "file"
        self._video = None
        self._images: list = []
        self._index = 0
        if p.is_dir():
            self._images = sorted(q for q in p.iterdir() if q.suffix.lower() in self.IMAGE_EXTS)
        elif p.suffix.lower() in self.IMAGE_EXTS:
            self._images = [p]
        else:
            self._video = cv2.VideoCapture(str(p))
            if not self._video.isOpened():
                raise ValueError(f"cannot open {path}")
        if self._video is None and not self._images:
            raise ValueError(f"no images found at {path}")
        self._rate = RateMeter()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="file-source", daemon=True)

    @property
    def current_name(self) -> str:
        return self._images[self._index].name if self._images else "video"

    def next_image(self) -> None:
        if self._images:
            self._index = (self._index + 1) % len(self._images)

    def start(self) -> "FileSource":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake_all()
        self._thread.join(timeout=1.0)

    def stream_fps(self) -> float:
        return self._rate.rate()

    def stats(self) -> dict:
        return {"state": self.state, "stream_fps": round(self.stream_fps(), 2), "source": self.current_name}

    def _run(self) -> None:
        period = 1.0 / self.fps
        cache: dict = {}
        while not self._stop.is_set():
            if self._video is not None:
                ok, image = self._video.read()
                if not ok:
                    self._video.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                ok, buf = cv2.imencode(".jpg", image)
                jpeg = buf.tobytes() if ok else b""
            else:
                path = self._images[self._index]
                if path not in cache:
                    jpeg = path.read_bytes()
                    cache[path] = (decode_jpeg(jpeg), jpeg)
                image, jpeg = cache[path]
                if image is None:
                    log.warning("cannot decode %s", path)
                    self.next_image()
                    continue
            image.flags.writeable = False
            self._publish(image, jpeg)
            self._rate.tick()
            self._stop.wait(period)
