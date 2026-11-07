# Bullseye

A project for UB Hackathon 2026.

Camera-guided sample missions with a laptop-hosted Mission Control dashboard.

- [Mission Control setup, simulator, camera relay, and integration API](docs/MISSION_CONTROL.md)
- [Firmware setup and known hardware behavior](docs/FIRMWARE_SETUP.md)
- [Direct ELEGOO connection: no-app, no-motion bench test](docs/ELEGOO_CONNECTION.md)
- [Local real-rover control demo](docs/ELEGOO_CONNECTION.md#local-browser-control-demo)
- [Vendor source references and attribution](docs/SOURCE_REFERENCES.md)

After setup, run `./mission-control/run.sh` and open http://localhost:8000.
The dashboard defaults to simulation. Existing vendor firmware remains untouched.
