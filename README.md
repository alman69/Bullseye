# Bullseye

A project for UB Hackathon 2026.

Camera-guided sample missions with a laptop-hosted Mission Control dashboard.

- [Mission Control setup, simulator, camera relay, and integration API](docs/MISSION_CONTROL.md)
- [Firmware setup and known hardware behavior](docs/FIRMWARE_SETUP.md)
- [Direct ELEGOO connection: no-app, no-motion bench test](docs/ELEGOO_CONNECTION.md)
- [Local real-rover control demo](docs/ELEGOO_CONNECTION.md#local-browser-control-demo)
- [Separate ESP32-S3 BLE distance display and Mac sender](docs/BLE_DISPLAY.md)
- [Combined animated display + scooper over one BLE connection: single ESP32-S3 upload](firmware/esp32-companion/README.md)
- [ESP32-S3 ore scooper (two SG90s) and the /bench Squeeze button](firmware/esp32-scooper/README.md)
- [Vendor source references and attribution](docs/SOURCE_REFERENCES.md)

After setup, run `./mission-control/run.sh` and open http://localhost:8000.
The dashboard defaults to simulation. Existing vendor firmware remains untouched.
