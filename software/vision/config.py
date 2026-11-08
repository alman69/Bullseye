"""Load and save the vision configuration (config.json).

Missing keys fall back to DEFAULTS, so a partially edited file still works.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")

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
        "s_max": 50,
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
        "arrive_distance_cm": 20,
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
        "decision_period_s": 0.5,
    },
    "colors": {
        "red": {"h": [170, 10], "s": [120, 255], "v": [70, 255]},
        "yellow": {"h": [20, 35], "s": [100, 255], "v": [100, 255]},
        "green": {"h": [40, 85], "s": [80, 255], "v": [50, 255]},
        "blue": {"h": [95, 130], "s": [120, 255], "v": [50, 255]},
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key != "controls":
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Return DEFAULTS overlaid with the JSON file (if it exists)."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    with open(path, "r", encoding="utf-8") as f:
        return _merge(DEFAULTS, json.load(f))


def save_config(cfg: dict, path: str | os.PathLike | None = None) -> Path:
    """Write the config atomically (temp file + rename) so a crash cannot truncate it."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path
