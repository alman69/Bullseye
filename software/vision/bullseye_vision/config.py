"""Load and save the vision configuration (config.json).

Missing keys fall back to DEFAULTS, so a partially edited file still works.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

# The arena settings live next to the package (software/vision/config.json), shared by
# the standalone app and Mission Control.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS: dict = {
    "camera": {
        "stream_url": "http://192.168.4.1:81/stream",
        "control_url": "http://192.168.4.1/control",
        "timeout_s": 3.0,
        "reconnect_min_s": 0.5,
        "reconnect_max_s": 5.0,
        "rotate": 0,
        "controls": {},
    },
    "detector": {
        "min_area_px": 400,
        "max_area_ratio": 0.6,
        "min_extent": 0.3,
        "blur_kernel": 5,
        "morph_kernel": 5,
        "center_deadband": 0.12,
    },
    "floor": {
        "roi_top": 0.5,
        "strips": 5,
        "s_max": 45,
        "v_min": 90,
        "blocked_height": 0.35,
        "corridor_half_width": 0.45,
        "column_percentile": 25,
        "work_width": 160,
        "morph_kernel": 3,
        "target_dilate": 7,
        "min_floor_visible": 0.3,
    },
    "navigation": {
        "turn_pwm": 60,
        "drive_pwm": 60,
        "aim_deadband": 0.12,
        "turn_min_ms": 80,
        "turn_max_ms": 250,
        "forward_ms": 300,
        "slow_forward_ms": 150,
        "slow_area_ratio": 0.04,
        "arrive_area_ratio": 0.08,
        "arrive_distance_cm": 10,
        "emergency_distance_cm": 10,
        "distance_check_every": 3,
        "lost_decisions": 3,
        "search_turn_ms": 300,
        "search_turns": 8,
        "avoid_turn_ms": 300,
        "avoid_forward_steps": 2,
        "avoid_max_retries": 4,
        "backoff_ms": 300,
        "explore_steps": 3,
        "explore_timeout_s": 120,
        "explore_forward_ms": 1000,
        "base_distance_cm": 15,
        "far_forward_ms": 1000,
        "far_bottom_y": 0.55,
        "mid_forward_ms": 500,
        "mid_bottom_y": 0.7,
        "clear_floor_height": 0.6,
        "glimpse_hfov_deg": 50,
        "camera_latency_s": 0.15,
        "wall_distance_cm": 25,
        "decision_period_s": 0.5,
        "arrival_mode": "camera",
        "approach_zone_y": 0.08,
        "camera_hfov_deg": 0,
        "steps": {"forward": {}, "turn": {}},
    },
    "obstacles": {"colors": ["blue"]},
    "ground": {
        "points": [],
        "width_k": None,
        "horizon_y": None,
        "stop_line_y": None,
        "size_tolerance": 2.0,
    },
    "rover_mask": {"boxes": [], "polygons": []},
    "palette": {"profiles": {}},
    "classifier": {
        "mode": "palette",
        "model_path": "color_model.json",
        "samples_path": "captures/samples.npz",
        "max_distance": 4.0,
        "min_chroma": 12.0,
        "regularization": 4.0,
        "white_balance": True,
        "wb_alpha": 0.2,
        "wb_min_floor": 0.02,
        "wb_gain_min": 0.6,
        "wb_gain_max": 1.6,
        "floor_class": "floor",
    },
    "colors": {
        "red": {"h": [170, 10], "s": [120, 255], "v": [70, 255]},
        "yellow": {"h": [20, 35], "s": [100, 255], "v": [100, 255]},
        "green": {"h": [40, 85], "s": [80, 255], "v": [50, 255]},
        "blue": {"h": [95, 130], "s": [120, 255], "v": [50, 255]},
        "black": {"h": [0, 179], "s": [0, 140], "v": [0, 55]},
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key not in ("controls", "forward", "turn"):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class ConfigConflictError(ValueError):
    """Another process or checkout changed the calibration after this copy was loaded."""


class Config(dict):
    """Ordinary JSON-compatible dict with an out-of-band disk revision for safe saves."""

    def __init__(self, values, source_path, source_digest):
        super().__init__(values)
        self.source_path = source_path
        self.source_digest = source_digest


def _digest(raw):
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _read_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Load defaults plus saved values, remembering the exact revision that was read."""
    path = (Path(path) if path else DEFAULT_CONFIG_PATH).resolve()
    raw = _read_bytes(path)
    values = copy.deepcopy(DEFAULTS) if raw is None else _merge(DEFAULTS, json.loads(raw))
    return Config(values, path, _digest(raw))


def save_config(cfg: dict, path: str | os.PathLike | None = None) -> Path:
    """Atomically save; reject stale loaded copies rather than losing newer calibration.

    A separate advisory lock serializes cooperating writers across processes. The
    revision is metadata on Config, never a key in the saved JSON. Plain dicts may
    still be used to initialize/export configurations without a revision check.
    """
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    resolved = path.resolve()
    payload = (json.dumps(cfg, indent=2) + "\n").encode("utf-8")
    lock_path = resolved.with_name("." + resolved.name + ".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if isinstance(cfg, Config) and cfg.source_path == resolved:
            if _digest(_read_bytes(resolved)) != cfg.source_digest:
                raise ConfigConflictError(
                    "Calibration changed on disk in another server or checkout. "
                    "Restart this server to load the saved calibration, then retry; nothing was overwritten.")
        fd, tmp = tempfile.mkstemp(dir=str(resolved.parent), prefix=".config-", suffix=".json")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, resolved)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        if isinstance(cfg, Config):
            cfg.source_path, cfg.source_digest = resolved, _digest(payload)
    return path
