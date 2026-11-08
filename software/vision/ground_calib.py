"""Camera geometry calibration from snapshots (no motors).

Take each snapshot with the app (`s` key), with the block's color selected so
the block is DETECTED, centered in front of the rover. Distances are measured
on the floor from the rover's front bumper to the front of the block.

  Stop line + distance mapping (block straight ahead at several distances):
    .venv/bin/python ground_calib.py stopline --distances 10,15,20,30 \\
        captures/A_red_result.json captures/B_red_result.json captures/C_... captures/D_...

  Camera field of view (block at DISTANCE cm: once centered, once OFFSET cm to the side):
    .venv/bin/python ground_calib.py fov --distance-cm 30 --offset-cm 10 CENTER_result.json SIDE_result.json

Both write their results into config.json (or --config FILE).
"""
from __future__ import annotations

import argparse
import json
import math
import sys

from config import DEFAULT_CONFIG_PATH, load_config, save_config
from ground import GroundModel


def read_detection(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        record = json.load(f)
    det = record.get("detection") or {}
    if not det.get("detected") or not det.get("bbox"):
        raise ValueError(f"{path}: no target detected in this snapshot; select the block's color and retake it")
    return det


def stopline(args) -> int:
    if len(args.files) != len(args.distances):
        print("error: give one snapshot per distance, in the same order", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    samples = []
    for path, dist in zip(args.files, args.distances):
        det = read_detection(path)
        x, y, w, h = det["bbox"]
        bottom = (y + h) / det["frame_height"]
        width = w / det["frame_width"]
        samples.append((bottom, float(dist), width))
        print(f"{dist:6.1f} cm  bottom_y {bottom:.3f}  width {width:.3f}  error {det['horizontal_error']:+.2f}  ({path})")
    model = GroundModel.fit(samples, cfg["ground"].get("size_tolerance", 2.0))
    arrive = cfg["navigation"]["arrive_distance_cm"]
    model.stop_line_y = model.row_for_distance(arrive)
    if model.stop_line_y is None or not 0 < model.stop_line_y < 1.05:
        print(f"warning: stop line for {arrive} cm falls outside the image; calibrate closer distances", file=sys.stderr)
    cfg["ground"] = model.to_dict()
    path = save_config(cfg, args.config)
    print(f"horizon_y {model.horizon_y}, stop_line_y {model.stop_line_y} for {arrive} cm, width_k {model.width_k}")
    print(f"saved ground calibration to {path}")
    return 0


def fov(args) -> int:
    center = read_detection(args.center)
    side = read_detection(args.side)
    delta = abs(side["horizontal_error"] - center["horizontal_error"])
    if delta < 0.05:
        print("error: the two snapshots show almost the same position; move the block further sideways", file=sys.stderr)
        return 2
    angle = math.degrees(math.atan2(args.offset_cm, args.distance_cm))
    hfov = 2.0 * angle / delta
    cfg = load_config(args.config)
    cfg["navigation"]["camera_hfov_deg"] = round(hfov, 1)
    path = save_config(cfg, args.config)
    print(f"offset {angle:.1f} deg moved the target by {delta:.3f} of half the frame -> camera_hfov_deg {hfov:.1f}")
    print(f"saved to {path}")
    return 0


def parse_distances(text: str) -> list:
    try:
        values = [float(v) for v in text.replace(" ", "").split(",") if v]
    except ValueError:
        raise argparse.ArgumentTypeError("use comma-separated numbers, e.g. 10,15,20,30") from None
    if len(values) < 2 or any(v <= 0 for v in values):
        raise argparse.ArgumentTypeError("give at least two positive distances")
    return values


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stopline", help="distance mapping and stop line from blocks at known distances")
    s.add_argument("--distances", type=parse_distances, required=True,
                   help="comma-separated, one per snapshot, same order (e.g. 10,15,20,30)")
    s.add_argument("files", nargs="+")
    f = sub.add_parser("fov", help="horizontal field of view from a centered and a side-offset block")
    f.add_argument("--distance-cm", type=float, required=True)
    f.add_argument("--offset-cm", type=float, required=True)
    f.add_argument("center")
    f.add_argument("side")
    args = p.parse_args(argv)
    try:
        return stopline(args) if args.cmd == "stopline" else fov(args)
    except (OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
