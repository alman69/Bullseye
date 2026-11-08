# Source references and provenance

The committed firmware text files came from the ELEGOO Smart Robot Car Kit V4.0 **2023.02.01** software package supplied by the project owner. Their Git blob hashes were checked against the same historical source in the initial commit of [this public archive mirror](https://github.com/herrsergio/elegoo-smart-robot/tree/c9d2b2104638) before importing. **Do not pull that mirror's current `main` as a replacement:** it contains later community modifications to obstacle avoidance and motor drivers.

Relevant reference documents from the supplied vendor package:

- [Communication protocol for Smart Robot Car.pdf](https://github.com/herrsergio/elegoo-smart-robot/blob/c9d2b2104638/04_Related_Chip_Information/Communication%20protocol%20for%20Smart%20Robot%20Car.pdf) — JSON serial control protocol, commands and responses (external link because this PDF is large).
- `docs/vendor/ReadMeFirst.pdf` — motor/IMU/USB chip variants.
- `docs/vendor/SmartRobot-Shield(TB6612).pdf` — expansion shield circuit schematic.
- [Official ELEGOO Smart Robot Car Kit V4.0 repository](https://github.com/elegooofficial/ELEGOO-Smart-Robot-Car-Kit-V4.0) — archived reference and downloads.

The vendor's `.hex` / `.ino.bin` outputs, macOS metadata, mobile app installers, alternative motor-driver versions, and bundled library ZIPs are intentionally not committed. Keep the original ZIP locally for manufacturer library versions and additional tutorials.

## Third-party notice

The ELEGOO firmware, included third-party source modules, PDFs and schematics are **third-party material**, not originally authored for this hackathon project. Retain source-level attributions and any applicable license notices. The supplied vendor package did not contain an obvious top-level license grant for the entire package; repository inclusion does not claim ownership or assign a new license to that content. Verify redistribution terms before wider publication or release.

## Standalone BLE display

`firmware/esp32-display/` uses the project owner's working hardware reference
at `/Users/faiyaz/Onebox/docs/reference/hardware-handoff-ble-display.md`.
`bullseye_display/TouchInput.h` is copied unchanged from
`Onebox/firmware/onebox_telemetry/TouchInput.h`, inspected at Onebox HEAD
`a7fd3eaf21aab354d95f34d4e5bf5ca76aa6dc00` on November 7, 2026. The copied file's
SHA-256 is `1b5e9d23ac1b24694ee2af644473f433478cdd309f607788774b5332732272da`.
The touch bus adapter and reset sequence are also reused from that sketch.
Only display/touch hardware portions are reused; no Onebox vehicle networking,
configuration or credentials are included. Its source and uncommitted work are
preserved. The original `onebox` namespace remains in the copied touch header.

Dependencies remain external libraries: the existing working DFRobot_GDL 1.0.4,
Adafruit GFX 1.12.6, Adafruit BusIO 1.17.4, Espressif Arduino core 3.3.12 with
bundled BLE/cJSON, and optional Bleak 3.1.0 with PyObjC 13.0 for the Mac sender.
Retain their respective license notices when redistributing dependencies. The
host-only native checks use the installed Homebrew cJSON 1.7.18; firmware uses
the core's cJSON 1.7.19. See [BLE display setup](BLE_DISPLAY.md).
