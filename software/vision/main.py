"""Bullseye vision test app: live view, color selection, calibration, snapshots.

The OpenCV windows here are for testing and calibration only. Navigation and
Mission Control should use camera.CameraStream + detector.ColorDetector directly
(see README "Using the detector from other code"), or `--headless` JSON output.

Keys (GUI):  r/y/g/b    select target color      c  toggle calibration window
             w          save HSV values to config s  save snapshot (raw, annotated, JSON)
             n          next image (offline folder) q / Esc  quit
             left-click the video to read the HSV value under the cursor (p clears it)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from camera import CameraStream, FileSource, RateMeter
from config import DEFAULT_CONFIG_PATH, load_config, save_config
from detector import COLORS, ColorDetector, Detection, HsvRange

MAIN_WIN = "Bullseye Vision"
CAL_WIN = "Calibration (mask)"
CAPTURE_DIR = Path(__file__).with_name("captures")
BGR = {"red": (40, 40, 230), "yellow": (0, 220, 255), "green": (60, 200, 60), "blue": (230, 120, 40)}
COLOR_KEYS = {ord("r"): "red", ord("y"): "yellow", ord("g"): "green", ord("b"): "blue"}
TRACKBARS = (("H low", 179), ("H high", 179), ("S low", 255), ("S high", 255), ("V low", 255), ("V high", 255))


# ---------------------------------------------------------------- drawing
def put_text(img, text, org, scale=0.55, color=(255, 255, 255), thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw_overlay(frame: np.ndarray, det: Detection, info: dict, deadband: float) -> np.ndarray:
    img = frame.copy()
    h, w = img.shape[:2]
    cx0, cy0 = w // 2, h // 2
    color = BGR.get(det.target_color, (255, 255, 255))

    # Frame center line and CENTER deadband.
    cv2.line(img, (cx0, 0), (cx0, h), (255, 255, 255), 1)
    band = int(deadband * w / 2)
    for x in (cx0 - band, cx0 + band):
        cv2.line(img, (x, 0), (x, h), (160, 160, 160), 1, cv2.LINE_4)
    cv2.drawMarker(img, (cx0, cy0), (255, 255, 255), cv2.MARKER_CROSS, 18, 1)

    # Other passing regions (thin gray), selected target (thick, target color).
    for cand in det.all_candidates[1:]:
        x, y, bw, bh = cand.bbox
        cv2.rectangle(img, (x, y), (x + bw, y + bh), (180, 180, 180), 1)
    if det.detected:
        x, y, bw, bh = det.bbox
        cv2.rectangle(img, (x, y), (x + bw, y + bh), color, 3)
        cv2.circle(img, (det.center_x, det.center_y), 6, (255, 255, 255), -1)
        cv2.circle(img, (det.center_x, det.center_y), 6, color, 2)
        cv2.line(img, (cx0, det.center_y), (det.center_x, det.center_y), (0, 255, 255), 2)

    # Text panel.
    status = "DETECTED" if det.detected else f"LOST {info['lost_for']:.1f}s" if info["lost_for"] is not None else "LOST"
    status_color = (80, 255, 80) if det.detected else (60, 60, 255)
    put_text(img, f"TARGET: {det.target_color.upper()}", (10, 24), 0.7, color, 2)
    put_text(img, status, (230, 24), 0.7, status_color, 2)
    if det.detected:
        put_text(img, f"{det.direction}   err {det.horizontal_error:+.2f}   "
                      f"area {det.area:.0f}px ({det.area_ratio * 100:.2f}%)   x={det.center_x} y={det.center_y}",
                 (10, 50))
    else:
        put_text(img, "NONE", (10, 50))
    put_text(img, f"stream {info['stream_fps']:.1f} fps | processing {info['proc_fps']:.1f} fps | "
                  f"detect {det.processing_ms:.1f} ms | age {info['age_ms']:.0f} ms", (10, 74), 0.5)
    put_text(img, f"candidates {det.candidates}   {w}x{h}   source: {info['source']}", (10, 96), 0.5)

    if info["state"] not in ("streaming", "file"):
        put_text(img, f"CAMERA {info['state'].upper()}", (10, h // 2 - 20), 1.0, (60, 60, 255), 2)
        if info.get("error"):
            put_text(img, info["error"][:90], (10, h // 2 + 12), 0.5, (60, 60, 255))

    if info.get("probe"):
        (px, py), (ph, ps, pv) = info["probe"]
        cv2.drawMarker(img, (px, py), (0, 255, 255), cv2.MARKER_TILTED_CROSS, 14, 2)
        put_text(img, f"HSV at ({px},{py}) = {ph},{ps},{pv}", (10, h - 34), 0.5, (0, 255, 255))
    put_text(img, "r/y/g/b color  c calibrate  w save HSV  s snapshot  q quit", (10, h - 10), 0.45, (220, 220, 220))
    return img


def waiting_image(text: str) -> np.ndarray:
    img = np.zeros((480, 640, 3), np.uint8)
    put_text(img, text[:70], (20, 240), 0.6, (60, 60, 255))
    put_text(img, "q to quit", (20, 270), 0.5)
    return img


# ------------------------------------------------------------ calibration
def open_calibration(rng: HsvRange) -> None:
    cv2.namedWindow(CAL_WIN, cv2.WINDOW_AUTOSIZE)
    values = (rng.h[0], rng.h[1], rng.s[0], rng.s[1], rng.v[0], rng.v[1])
    for (name, maxval), val in zip(TRACKBARS, values):
        cv2.createTrackbar(name, CAL_WIN, int(val), maxval, lambda _v: None)


def set_trackbars(rng: HsvRange) -> None:
    values = (rng.h[0], rng.h[1], rng.s[0], rng.s[1], rng.v[0], rng.v[1])
    for (name, _), val in zip(TRACKBARS, values):
        cv2.setTrackbarPos(name, CAL_WIN, int(val))


def read_trackbars() -> HsvRange:
    v = [cv2.getTrackbarPos(name, CAL_WIN) for name, _ in TRACKBARS]
    return HsvRange((v[0], v[1]), (min(v[2], v[3]), max(v[2], v[3])), (min(v[4], v[5]), max(v[4], v[5])))


def window_closed(name: str) -> bool:
    try:
        return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


# ------------------------------------------------------------- snapshots
def save_snapshot(frame, annotated, det: Detection, info: dict, detector: ColorDetector) -> Path:
    CAPTURE_DIR.mkdir(exist_ok=True)
    stem = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + f"_{det.target_color}"
    cv2.imwrite(str(CAPTURE_DIR / f"{stem}_raw.jpg"), frame)
    cv2.imwrite(str(CAPTURE_DIR / f"{stem}_annotated.jpg"), annotated)
    record = {
        "detection": det.to_dict(),
        "metrics": {k: info[k] for k in ("stream_fps", "proc_fps", "age_ms", "state", "lost_for")},
        "hsv_range": detector.ranges[det.target_color].to_dict(),
        "detector_settings": vars(detector.settings),
    }
    (CAPTURE_DIR / f"{stem}_result.json").write_text(json.dumps(record, indent=2) + "\n")
    return CAPTURE_DIR / stem


# ------------------------------------------------------------------ loops
def make_source(args, cfg):
    if args.source:
        return FileSource(args.source).start()
    return CameraStream.from_config(cfg["camera"], url=args.url).start()


def run_gui(source, detector: ColorDetector, cfg: dict, args) -> None:
    color = args.color
    proc_rate = RateMeter()
    state = {"probe_xy": None}
    last_frame = None
    last_det = None
    lost_since = time.monotonic()
    calibrating = False

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            state["probe_xy"] = (x, y)

    cv2.namedWindow(MAIN_WIN, cv2.WINDOW_AUTOSIZE)   # autosize: mouse coords == image coords
    cv2.setMouseCallback(MAIN_WIN, on_mouse)
    if args.calibrate:
        open_calibration(detector.ranges[color])
        calibrating = True

    last_id = -1
    while True:
        if calibrating:
            if window_closed(CAL_WIN):
                calibrating = False
            else:
                rng = read_trackbars()
                if rng != detector.ranges[color]:
                    detector.set_range(color, rng)

        frame = source.wait_for_frame(last_id, timeout=0.03)
        if frame is not None:
            last_id = frame.frame_id
            last_frame = frame
            last_det = detector.detect(frame.image, color, frame.frame_id, frame.received_wall)
            proc_rate.tick()
            now = time.monotonic()
            if last_det.detected:
                lost_since = None
            elif lost_since is None:
                lost_since = now

        stats = source.stats()
        if last_frame is None:
            msg = f"Waiting for camera ({stats['state']})... {stats.get('last_error', '')}"
            cv2.imshow(MAIN_WIN, waiting_image(msg))
        else:
            probe = None
            if state["probe_xy"]:
                px, py = state["probe_xy"]
                img = last_frame.image
                if 0 <= px < img.shape[1] and 0 <= py < img.shape[0]:
                    hsv = cv2.cvtColor(img[py:py + 1, px:px + 1], cv2.COLOR_BGR2HSV)[0, 0]
                    probe = ((px, py), tuple(int(v) for v in hsv))
            info = {
                "stream_fps": source.stream_fps(),
                "proc_fps": proc_rate.rate(),
                "age_ms": (time.monotonic() - last_frame.received_at) * 1000.0,
                "state": stats["state"],
                "error": stats.get("last_error", ""),
                "lost_for": None if lost_since is None else time.monotonic() - lost_since,
                "source": getattr(source, "current_name", args.url or cfg["camera"]["stream_url"]),
                "probe": probe,
            }
            annotated = draw_overlay(last_frame.image, last_det, info, detector.settings.center_deadband)
            cv2.imshow(MAIN_WIN, annotated)
            if calibrating:
                mask = detector.mask(last_frame.image, color)
                view = cv2.bitwise_and(last_frame.image, last_frame.image, mask=mask)
                put_text(view, f"{color.upper()} {detector.ranges[color].to_dict()}", (10, 24), 0.55, BGR[color])
                put_text(view, "H low > H high wraps around (red). w = save", (10, 48), 0.5)
                cv2.imshow(CAL_WIN, view)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27) or window_closed(MAIN_WIN):
            break
        if key in COLOR_KEYS:
            color = COLOR_KEYS[key]
            lost_since = time.monotonic()
            if last_frame is not None:  # re-run so the overlay matches the new color immediately
                last_det = detector.detect(last_frame.image, color, last_frame.frame_id, last_frame.received_wall)
            if calibrating:
                set_trackbars(detector.ranges[color])
            print(f"target color: {color}")
        elif key == ord("c"):
            if calibrating:
                cv2.destroyWindow(CAL_WIN)
                calibrating = False
            else:
                open_calibration(detector.ranges[color])
                calibrating = True
        elif key == ord("w"):
            cfg["colors"] = {name: rng.to_dict() for name, rng in detector.ranges.items()}
            path = save_config(cfg, args.config)
            print(f"saved HSV ranges to {path}: {json.dumps(cfg['colors'])}")
        elif key == ord("s") and last_frame is not None:
            stem = save_snapshot(last_frame.image, annotated, last_det, info, detector)
            print(f"saved snapshot {stem}_*.jpg/json")
        elif key == ord("n") and hasattr(source, "next_image"):
            source.next_image()
        elif key == ord("p"):
            state["probe_xy"] = None
    cv2.destroyAllWindows()
    cv2.waitKey(1)


def run_headless(source, detector: ColorDetector, args) -> None:
    """Print one JSON line per processed frame: {"detection": {...}, "metrics": {...}}."""
    proc_rate = RateMeter()
    deadline = time.monotonic() + args.duration if args.duration else None
    last_id = -1
    while deadline is None or time.monotonic() < deadline:
        frame = source.wait_for_frame(last_id, timeout=0.5)
        stats = source.stats()
        if frame is None:
            print(json.dumps({"detection": None, "metrics": stats}), flush=True)
            continue
        last_id = frame.frame_id
        det = detector.detect(frame.image, args.color, frame.frame_id, frame.received_wall)
        proc_rate.tick()
        metrics = dict(stats, proc_fps=round(proc_rate.rate(), 2),
                       age_ms=round((time.monotonic() - frame.received_at) * 1000.0, 1))
        print(json.dumps({"detection": det.to_dict(), "metrics": metrics}), flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bullseye color-target vision (ESP32 camera)")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="config JSON path")
    p.add_argument("--url", help="override camera.stream_url")
    p.add_argument("--source", help="offline image, image folder or video instead of the camera")
    p.add_argument("--color", default="red", choices=COLORS, help="initial target color")
    p.add_argument("--calibrate", action="store_true", help="open the calibration window at start")
    p.add_argument("--headless", action="store_true", help="no windows; print JSON lines to stdout")
    p.add_argument("--duration", type=float, default=0, help="headless: stop after N seconds")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    detector = ColorDetector.from_config(cfg)
    source = make_source(args, cfg)
    try:
        if args.headless:
            run_headless(source, detector, args)
        else:
            run_gui(source, detector, cfg, args)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            source.stop()
            if not args.headless:
                cv2.destroyAllWindows()
                cv2.waitKey(1)
        except KeyboardInterrupt:  # second Ctrl+C while shutting down
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
