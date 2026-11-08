# Bullseye

A project for UB Hackathon 2026.

Camera-guided sample missions with a laptop-hosted Mission Control dashboard.

- [Simple arena vision: click-to-calibrate colors and trace the rover mask](docs/SIMPLE_VISION.md)
- [Mission Control setup, simulator, camera relay, and integration API](docs/MISSION_CONTROL.md)
- [Firmware setup and known hardware behavior](docs/FIRMWARE_SETUP.md)
- [Direct ELEGOO connection: no-app, no-motion bench test](docs/ELEGOO_CONNECTION.md)
- [Local real-rover control demo](docs/ELEGOO_CONNECTION.md#local-browser-control-demo)
- [Separate ESP32-S3 BLE distance display and Mac sender](docs/BLE_DISPLAY.md)
- [360° search, ultrasonic approach, and manual pickup](docs/AUTONOMY_PLAN.md)
- [Integration PR description](docs/INTEGRATION_PR.md)
- Manual squeezer control: [combined companion](firmware/esp32-companion/README.md) and [standalone scooper](firmware/esp32-scooper/README.md). Connect, Squeeze, Release, and Straight down are available on `/bench`; STOP stops the squeezer too. Automatic ore missions continue to use LED/display signalling.
- [Vendor source references and attribution](docs/SOURCE_REFERENCES.md)

After setup, run `./mission-control/run.sh` and open http://localhost:8000.
The dashboard defaults to simulation. Existing vendor firmware remains untouched.

The current setup keeps click-to-calibrate colors and traced rover masks. Each accepted example
is saved immediately in `software/vision/config.json` and loaded again after a server or device
restart. Use the same checkout or `BULLSEYE_VISION_DIR` to keep using that calibration.

On `/bench` the rover connects (and reconnects) by itself and the controls stay on; STOP is always
available. Choose the ruby (red) or emerald (green) and start a search. The rover scans 360°; if the
sample is absent it explores toward the most open direction the ultrasonic sensor finds, checking
left and right at walls (no second 360). Once found it approaches to about 10 cm and stops. You then drive up, squeeze the gripper,
drive the sample home and press Delivered. See [docs/AUTONOMY_PLAN.md](docs/AUTONOMY_PLAN.md).
Blue is an obstacle; yellow remains available for calibration. Motion uses timed calibration and
still needs supervised physical validation; camera size alone never confirms the 10 cm stop.
