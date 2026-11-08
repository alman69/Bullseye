# Bullseye

A project for UB Hackathon 2026.

Camera-guided sample missions with a laptop-hosted Mission Control dashboard.

- [Simple arena vision: click-to-calibrate colors and trace the rover mask](docs/SIMPLE_VISION.md)
- [Mission Control setup, simulator, camera relay, and integration API](docs/MISSION_CONTROL.md)
- [Firmware setup and known hardware behavior](docs/FIRMWARE_SETUP.md)
- [Direct ELEGOO connection: no-app, no-motion bench test](docs/ELEGOO_CONNECTION.md)
- [Local real-rover control demo](docs/ELEGOO_CONNECTION.md#local-browser-control-demo)
- [Separate ESP32-S3 BLE distance display and Mac sender](docs/BLE_DISPLAY.md)
- [Autonomous approach, arrival checks, and the exploration roadmap](docs/AUTONOMY_PLAN.md)
- [Integration PR description](docs/INTEGRATION_PR.md)
- Historical servo experiments: [combined companion](firmware/esp32-companion/README.md) and [standalone scooper](firmware/esp32-scooper/README.md). Servo squeezing is retired; these guides are archives, not the current setup.
- [Vendor source references and attribution](docs/SOURCE_REFERENCES.md)

After setup, run `./mission-control/run.sh` and open http://localhost:8000.
The dashboard defaults to simulation. Existing vendor firmware remains untouched.

The current setup keeps click-to-calibrate colors and traced rover masks. Each accepted example
is saved immediately in `software/vision/config.json` and loaded again after a server or device
restart. Use the same checkout or `BULLSEYE_VISION_DIR` to keep using that calibration.

On `/bench`, a red or green ore mission stops at the ore, signals the sample with green rover LEDs
and a happy animation on the connected BLE display, then automatically searches for the black home flag and returns.
Blue is an obstacle; yellow remains available for calibration. This demo marks a sample by reaching
the ore and showing feedback; it does not physically grasp it. Motion remains experimental and
uses timed steps with obstacle checks.
