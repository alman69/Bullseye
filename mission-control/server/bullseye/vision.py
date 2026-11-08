"""Vision inside Mission Control: detection, floor check and a planner DRY RUN on the
shared camera relay's frames, plus browser-driven calibration.

The camera relay (camera.CameraHub) stays the ONLY connection to the ESP32 stream.
This service subscribes to its newest JPEG frames, processes them in a worker thread
(so video delivery never waits on vision), and publishes:
  * a per-frame result on /ws/vision (for drawing the overlay on the video), and
  * a compact summary in the normal state snapshot (for panels and calibration).

The processing code is the shared `bullseye_vision` package in software/vision, which
also holds the arena calibration (config.json, color_model.json, captures/samples.npz),
so the standalone test app and Mission Control use one calibration.

The planner here is a DRY RUN: it reports the move it would make. Nothing in this
module sends anything to the rover.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import math
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx

from .config import Settings
from .mission import CommandError

TUNABLE = {
    # "section.key": (type, min, max) - the only settings the browser may change.
    "floor.s_max": (int, 0, 255),
    "floor.v_min": (int, 0, 255),
    "detector.min_area_px": (int, 10, 5000),
    "classifier.max_distance": (float, 1.0, 12.0),
    "navigation.aim_deadband": (float, 0.02, 0.4),
    "navigation.arrive_distance_cm": (float, 2.0, 100.0),
    "navigation.approach_zone_y": (float, 0.0, 0.3),
    "navigation.search_turn_ms": (int, 100, 1000),
    "navigation.avoid_turn_ms": (int, 100, 1000),
    "black.v_max": (int, 10, 150),          # the home-base black flag: max brightness counted as "black"
}
TARGETS = ("red", "green", "blue", "yellow", "black")
# Camera sensor settings sent to the ESP32's /control endpoint (runtime only, reset on power-off;
# no firmware change). Under the floodlight: more saturation and less exposure keep colors visible.
CAMERA_CONTROLS = {"saturation": (-2, 2), "ae_level": (-2, 2), "brightness": (-2, 2), "contrast": (-2, 2)}
CAMERA_GAP_S = 3.0      # frames missing this long = camera may have restarted: re-send the settings
STEP_DURATIONS = ("200", "500", "1000")
# The shared code always comes from this repository; settings.vision_dir only chooses
# where calibration data (config.json, color_model.json, captures/) is read and written.
PACKAGE_DIR = Path(__file__).resolve().parents[3] / "software" / "vision"
MAX_GROUND_CAPTURES = 8


def _import_vision(package_dir: Path = PACKAGE_DIR):
    """Import the shared package from software/vision without requiring an install."""
    if str(package_dir) not in sys.path:
        sys.path.insert(0, str(package_dir))
    importlib.import_module("cv2")      # fail early with a clear message if OpenCV is missing
    importlib.import_module("numpy")
    from bullseye_vision import colorclass, config, detector, ground, pipeline
    return SimpleNamespace(colorclass=colorclass, config=config, detector=detector,
                           ground=ground, pipeline=pipeline)


class VisionService:
    def __init__(self, settings: Settings, camera):
        self.settings, self.camera = settings, camera
        self.dir = Path(settings.vision_dir)
        self.config_path = self.dir / "config.json"
        self.available, self.error = False, None
        self.lock = threading.Lock()            # guards detector/planner/config between thread and commands
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None
        self.target = "red"
        self.latest: dict | None = None         # last per-frame result (JSON-ready)
        self.message: str | None = None         # feedback for the last calibration action
        self.confusion: dict | None = None
        self.ground_captures: list = []         # [{"distance_cm", "bottom_y", "width"}]
        self.fov_captures: dict = {}            # {"center": error, "side": error}
        self._times: deque = deque(maxlen=30)
        self._last_frame_at = 0.0
        self._camera_busy = False
        parsed = urlparse(settings.camera_url) if settings.camera_url else None
        self.control_url = f"{parsed.scheme}://{parsed.hostname}/control" if parsed and parsed.hostname else None
        self.camera_status = {"ok": None, "message": "Not sent yet.", "at": None}
        self._image = self._prepared = self._det = None
        try:
            self.v = _import_vision()
            self._load()
            self.available = True
        except Exception as error:  # vision must never take Mission Control down
            self.error = f"Vision unavailable: {type(error).__name__}: {error}"

    # ------------------------------------------------------------ setup
    def _load(self) -> None:
        v = self.v
        self.cfg = v.config.load_config(self.config_path)
        self.detector = v.detector.ColorDetector.from_config(self.cfg, base_dir=self.dir)
        self.nav = v.pipeline.NavRunner(self.cfg)
        cc = self.cfg["classifier"]
        self.samples_path = self.dir / cc.get("samples_path", "captures/samples.npz")
        self.model_path = self.dir / cc.get("model_path", "color_model.json")
        self.samples = v.colorclass.load_samples(self.samples_path)
        self.undo: list = []

    def _reload(self) -> None:
        """Rebuild detector and planner after a calibration change (call with the lock held)."""
        samples, undo = self.samples, self.undo
        self.v.config.save_config(self.cfg, self.config_path)
        self._load()
        self.samples, self.undo = samples, undo

    async def start(self):
        if self.available:
            self.task = asyncio.create_task(self._run())

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

    def set_target(self, color: str) -> None:
        if color != self.target:
            self.target = color
            with self.lock:
                if self.available:
                    self.nav.reset()

    # ------------------------------------------------------------ frame loop
    async def _run(self):
        queue = self.camera.subscribe()
        try:
            while True:
                jpeg = await queue.get()
                seq = self.camera.sequence
                now = time.monotonic()
                if now - self._last_frame_at > CAMERA_GAP_S:      # first frame, or the stream came back
                    self._schedule_camera_apply()
                self._last_frame_at = now
                try:
                    result = await asyncio.to_thread(self._process, jpeg, seq)
                except Exception as error:
                    self.error = f"Vision frame failed: {type(error).__name__}: {error}"
                    continue
                if result is None:
                    continue
                self.error = None
                for sub in list(self.subscribers):
                    if sub.full():
                        sub.get_nowait()
                    sub.put_nowait(result)
        finally:
            self.camera.unsubscribe(queue)

    def _process(self, jpeg: bytes, seq: int):
        import cv2
        import numpy as np
        t0 = time.perf_counter()
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return None
        with self.lock:
            prepared = self.detector.prepare(image)
            det = self.detector.detect(image, self.target, seq, time.time(), prepared=prepared)
            frame = SimpleNamespace(image=image, received_at=time.monotonic())
            action = self.nav.update(frame, det, self.detector, prepared=prepared)
            self._image, self._prepared, self._det = image, prepared, det
            result = self._result(image, det, action, seq)
        now = time.monotonic()
        self._times.append(now)
        result["processing_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        result["fps"] = round((len(self._times) - 1) / (self._times[-1] - self._times[0]), 1) \
            if len(self._times) > 1 and self._times[-1] > self._times[0] else 0.0
        self.latest = result
        return result

    def _result(self, image, det, action, seq) -> dict:
        h, w = image.shape[:2]

        def box(b):
            return [round(b[0] / w, 4), round(b[1] / h, 4), round(b[2] / w, 4), round(b[3] / h, 4)]

        free = self.nav.free
        ground = self.cfg["ground"]
        stop = ground.get("stop_line_y")
        return {
            "type": "vision", "seq": seq, "w": w, "h": h, "target": self.target,
            "mode": self.detector.mode, "dry_run": True,
            "detection": det.to_dict(),
            "box": box(det.bbox) if det.detected else None,
            "center": [round(det.center_x / w, 4), round(det.center_y / h, 4)] if det.detected else None,
            "others": [box(c.bbox) for c in det.all_candidates[1:8]],
            "deadband": self.detector.settings.center_deadband,
            "free": None if free is None else {
                "heights": [round(x, 3) for x in free.heights], "blocked": free.blocked,
                "corridor": free.corridor, "roi_top": round(free.roi_top_px / h, 4),
                "blocked_height": self.nav.floor.blocked_height, "floor_visible": free.floor_visible},
            "nav": {"state": self.nav.planner.state,
                    "action": action.to_dict() if action else None,
                    "label": action.label() if action else None},
            "mask_boxes": self.detector.ignore_boxes,
            "lines": {"stop": stop,
                      "zone": None if stop is None else round(stop - self.cfg["navigation"]["approach_zone_y"], 4),
                      "horizon": ground.get("horizon_y")},
            "wb": None if self.detector.wb is None else [round(float(g), 3) for g in self.detector.wb.gains],
        }

    # ------------------------------------------------------------ camera settings
    def _controls(self) -> dict:
        return dict(self.cfg["camera"].get("controls") or {})

    def _schedule_camera_apply(self) -> None:
        if self.control_url and self._controls() and not self._camera_busy:
            self._camera_busy = True
            task = asyncio.create_task(asyncio.to_thread(self._apply_camera))
            task.add_done_callback(lambda _t: setattr(self, "_camera_busy", False))

    def _apply_camera(self) -> None:
        controls = self._controls()
        if not self.control_url:
            self.camera_status = {"ok": None, "message": "No real camera configured (simulation): settings saved, not sent.",
                                  "at": datetime.now().strftime("%H:%M:%S")}
            return
        failed = []
        for var, val in controls.items():
            try:
                httpx.get(self.control_url, params={"var": var, "val": val}, timeout=2).raise_for_status()
            except httpx.HTTPError as error:
                failed.append(f"{var} ({type(error).__name__})")
        sent = ", ".join(f"{k}={v}" for k, v in controls.items()) or "nothing to send"
        self.camera_status = {"ok": not failed,
                              "message": f"Camera not updated: {', '.join(failed)}" if failed else f"Camera set: {sent}",
                              "at": datetime.now().strftime("%H:%M:%S")}

    # ------------------------------------------------------------ state
    def summary(self) -> dict:
        if not self.available:
            return {"available": False, "error": self.error}
        latest = self.latest or {}
        det = latest.get("detection") or {}
        nav = latest.get("nav") or {}
        ground = self.cfg["ground"]
        model = self.detector.model
        return {
            "available": True, "error": self.error, "dry_run": True,
            "target": self.target, "mode": self.detector.mode,
            "classes": list(model.classes) if model else [],
            "model_fitted": model is not None,
            "camera": {"controls": self._controls(), "url_set": bool(self.control_url), **self.camera_status},
            "rover_mask": {"boxes": self.detector.ignore_boxes},
            "detected": bool(det.get("detected")), "direction": det.get("direction"),
            "horizontal_error": det.get("horizontal_error"), "distance_cm_est": det.get("distance_cm_est"),
            "nav_state": nav.get("state"), "nav_label": nav.get("label"),
            "nav_reason": (nav.get("action") or {}).get("reason"),
            "fps": latest.get("fps", 0.0), "processing_ms": latest.get("processing_ms"),
            "message": self.message,
            "sample_counts": {k: int(len(v)) for k, v in self.samples.items()},
            "confusion": self.confusion,
            "ground": {"calibrated": len(ground.get("points") or []) >= 2,
                       "stop_line_y": ground.get("stop_line_y"), "horizon_y": ground.get("horizon_y"),
                       "captures": self.ground_captures},
            "fov": {"captures": self.fov_captures, "hfov_deg": self.cfg["navigation"].get("camera_hfov_deg")},
            "steps": self.cfg["navigation"].get("steps", {"forward": {}, "turn": {}}),
            "tuning": {key: self._get(key) for key in TUNABLE},
        }

    def _get(self, dotted: str):
        if dotted == "black.v_max":
            return self.cfg["colors"].get("black", {"v": [0, 60]})["v"][1]
        section, key = dotted.split(".")
        return self.cfg[section][key]

    # ------------------------------------------------------------ commands
    async def command(self, kind: str, payload: dict) -> None:
        if not self.available:
            raise CommandError(self.error or "Vision is not available on this server.")
        handler = getattr(self, "_cmd_" + kind.removeprefix("vision_"), None)
        if handler is None:
            raise CommandError("Unknown vision command.")
        await asyncio.to_thread(self._locked, handler, payload)
        if kind == "vision_camera":           # network I/O outside the lock so frames keep flowing
            await asyncio.to_thread(self._apply_camera)

    def _locked(self, handler, payload):
        with self.lock:
            handler(payload)

    def _need_frame(self):
        if self._image is None:
            raise CommandError("No camera frame yet. Wait for video, then try again.")

    def _cmd_target(self, p):
        color = p.get("color")
        if color not in TARGETS:
            raise CommandError("Choose red, green, blue, yellow or black.")
        self.target = color
        self.nav.reset()

    def _cmd_reset_planner(self, p):
        self.nav.reset()
        self.message = "Planner reset."

    def _cmd_sample(self, p):
        import cv2
        import numpy as np
        self._need_frame()
        cls = p.get("cls")
        if cls not in self.v.colorclass.SAMPLE_CLASSES:
            raise CommandError("Unknown sample class.")
        box = p.get("box")
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in box)):
            raise CommandError("Sample box must be [x0, y0, x1, y1] between 0 and 1.")
        h, w = self._image.shape[:2]
        x0, x1 = sorted(int(round(min(1, max(0, v)) * w)) for v in (box[0], box[2]))
        y0, y1 = sorted(int(round(min(1, max(0, v)) * h)) for v in (box[1], box[3]))
        if x1 - x0 < 3 or y1 - y0 < 3:
            raise CommandError("Drag a bigger box (at least a few pixels).")
        k = self.detector.settings.blur_kernel
        src = self._prepared.corrected if self._prepared is not None else (
            cv2.GaussianBlur(self._image, (k, k), 0) if k and k > 1 else self._image)
        px = src[y0:y1, x0:x1].reshape(-1, 3)
        if len(px) > 4000:
            px = px[np.random.default_rng().choice(len(px), 4000, replace=False)]
        self.samples[cls] = np.concatenate([self.samples[cls], px]) if cls in self.samples else px.copy()
        self.undo.append((cls, len(px)))
        self.v.colorclass.save_samples(self.samples, self.samples_path)
        self.message = f"Added {len(px)} {cls} pixels."

    def _cmd_undo(self, p):
        if not self.undo:
            raise CommandError("Nothing to undo.")
        cls, n = self.undo.pop()
        self.samples[cls] = self.samples[cls][:-n]
        if not len(self.samples[cls]):
            del self.samples[cls]
        self.v.colorclass.save_samples(self.samples, self.samples_path)
        self.message = f"Removed the last {n} {cls} pixels."

    def _cmd_clear_samples(self, p):
        cls = p.get("cls")
        if cls:
            self.samples.pop(cls, None)
        else:
            self.samples = {}
        self.undo = []
        self.v.colorclass.save_samples(self.samples, self.samples_path)
        self.message = f"Cleared {cls or 'all'} samples."

    def _cmd_fit(self, p):
        cc = self.cfg["classifier"]
        try:
            model = self.v.colorclass.ColorModel.fit(self.samples, cc.get("max_distance", 4.0),
                                                     cc.get("regularization", 4.0), cc.get("floor_class", "floor"),
                                                     min_chroma=cc.get("min_chroma", 12.0))
        except ValueError as error:
            raise CommandError(str(error)) from None
        model.save(self.model_path)
        self.confusion = {k: {c: round(v, 4) for c, v in row.items()} for k, row in model.confusion(self.samples).items()}
        if self.cfg["classifier"].get("mode") == "hsv":
            self.cfg["classifier"]["mode"] = "auto"
        self._reload()
        self.message = f"Color model saved ({', '.join(model.classes)})."

    def _cmd_mode(self, p):
        mode = p.get("mode")
        if mode not in ("auto", "classifier", "hsv"):
            raise CommandError("Mode must be auto, classifier or hsv.")
        if mode == "classifier" and not self.model_path.exists():
            raise CommandError("Fit a color model first.")
        self.cfg["classifier"]["mode"] = mode
        self._reload()
        self.message = f"Detector mode: {mode}."

    def _cmd_ground_capture(self, p):
        self._need_frame()
        dist = p.get("distance_cm")
        if not isinstance(dist, (int, float)) or isinstance(dist, bool) or not 3 <= dist <= 300:
            raise CommandError("Distance must be 3-300 cm.")
        det = self._det
        if det is None or not det.detected:
            raise CommandError(f"No {self.target} block detected. Center it in view first.")
        x, y, bw, bh = det.bbox
        h, w = self._image.shape[:2]
        self.ground_captures = [c for c in self.ground_captures if c["distance_cm"] != dist]
        self.ground_captures.append({"distance_cm": float(dist), "bottom_y": round((y + bh) / h, 4),
                                     "width": round(bw / w, 4), "error": round(det.horizontal_error, 3)})
        self.ground_captures = sorted(self.ground_captures, key=lambda c: c["distance_cm"])[:MAX_GROUND_CAPTURES]
        self.message = f"Captured the block at {dist:g} cm."

    def _cmd_ground_clear(self, p):
        self.ground_captures = []
        self.message = "Stop-line captures cleared."

    def _cmd_ground_fit(self, p):
        if len(self.ground_captures) < 2:
            raise CommandError("Capture the block at two or more distances first.")
        samples = [(c["bottom_y"], c["distance_cm"], c["width"]) for c in self.ground_captures]
        try:
            model = self.v.ground.GroundModel.fit(samples, self.cfg["ground"].get("size_tolerance", 2.0))
        except ValueError as error:
            raise CommandError(str(error)) from None
        model.stop_line_y = model.row_for_distance(self.cfg["navigation"]["arrive_distance_cm"])
        self.cfg["ground"] = model.to_dict()
        self._reload()
        self.message = f"Stop line set at {model.stop_line_y:.3f} of the image height."

    def _cmd_ground_reset(self, p):
        self.cfg["ground"] = self.v.ground.GroundModel().to_dict()
        self._reload()
        self.message = "Ground calibration removed."

    def _cmd_fov_capture(self, p):
        self._need_frame()
        role = p.get("role")
        if role not in ("center", "side"):
            raise CommandError("Role must be center or side.")
        if self._det is None or not self._det.detected:
            raise CommandError(f"No {self.target} block detected.")
        self.fov_captures[role] = round(self._det.horizontal_error, 4)
        self.message = f"Field-of-view {role} captured."

    def _cmd_fov_fit(self, p):
        dist, offset = p.get("distance_cm"), p.get("offset_cm")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in (dist, offset)):
            raise CommandError("Enter the distance and the side offset in cm.")
        if set(self.fov_captures) != {"center", "side"}:
            raise CommandError("Capture both the centered and the side position first.")
        delta = abs(self.fov_captures["side"] - self.fov_captures["center"])
        if delta < 0.05:
            raise CommandError("The two captures are almost the same; move the block further sideways.")
        hfov = 2 * math.degrees(math.atan2(offset, dist)) / delta
        self.cfg["navigation"]["camera_hfov_deg"] = round(hfov, 1)
        self._reload()
        self.message = f"Camera field of view: {hfov:.1f} degrees."

    def _cmd_steps(self, p):
        steps = {}
        for kind in ("forward", "turn"):
            table = p.get(kind) or {}
            if not isinstance(table, dict):
                raise CommandError("Steps must be tables of duration -> measurement.")
            clean = {}
            for ms, value in table.items():
                if str(ms) not in STEP_DURATIONS:
                    raise CommandError("Step durations must be 200, 500 or 1000 ms.")
                if value in (None, ""):
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value < 1000:
                    raise CommandError("Step measurements must be positive numbers.")
                clean[str(ms)] = float(value)
            steps[kind] = clean
        self.cfg["navigation"]["steps"] = steps
        self._reload()
        self.message = "Step table saved."

    def _cmd_tune(self, p):
        key, value = p.get("key"), p.get("value")
        if key not in TUNABLE:
            raise CommandError("That setting can't be changed from the browser.")
        kind, lo, hi = TUNABLE[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not lo <= value <= hi:
            raise CommandError(f"{key} must be between {lo} and {hi}.")
        if key == "black.v_max":
            black = self.cfg["colors"].setdefault("black", {"h": [0, 179], "s": [0, 255], "v": [0, 60]})
            black["v"] = [0, kind(value)]
            self._reload()
            self.message = f"Black flag: brightness up to {kind(value)}"
            return
        section, name = key.split(".")
        self.cfg[section][name] = kind(value)
        if key == "navigation.arrive_distance_cm" and len(self.cfg["ground"].get("points") or []) >= 2:
            model = self.v.ground.GroundModel.from_dict(self.cfg["ground"])
            self.cfg["ground"]["stop_line_y"] = model.row_for_distance(float(value))
        self._reload()
        self.message = f"{key} = {kind(value)}"

    def _cmd_camera(self, p):
        key, value = p.get("key"), p.get("value")
        if key not in CAMERA_CONTROLS:
            raise CommandError("Unknown camera setting.")
        lo, hi = CAMERA_CONTROLS[key]
        if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
            raise CommandError(f"{key} must be a whole number from {lo} to {hi}.")
        self.cfg["camera"].setdefault("controls", {})[key] = value
        self._reload()
        self.message = f"Camera {key} = {value}"

    def _box(self, p) -> list:
        box = p.get("box")
        if not (isinstance(box, list) and len(box) == 4 and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in box)):
            raise CommandError("Box must be [x0, y0, x1, y1] between 0 and 1.")
        x0, x1 = sorted(min(1.0, max(0.0, float(v))) for v in (box[0], box[2]))
        y0, y1 = sorted(min(1.0, max(0.0, float(v))) for v in (box[1], box[3]))
        if x1 - x0 < 0.005 or y1 - y0 < 0.005:
            raise CommandError("Drag a bigger box.")
        return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]

    def _cmd_mask_add(self, p):
        boxes = self.cfg.setdefault("rover_mask", {"boxes": []}).setdefault("boxes", [])
        if len(boxes) >= 12:
            raise CommandError("Up to 12 mask boxes; clear some first.")
        boxes.append(self._box(p))
        self._reload()
        self.message = f"Rover mask: {len(boxes)} box(es)."

    def _cmd_mask_undo(self, p):
        boxes = self.cfg.get("rover_mask", {}).get("boxes", [])
        if not boxes:
            raise CommandError("No mask boxes to undo.")
        boxes.pop()
        self._reload()
        self.message = f"Rover mask: {len(boxes)} box(es)."

    def _cmd_mask_clear(self, p):
        self.cfg["rover_mask"] = {"boxes": []}
        self._reload()
        self.message = "Rover mask cleared."

    def _cmd_snapshot(self, p):
        import cv2
        self._need_frame()
        folder = self.dir / "captures"
        folder.mkdir(exist_ok=True)
        stem = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + f"_{self.target}_mc"
        cv2.imwrite(str(folder / f"{stem}_raw.jpg"), self._image)
        record = {"detection": self._det.to_dict() if self._det else None, "vision": self.latest}
        (folder / f"{stem}_result.json").write_text(json.dumps(record, indent=2) + "\n")
        self.message = f"Saved software/vision/captures/{stem}_raw.jpg"

    # ------------------------------------------------------------ mask preview
    def mask_jpeg(self, kind: str) -> bytes | None:
        """JPEG of the current target mask or floor mask (white = matched), for calibration."""
        import cv2
        import numpy as np
        with self.lock:
            if self._image is None:
                return None
            if kind == "floor":
                mask = self.detector.class_mask(self._prepared, self.nav.floor_class)
                if mask is None:
                    hsv = cv2.cvtColor(self._image, cv2.COLOR_BGR2HSV)
                    f = self.nav.floor
                    mask = cv2.inRange(hsv, (0, 0, f.v_min), (179, f.s_max, 255))
            else:
                mask = self.detector.mask(self._image, self.target, prepared=self._prepared)
            image = self._image
            view = np.where(mask[..., None] > 0, image, (image * 0.15).astype(np.uint8))
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None
