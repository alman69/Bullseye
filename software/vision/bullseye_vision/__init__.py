"""Bullseye laptop vision: color-target detection, floor free-space check and the
navigation planner, used by the standalone test app (software/vision/main.py) and by
Mission Control's vision service (mission-control/server/bullseye/vision.py).

Settings and the learned color model live next to this package in software/vision/
(config.json, color_model.json), so both users share one calibration.
"""
