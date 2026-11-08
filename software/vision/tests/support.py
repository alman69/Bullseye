"""Test helpers: tests must not depend on the arena calibration saved in software/vision
(config.json, color_model.json), which changes during calibration sessions."""
import copy

from bullseye_vision.config import DEFAULTS


def clean_config() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["classifier"]["mode"] = "hsv"   # never pick up a local color_model.json
    return cfg
