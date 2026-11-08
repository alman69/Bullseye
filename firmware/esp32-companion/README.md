# Bullseye Companion — display and scooper on one BLE connection

Upload **one file** to the existing ESP32-S3 touchscreen board:

```text
/Users/faiyaz/Bullseye/firmware/esp32-companion/bullseye_companion/bullseye_companion.ino
```

It contains the full-screen animated pixel face, three swipe pages, BLE distance
telemetry and both scooper servos. **The companion uses no Wi-Fi or HTTP server.**
Mission Control owns one BLE connection and forwards cached ultrasonic readings
while accepting scooper actions from the existing `/bench` panel.

```text
Browser → Mission Control on Mac → Wi-Fi → rover ESP32 ↔ UNO
                    ↕ one BLE GATT session
                 ESP32-S3 → touchscreen + GPIO39/40 scooper
```

The generated `.ino` embeds all project headers. No other project tabs need to be
copied into Arduino IDE. The original vendor firmware, standalone display and
standalone Wi-Fi scooper logic remain preserved; the shared servo pin configuration
now uses GPIO39/40.

## Hardware and upload

Current wiring (servo signals moved from GPIO41/42 for isolation testing):

- LCD: SPI12/13/11, CS10, DC8, reset9, backlight 3V3.
- `screen.begin(8000000)`, rotation 3, 480×320.
- Touch: SDA4/SCL5, reset6/INT7, 100 kHz, 5 ms timeout, 20 ms polling.
- Servo signals: GPIO39 left, GPIO40 right. Existing mirrored right output,
  limits, trims and poses come from `esp32-scooper/ScooperConfig.h`.
- Use a suitable separate **5 V servo supply with common GND**; do not power the
  servos from 3V3. USB supplies the S3 logic/display.

**The servos are commanded to 90° at boot.** Clear the mechanism before upload
or reset. Squeeze remains 90° → 60° → 170°. Angles are commanded positions;
horn alignment, load, mechanical range and physical stopping need operator checks.

| Arduino setting | Value |
| --- | --- |
| Board / Espressif core | ESP32S3 Dev Module / 3.3.12 |
| Flash mode / size | OPI 80 MHz / 32 MB |
| PSRAM | OPI PSRAM |
| Partition | Default 4MB with spiffs |
| USB CDC On Boot | Disabled |
| Upload / serial speed | 115200, UART USB connector |

Keep DFRobot_GDL, Adafruit GFX and Adafruit BusIO. BLE, cJSON and LEDC come from
the installed core. No additional Arduino libraries are needed.

## Operator setup

1. Stop the old `send-display.sh` process with Ctrl-C. Only **one** central can
   connect; Mission Control now sends both display data and scooper actions.
2. Upload the updated single `.ino` above yourself, using the same S3 settings.
   The full-screen face animates immediately, without a connection. Auto stays
   on, taps do nothing, and swipes cycle Face → Distance → Link → Face.
3. Stop/restart the old Mission Control server so it loads the BLE integration:

   ```sh
   /Users/faiyaz/Bullseye/mission-control/run-rover-demo.sh
   ```

4. Refresh **http://127.0.0.1:8001/bench**. Click **Claim controls**, then **Connect
   inside the Scooper panel**. It should say **DISPLAY + SCOOPER · BLE**. A BLE
   connection first requests STOP to hold the current pose. Only after its
   acknowledgement do Squeeze / Release / Straight down become available.
5. Connect the rover bridge in the same page for real ultrasonic readings. The
   Mac remains on ELEGOO Wi-Fi for that existing bridge and camera. Mission
   Control forwards its cached readings to the display twice per second,
   without opening another rover connection or requesting additional samples.
6. Check the display's Distance page and the panel's acknowledged-update count,
   then test the scooper's range with the mechanism clear. Rover driving can
   remain disabled. STOP in the website also requests scooper STOP over BLE.

### PWM readiness and BLE connection status

The earlier combined sketch checked `ledcReadFreq()` immediately after writing
the first home pulse. In the installed core 3.3.12, that function returns zero
when the current duty readback is zero. A new duty is not readable until the
next PWM cycle (20 ms at 50 Hz), so this check could falsely disable working
outputs at boot. See [Espressif's LEDC duty-readback documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-reference/peripherals/ledc.html).

The combined sketch now uses its own tested `ServoPwm.h` / `ArduinoServoPwm.h`
adapter, checking **both attach and write return values**. It does not rely on
immediate frequency readback. A real attach/write/detach failure disables both
outputs, reports the stage and GPIO in the 115200-baud startup/status log, and
keeps motion disabled until reset. The standalone scooper's original adapter
is preserved. No physical PWM waveform verification is claimed by these checks.

BLE and servo readiness are separate: a PWM fault now leaves the display's BLE
connection and telemetry active. The website reports **BLE connected · scooper
PWM unavailable** and disables all positive motion in both UI and backend.
STOP remains available. The display's Distance and Link pages should show
Bluetooth Connected; Rover/Freshness reflect the independent UNO connection
and cached data. A PWM fault is not a Bluetooth pairing failure. Previously the
backend closed BLE on that fault, making the display correctly show Disconnected.

**Do not start `send-display.sh` alongside this setup.** It remains available for
standalone display/demo use, but it occupies the same BLE peripheral. The old
sender has no scooper control heartbeat and cannot operate the servos.

The existing optional Bleak dependency is used. If it is missing:

```sh
cd /Users/faiyaz/Bullseye/mission-control/server
.venv/bin/python -m pip install -r requirements-ble-macos.lock.txt -e '.[ble]'
```

Enable Bluetooth and allow the terminal launching Mission Control under macOS
System Settings → Privacy & Security → Bluetooth. Browser Bluetooth permissions
and Web Bluetooth are not required; all BLE operations occur on the Mac server.

If several displays are found, stop other senders, run `send-display.sh --list`,
then set `BLE_DISPLAY_DEVICE_ID` in the server's local `.env` and restart.
Use the listed **CoreBluetooth identifier**, not an assumed MAC address.
See [Bleak's macOS guide](https://bleak.readthedocs.io/en/latest/backends/macos.html).

`SCOOPER_TRANSPORT=ble` is the default. `SCOOPER_TRANSPORT=http` and
`SCOOPER_URL=http://192.168.4.50` retain the separate legacy Wi-Fi-only sketch's
backend for comparison. This combined BLE sketch has no endpoint at that IP.
Remote browsers still use the existing single Mission Control origin/tunnel;
only the laptop must be within BLE range of the S3.

## Safety and protocol

For operator-run, small-angle serial tests that bypass BLE, see
[the physical scooper baseline](../../docs/SCOOPER_TEST.md). The page diagram
shows commanded angles; these SG90s do not provide position feedback.

The Mac now serializes heartbeat and action writes on the control characteristic.
Bleak/CoreBluetooth keeps one pending write-with-response future per handle;
overlapping writes could time out/disconnect an otherwise working connection.
STOP cancellation drains an already-issued write within its existing timeout
before writing STOP. A stuck write closes the session instead of overlapping.
The website exposes heartbeat applied/sent counts, last-send age and max gap.
It also shows observed device boot-ID changes across reconnects (including
manual resets), allowing a reboot to be distinguished from a BLE-only drop.

Watchdog history is cleared by an explicit STOP/relax or a new accepted move,
without resuming old motion. Disconnecting after a completed sequence no longer
claims it interrupted movement. The backend clears only the historical watchdog
warning when the device clears its flag. The two-second watchdog is retained.

- One pilot starts actions; every visitor may press STOP. Pilot loss requests
  STOP for both the UNO and companion using the existing coordinator.
- Command acknowledgements identify a device boot and increasing command number.
  STOP cancels queued/in-flight work; delayed older packets cannot overtake it.
- BLE disconnect or connection-epoch change stops active remote scooper motion
  in the main loop and discards queued commands. No automatic motion reconnect.
- Missing **control heartbeat for two seconds** stops active motion, even if
  display telemetry still arrives. Missing acknowledgements or stale device
  status closes the backend session and requires explicit Reconnect.
- STOP/watchdog hold the last commanded angles; they do not remove holding
  torque or independently verify a physical stop. Serial `relax` turns pulses
  off. Local serial calibration does not require the BLE heartbeat.
- BLE callbacks copy fixed-size commands into a bounded two-frame mailbox;
  parsing, actuation and drawing occur in the main loop. Display/touch behavior
  and the original telemetry framing remain unchanged.

All UUIDs share suffix `-6f54-4a9c-8b2d-56e39a18c201`:

| UUID prefix | Purpose |
| --- | --- |
| `3f7c1000` | Existing advertised service, name Bullseye-Display |
| `3f7c1001` / `3f7c1002` | Existing telemetry JSON writes / 8-byte BS acknowledgements |
| `3f7c1003` | Scooper commands, write with response |
| `3f7c1004` | Scooper status/ACK, read + notify |

Control commands are **12 bytes**, little endian: ASCII `BC`, version `1`,
opcode, uint32 boot ID, uint32 command number. Opcodes: 0 heartbeat, 1 squeeze,
2 release, 3 home, 4 stop, 5 relax. Heartbeats have command number zero; actions
require positive strictly increasing numbers. The initial boot ID is read from
status. All writes fit the minimum ATT payload; GATT write success alone is not
an application acknowledgement.

Status is **20 bytes**: `BC`, version, result, uint32 boot ID, uint32 latest
accepted command, uint32 acknowledged request, flags, left angle, right angle,
sequence step (1-based, zero outside a sequence). Flags: bit 0 PWM ready, bit 1
watchdog stopped, bits 2–3 state (relaxed/holding/moving/sequence), bit 4 release
sequence. Result: 0 periodic status, 1 applied, 2 invalid, 3 old, 4 refused,
5 overflow. Periodic status is sent every 500 ms; unapplied commands never count
as verified motion. Frame sizes and integer fields are tested in Python and C++.

## Development checks

```sh
python3 /Users/faiyaz/Bullseye/firmware/esp32-companion/generate.py
python3 /Users/faiyaz/Bullseye/firmware/esp32-companion/generate.py --check
/Users/faiyaz/Bullseye/firmware/esp32-companion/tests/run-native.sh
```

Compile only, with no hardware access:

```sh
'/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli' compile \
  --fqbn 'esp32:esp32:esp32s3:FlashSize=32M,FlashMode=opi,PSRAM=opi,CDCOnBoot=default,PartitionScheme=default,UploadSpeed=115200' \
  --build-path /private/tmp/bullseye-companion-ble-build --jobs 4 \
  /Users/faiyaz/Bullseye/firmware/esp32-companion/bullseye_companion
```

Software checks use fake BLE transports and native sanitizer tests. Physical
upload, BLE permission, combined servo/display operation and range tests remain
operator-performed; the agent does not flash, open serial or connect live BLE.

Verified November 8, 2026: **166 backend tests**, **58 frontend tests**, native
sanitizer checks, TypeScript and the production build pass. The combined sketch
compiles against the installed core/libraries: **680,090 bytes flash (51% of the
1,310,720-byte app partition)** and **34,404 bytes static RAM (10%; 293,276 bytes
remaining)**. These are build-time sizes, not runtime heap or PSRAM measurements.

Vision work remains on local `codex/vision-baseline` commit `70c528c`; this work
is on `codex/display-scooper`. Original vendor and standalone sources are intact.
