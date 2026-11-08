"""Bullseye vision test app: live view, color selection, calibration, snapshots.

The OpenCV windows here are for testing and calibration only. Navigation and
Mission Control should use camera.CameraStream + detector.ColorDetector directly
(see README "Using the detector from other code"), or `--headless` JSON output.

Keys (GUI):  r/y/g/b    select target color      c  toggle calibration window
             w          save HSV values to config s  save snapshot (raw, annotated, JSON)
             n          next image (offline folder) q / Esc  quit
With --navigate (DRY RUN, never moves the rover):
             f          toggle the floor-mask window  x  reset the planner
Color sampling for the classifier (k toggles sampling mode):
             1-7        choose the class: 1 red 2 green 3 blue 4 yellow 5 floor 6 cardboard 7 obstacle
             drag       add the pixels in the box to that class   u  undo the last box
             m          fit the model, save color_model.json, print the confusion table
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

from bullseye_vision.camera import CameraStream, FileSource, RateMeter
from bullseye_vision.colorclass import SAMPLE_CLASSES, ColorModel, format_confusion, load_samples, save_samples
from bullseye_vision.config import DEFAULT_CONFIG_PATH, load_config, save_config
from bullseye_vision.detector import COLORS, ColorDetector, Detection, HsvRange
from bullseye_vision.pipeline import NavRunner
from bullseye_vision.planner import BACKWARD, FORWARD, TURN_LEFT, TURN_RIGHT

MAIN_WIN = "Bullseye Vision"
CAL_WIN = "Calibration (mask)"
FLOOR_WIN = "Floor mask (white = floor)"
CAPTURE_DIR = Path(__file__).with_name("captures")
BGR = {"red": (40, 40, 230), "yellow": (0, 220, 255), "green": (60, 200, 60), "blue": (230, 120, 40)}
COLOR_KEYS = {ord("r"): "red", ord("y"): "yellow", ord("g"): "green", ord("b"): "blue"}
SAMPLE_KEYS = {ord(str(i + 1)): name for i, name in enumerate(SAMPLE_CLASSES)}
SAMPLE_BGR = dict(BGR, floor=(235, 235, 235), cardboard=(90, 136, 176), obstacle=(0, 160, 255))
MAX_PIXELS_PER_BOX = 4000
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
    for key, line_color, label in (("horizon_y", (150, 150, 150), "horizon"),
                                   ("approach_zone_y", (200, 120, 255), "approach zone"),
                                   ("stop_line_y", (255, 0, 255), "STOP LINE")):
        if info.get(key) is not None and 0 < info[key] < 1:
            y_line = int(info[key] * h)
            cv2.line(img, (0, y_line), (w, y_line), line_color, 2 if key == "stop_line_y" else 1)
            put_text(img, label, (w - 150, y_line - 6), 0.45, line_color)
    if det.detected:
        dist = f"   ~{det.distance_cm_est:.0f} cm" if det.distance_cm_est is not None else ""
        put_text(img, f"{det.direction}   err {det.horizontal_error:+.2f}   "
                      f"area {det.area:.0f}px ({det.area_ratio * 100:.2f}%){dist}   x={det.center_x} y={det.center_y}",
                 (10, 50))
    else:
        put_text(img, "NONE", (10, 50))
    put_text(img, f"stream {info['stream_fps']:.1f} fps | processing {info['proc_fps']:.1f} fps | "
                  f"detect {det.processing_ms:.1f} ms | age {info['age_ms']:.0f} ms", (10, 74), 0.5)
    wb = info.get("wb")
    wb_text = f"   WB gains {wb[0]:.2f}/{wb[1]:.2f}/{wb[2]:.2f}" if wb is not None else ""
    put_text(img, f"candidates {det.candidates}   detector: {info.get('mode', 'hsv')}{wb_text}   "
                  f"source: {info['source']}", (10, 96), 0.5)

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


# ------------------------------------------------------------ navigation dry run
ARROWS = {FORWARD: (0, -1), BACKWARD: (0, 1), TURN_LEFT: (-1, 0), TURN_RIGHT: (1, 0)}


def draw_nav_overlay(img: np.ndarray, nav: NavRunner) -> np.ndarray:
    h, w = img.shape[:2]
    free, action = nav.free, nav.action
    if free is not None:
        top = free.roi_top_px
        roi_h = h - top
        edges = np.linspace(0, w, len(free.heights) + 1).astype(int)
        shade = img.copy()
        for i, height in enumerate(free.heights):
            y = int(h - height * roi_h)
            color = (60, 60, 230) if free.blocked[i] else (80, 200, 80)
            cv2.rectangle(shade, (edges[i] + 2, y), (edges[i + 1] - 2, h), color, -1)
        cv2.addWeighted(shade, 0.3, img, 0.7, 0, dst=img)
        for i in free.corridor:
            cv2.rectangle(img, (edges[i], top), (edges[i + 1] - 1, h - 1), (255, 255, 255), 1)
        cv2.line(img, (0, top), (w, top), (200, 200, 200), 1)
        y_thr = int(h - nav.floor.blocked_height * roi_h)
        cv2.line(img, (0, y_thr), (w, y_thr), (60, 60, 230), 1, cv2.LINE_4)
        if not free.floor_visible:
            put_text(img, "NO FLOOR VISIBLE at the bottom edge: check camera view / floor s_max",
                     (10, top - 10), 0.5, (60, 60, 255))
    state = nav.planner.state
    put_text(img, f"DRY RUN (no motors)  state: {state}", (10, 124), 0.6, (0, 255, 255), 2)
    if action is not None:
        put_text(img, f"NEXT: {action.label()}", (10, 152), 0.75, (0, 255, 255), 2)
        put_text(img, action.reason[:95], (10, 176), 0.5, (230, 230, 230))
        if action.kind in ARROWS:
            dx, dy = ARROWS[action.kind]
            cx, cy = w // 2, h - 70
            cv2.arrowedLine(img, (cx, cy), (cx + dx * 70, cy + dy * 50), (0, 255, 255), 5, tipLength=0.4)
    put_text(img, "f floor mask   x reset planner", (10, 198), 0.45, (220, 220, 220))
    return img


def floor_view(nav: NavRunner, frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    mask = nav.free.mask
    top = nav.free.roi_top_px
    view = cv2.cvtColor(cv2.resize(mask, (w, h - top), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
    put_text(view, "white = floor (classifier 'floor' class if sampled, else HSV floor.s_max / v_min)",
             (10, 24), 0.5, (0, 200, 255))
    return view


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
        "detector_mode": detector.mode,
    }
    if info.get("nav"):
        record["navigation"] = info["nav"]
    (CAPTURE_DIR / f"{stem}_result.json").write_text(json.dumps(record, indent=2) + "\n")
    return CAPTURE_DIR / stem


# ------------------------------------------------------------------ loops
def draw_sampling(img: np.ndarray, sampling: dict, samples: dict) -> None:
    h, w = img.shape[:2]
    cls = sampling["cls"]
    color = SAMPLE_BGR.get(cls, (255, 255, 255))
    if sampling["start"] is not None and sampling["end"] is not None:
        cv2.rectangle(img, sampling["start"], sampling["end"], color, 2)
    counts = "  ".join(f"{name}:{len(samples.get(name, ()))}" for name in SAMPLE_CLASSES)
    put_text(img, f"SAMPLING -> {cls.upper()}   (1 red 2 green 3 blue 4 yellow 5 floor 6 cardboard 7 obstacle)",
             (10, h - 82), 0.5, color, 2)
    put_text(img, "drag a box on it   u undo   m fit + save model   k done", (10, h - 60), 0.5)
    put_text(img, counts, (10, h - 38), 0.45, (220, 220, 220))


def make_source(args, cfg):
    if args.source:
        return FileSource(args.source).start()
    return CameraStream.from_config(cfg["camera"], url=args.url).start()


def run_gui(source, detector: ColorDetector, cfg: dict, args) -> None:
    color = args.color
    nav = NavRunner(cfg) if args.navigate else None
    floor_open = False
    proc_rate = RateMeter()
    state = {"probe_xy": None}
    last_frame = None
    last_det = None
    lost_since = time.monotonic()
    calibrating = False
    last_prepared = None
    cc = cfg.get("classifier", {})
    base = Path(args.config).parent
    samples_path = base / cc.get("samples_path", "captures/samples.npz")
    model_path = base / cc.get("model_path", "color_model.json")
    samples = load_samples(samples_path)
    undo = []
    sampling = {"on": False, "cls": "red", "start": None, "end": None, "commit": None}

    def on_mouse(event, x, y, _flags, _param):
        if sampling["on"]:
            if event == cv2.EVENT_LBUTTONDOWN:
                sampling["start"] = sampling["end"] = (x, y)
            elif event == cv2.EVENT_MOUSEMOVE and sampling["start"] is not None:
                sampling["end"] = (x, y)
            elif event == cv2.EVENT_LBUTTONUP and sampling["start"] is not None:
                sampling["commit"] = (sampling["start"], (x, y))
                sampling["start"] = sampling["end"] = None
        elif event == cv2.EVENT_LBUTTONDOWN:
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
            last_prepared = detector.prepare(frame.image)
            last_det = detector.detect(frame.image, color, frame.frame_id, frame.received_wall, prepared=last_prepared)
            if nav is not None:
                nav.update(frame, last_det, detector, prepared=last_prepared)
            proc_rate.tick()
            now = time.monotonic()
            if last_det.detected:
                lost_since = None
            elif lost_since is None:
                lost_since = now

        if sampling["commit"] is not None and last_frame is not None:
            (x0, y0), (x1, y1) = sampling["commit"]
            sampling["commit"] = None
            x0, x1 = sorted((max(0, x0), max(0, x1)))
            y0, y1 = sorted((max(0, y0), max(0, y1)))
            if x1 - x0 >= 3 and y1 - y0 >= 3:
                # Sample what the classifier will see: blurred (and white-balanced once a model exists).
                k = detector.settings.blur_kernel
                src = last_prepared.corrected if last_prepared is not None else (
                    cv2.GaussianBlur(last_frame.image, (k, k), 0) if k and k > 1 else last_frame.image)
                px = src[y0:y1, x0:x1].reshape(-1, 3)
                if len(px) > MAX_PIXELS_PER_BOX:
                    px = px[np.random.default_rng().choice(len(px), MAX_PIXELS_PER_BOX, replace=False)]
                name = sampling["cls"]
                samples[name] = np.concatenate([samples[name], px]) if name in samples else px.copy()
                undo.append((name, len(px)))
                save_samples(samples, samples_path)
                print(f"sampled {len(px)} px of {name}; totals: " + ", ".join(f"{k}={len(v)}" for k, v in samples.items()))

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
                "nav": nav.to_dict() if nav is not None else None,
                "mode": detector.mode,
                "wb": None if detector.wb is None else detector.wb.gains,
                "stop_line_y": cfg["ground"].get("stop_line_y"),
                "approach_zone_y": (cfg["ground"]["stop_line_y"] - cfg["navigation"]["approach_zone_y"]
                                    if cfg["ground"].get("stop_line_y") is not None else None),
                "horizon_y": cfg["ground"].get("horizon_y"),
            }
            annotated = draw_overlay(last_frame.image, last_det, info, detector.settings.center_deadband)
            if nav is not None:
                draw_nav_overlay(annotated, nav)
                if floor_open:
                    if window_closed(FLOOR_WIN):
                        floor_open = False
                    elif nav.free is not None:
                        cv2.imshow(FLOOR_WIN, floor_view(nav, last_frame.image))
            if sampling["on"]:
                draw_sampling(annotated, sampling, samples)
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
        if sampling["on"] and key in SAMPLE_KEYS:
            sampling["cls"] = SAMPLE_KEYS[key]
            print(f"sampling class: {sampling['cls']}")
        elif sampling["on"] and key == ord("u"):
            if undo:
                name, n = undo.pop()
                samples[name] = samples[name][:-n]
                if not len(samples[name]):
                    del samples[name]
                save_samples(samples, samples_path)
                print(f"removed the last {n} px of {name}")
        elif sampling["on"] and key == ord("m"):
            try:
                model = ColorModel.fit(samples, cc.get("max_distance", 4.0), cc.get("regularization", 4.0),
                                       cc.get("floor_class", "floor"))
            except ValueError as error:
                print(f"cannot fit: {error}")
            else:
                model.save(model_path)
                detector.set_model(model, cc)
                print(f"saved {model_path} (classes: {', '.join(model.classes)})")
                print(format_confusion(model.confusion(samples)))
                print("Good: >= 95% on the diagonal, < 2% between minerals and floor/cardboard/obstacle.")
        elif key == ord("k"):
            sampling["on"] = not sampling["on"]
            sampling["start"] = sampling["end"] = None
            print("sampling mode " + ("ON: choose a class with 1-7, drag boxes, m to fit" if sampling["on"] else "off"))
        elif key in COLOR_KEYS:
            color = COLOR_KEYS[key]
            lost_since = time.monotonic()
            if last_frame is not None:  # re-run so the overlay matches the new color immediately
                last_det = detector.detect(last_frame.image, color, last_frame.frame_id, last_frame.received_wall,
                                           prepared=last_prepared)
            if calibrating:
                set_trackbars(detector.ranges[color])
            if nav is not None:
                nav.reset()
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
        elif key == ord("x") and nav is not None:
            nav.reset()
            print("planner reset")
        elif key == ord("f") and nav is not None:
            if floor_open:
                cv2.destroyWindow(FLOOR_WIN)
            floor_open = not floor_open
    cv2.destroyAllWindows()
    cv2.waitKey(1)


def run_headless(source, detector: ColorDetector, args, cfg: dict) -> None:
    """Print one JSON line per processed frame: {"detection": {...}, "metrics": {...}}
    (plus "navigation": {...} with --navigate)."""
    proc_rate = RateMeter()
    nav = NavRunner(cfg) if args.navigate else None
    deadline = time.monotonic() + args.duration if args.duration else None
    last_id = -1
    while deadline is None or time.monotonic() < deadline:
        frame = source.wait_for_frame(last_id, timeout=0.5)
        stats = source.stats()
        if frame is None:
            print(json.dumps({"detection": None, "metrics": stats}), flush=True)
            continue
        last_id = frame.frame_id
        prepared = detector.prepare(frame.image)
        det = detector.detect(frame.image, args.color, frame.frame_id, frame.received_wall, prepared=prepared)
        proc_rate.tick()
        metrics = dict(stats, proc_fps=round(proc_rate.rate(), 2),
                       age_ms=round((time.monotonic() - frame.received_at) * 1000.0, 1))
        record = {"detection": det.to_dict(), "metrics": metrics}
        if nav is not None:
            nav.update(frame, det, detector, prepared=prepared)
            record["navigation"] = nav.to_dict()
        print(json.dumps(record), flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Bullseye color-target vision (ESP32 camera)")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="config JSON path")
    p.add_argument("--url", help="override camera.stream_url")
    p.add_argument("--source", help="offline image, image folder or video instead of the camera")
    p.add_argument("--color", default="red", choices=COLORS, help="initial target color")
    p.add_argument("--calibrate", action="store_true", help="open the calibration window at start")
    p.add_argument("--headless", action="store_true", help="no windows; print JSON lines to stdout")
    p.add_argument("--duration", type=float, default=0, help="headless: stop after N seconds")
    p.add_argument("--navigate", action="store_true",
                   help="DRY RUN: show floor check and planned moves; never sends motor commands")
    p.add_argument("--detector", choices=("auto", "classifier", "hsv"),
                   help="override classifier.mode (auto = classifier when color_model.json exists)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    detector = ColorDetector.from_config(cfg, base_dir=Path(args.config).parent, mode=args.detector)
    print(f"detector: {detector.mode}" + ("" if detector.model is None else f" ({', '.join(detector.model.classes)})")
          + ("" if detector.ground is None else "; ground calibrated"), file=sys.stderr)
    source = make_source(args, cfg)
    try:
        if args.headless:
            run_headless(source, detector, args, cfg)
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
