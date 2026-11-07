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
