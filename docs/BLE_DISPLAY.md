# Separate BLE telemetry display

For this display **plus the scooper on the same ESP32-S3**, use the
[combined one-file upload](../firmware/esp32-companion/README.md). BLE UUIDs,
the sender commands and the full-screen animated face remain unchanged.

The Mac reads **cached ultrasonic telemetry** from the existing real-rover
controller and forwards it to a separate ESP32-S3 touchscreen over BLE. It can
keep Wi-Fi connected to the rover at the same time. Neither the sender nor the
touchscreen sends rover commands, claims controls or requests measurements.

```text
UNO → rover ESP32 → Wi-Fi → real-rover controller on Mac (:8001)
                                      ↓ cached GET /api/bench/state, 2 Hz
                              separate Mac sender process
                                      ↓ BLE GATT
                              ESP32-S3 → touchscreen
```

The sender works locally without Internet after dependencies are installed.
It does not need a Cloudflare tunnel. Stopping or failing the sender leaves
the rover controller running independently. Touch switches Distance, Link and
Face pages; it has no pilot or movement functions.

## 1. Prepare and upload the display firmware

The operator performs upload, serial access and physical tests. Disconnect the
Onebox vehicle adapter while uploading/switching firmware. Uploading this sketch
replaces the sketch currently running on the separate display board; its saved
Onebox source remains intact. Do not upload this sketch to the rover.

Open this file in Arduino IDE:

```text
/Users/faiyaz/Bullseye/firmware/esp32-display/bullseye_display/bullseye_display.ino
```

Use the existing working libraries: **DFRobot_GDL 1.0.4** (the installed copy
with `DFRobot_ST7365P_320x480_HW_SPI`), **Adafruit GFX 1.12.6**, and **Adafruit
BusIO 1.17.4**. BLE and cJSON come from Espressif core **3.3.12**. Do not install
an alternative BLE library or use BluetoothSerial/SPP.

| Arduino setting | Value |
| --- | --- |
| Board | ESP32S3 Dev Module |
| Espressif core | 3.3.12 |
| Flash mode | OPI 80 MHz |
| Flash size | 32 MB (256 Mb) |
| PSRAM | OPI PSRAM |
| Partition scheme | Default 4MB with spiffs |
| USB CDC On Boot | Disabled |
| Upload speed | 115200 |
| USB connector | UART USB, also used for 115200-baud debug |

Preserve the tested wiring. These are GPIO numbers, with 3.3 V logic and common
ground. USB powers the ESP32-S3. Disconnect power before touching connections.

| Display signal | Connection |
| --- | --- |
| VCC / GND | 3V3 / GND |
| SCLK / MOSI / MISO | GPIO12 / GPIO11 / GPIO13 |
| SCR_CS / RST / DC | GPIO10 / GPIO9 / GPIO8 |
| BLK | 3V3, always on |
| Touch SDA / SCL | GPIO4 / GPIO5 |
| Touch TP_R / INT | GPIO6 / GPIO7 |
| SD_CS | Disconnected; microSD unused |

Display setup is unchanged: `DFRobot_ST7365P_320x480_HW_SPI screen(8, 10, 9,
GDL_PIN_NC)`, `SPI.begin(12, 13, 11, 10)`, `screen.begin(8000000)`, rotation **3**,
landscape **480×320**. Touch retains the working reset/address-selection sequence,
100 kHz I2C, 5 ms transaction timeout and 20 ms polling. The driver validates
GT911 at `0x5D` or FT54xx at `0x38`; the physical identity still needs an operator
check. Failure leaves telemetry rendering and retries touch detection every five seconds.

On boot, expect only the large pixel robot on the navy **Face** page. It uses
the full 480×320 canvas, with no title, captions, telemetry, buttons or tabs.
Swipe left/right to switch **Distance / Link / Face**; swiping left from Face
opens Distance. There are no bottom tabs or hidden tap controls on any page.
Distance retains the large ultrasonic reading and sample age. Link reports the
detected touch identity, connection, source, freshness, last sequence and
protocol-error count. Drawing belongs to the main loop, updates changed fields
only, and is capped at 10 Hz.

The Face page implements the selected **Pixel bot** design:

| Expression | Animation |
| --- | --- |
| Idle | Periodic blink |
| Happy | Pixel bob, smile and blink |
| Curious | Side-to-side glances, raised eyebrow and blink |
| Grumpy | Eyebrow twitch, blink and pulsing red accents |
| Sleepy | Drooping eyelids, breathing mouth and drifting Z |

Auto is enabled on boot and cycles every four seconds: Happy → Curious →
Grumpy → Sleepy → Idle → Happy. Every expression also has its own animation.
Taps do nothing, so the old tab/Auto hit regions cannot interrupt the cycle.
These are playful local animations, independent of sensor values and rover
state. They do not signal obstacle avoidance or send BLE/rover commands.

Telemetry is shown on Distance and Link, reached by swiping. Numeric values on
Distance still blank when stale or disconnected; the Face page keeps animating.
A tiny cell buffer compares frames and sends only changed runs of 8×8 pixels
to the LCD; it does
not clear the whole display on each animation tick. No image assets or new
Arduino libraries are needed.

For this update, stop the Mac sender with Ctrl-C, close/reopen the sketch so
Arduino loads the added headers, and upload with the same settings. Then restart
the same sender command. There is no sender or rover firmware update to perform.

## 2. Install the optional Mac sender

Use Python **3.13**. These absolute paths work from any directory and avoid the
earlier `cd` confusion. If the existing server virtualenv is already set up,
only the second command is necessary.

```sh
python3.13 -m venv /Users/faiyaz/Bullseye/mission-control/server/.venv
/Users/faiyaz/Bullseye/mission-control/server/.venv/bin/python -m pip install -r /Users/faiyaz/Bullseye/mission-control/server/requirements.lock.txt -r /Users/faiyaz/Bullseye/mission-control/server/requirements-ble-macos.lock.txt -e '/Users/faiyaz/Bullseye/mission-control/server[ble,test]'
```

The optional lock pins Bleak **3.1.0** and its macOS PyObjC dependencies. The
normal Mission Control dependency lock is unchanged. Installation needs Internet;
do it before joining rover Wi-Fi if no second Internet connection is available.

Allow Bluetooth for the terminal application that launches the sender under
**System Settings → Privacy & Security → Bluetooth**. Enable Bluetooth on the
Mac. If permission was denied, enable it and restart that terminal application.
When launched from an app's integrated terminal, permission belongs to that app.
The sender prints this guidance on CoreBluetooth permission/unavailability errors.

## 3. Test labelled demo data first

With the uploaded ESP32-S3 powered by USB, run:

```sh
/Users/faiyaz/Bullseye/mission-control/send-display.sh --demo
```

Swipe left from Face to Distance for **DEMO DATA**, the large **37 cm**, BLE Connected, and Rover
**Demo (no rover)**.
The first console acknowledgement means the firmware's UI loop validated and
applied the message; it does not independently verify the LCD pixels or sensor.
Demo mode never opens Mission Control HTTP or a rover connection.

Try another distance, or list/select a display explicitly:

```sh
/Users/faiyaz/Bullseye/mission-control/send-display.sh --demo --distance-cm 75
/Users/faiyaz/Bullseye/mission-control/send-display.sh --list
/Users/faiyaz/Bullseye/mission-control/send-display.sh --demo --device-id 'IDENTIFIER-FROM-LIST'
```

Stop the previous sender with Ctrl-C before starting another. Discovery uses
the service UUID. With multiple matches the sender requires `--device-id`.
On macOS this is a **CoreBluetooth UUID**, not a Bluetooth MAC address; use the
identifier printed by `--list`. One central owns the display connection.

Operator checks, in order:

1. Verify swipe navigation through all three pages and the detected touch
   controller on Link status. Watch Face cycle through all five expressions
   automatically, with no text or controls visible. Taps must have no effect.
   Returning to Face must repaint it cleanly. A touch failure must not stop rendering.
2. Interrupt the sender: a clean disconnect must blank the number and show BLE
   disconnected. Restart and expect a fresh session starting at sequence 1.
3. Unplug/replug the display while the sender stays running. It must rediscover,
   reconnect and show current data. No old partial message may appear.
4. If the BLE link remains connected but updates stop, the number must blank
   after 1.5 seconds of sample age and show **Application timed out** after three
   seconds without a valid update. This differs from BLE disconnected.

## 4. Forward real cached ultrasonic readings

Keep the existing rover Wi-Fi connection. If the controller is not already
running, start it in its own terminal:

```sh
/Users/faiyaz/Bullseye/mission-control/run-rover-demo.sh
```

Open **http://127.0.0.1:8001/bench**, claim controls and **Connect rover bridge**
using the [existing controller procedure](ELEGOO_CONNECTION.md#local-browser-control-demo).
Controls can remain disabled. Connecting that controller performs its existing
STOP/query handshake and starts its normal sensor schedule. The display sender
does not change that schedule or connect the rover itself.

In a separate terminal:

```sh
/Users/faiyaz/Bullseye/mission-control/send-display.sh
```

Swipe to Distance for **FIELD TELEMETRY**, the same uncalibrated centimetres
as the browser, and current sample age.
Move an obstacle through known distances and compare
both displays. The vendor clamps long distances: **150+ cm** is a saturated
reading. Zero/no echo becomes **No echo / invalid** with a blank number, never
clear space. No calibration or obstacle-avoidance behavior is added here.

Default acquisition is a read-only GET twice per second from:

```text
http://127.0.0.1:8001/api/bench/state
```

`--state-url http://127.0.0.1:OTHER_PORT/api/bench/state` supports another local
port. The simulation server on 8000 is not a real ultrasonic source. A closed
controller, disconnected rover, bad HTTP response or incompatible state yields
**unavailable**, not a replayed reading. Test disconnecting the rover bridge and
stopping/restarting Mission Control while the sender runs; sender recovery must
not claim pilot controls, enable motors or reconnect the rover bridge.

HTTP acquisition and BLE delivery run independently with a latest-value mailbox.
There is no telemetry backlog. Request transit, mailbox wait, BLE fragmentation
and on-device wait count toward sample age. The sender reconnects with 1/2/4/5
second capped backoff, resets sequence numbers and sends only the latest state.
BLE trouble stays confined to the sender process.

## Wire protocol

Advertised name: **Bullseye-Display** in the scan response. The service UUID is
in the advertising data. BLE uses the library bundled with ESP32 core 3.3.12
(NimBLE on the installed S3 build) and the core-bundled cJSON parser.

| Interface | UUID | Properties |
| --- | --- | --- |
| Service | `3f7c1000-6f54-4a9c-8b2d-56e39a18c201` | Primary |
| RX | `3f7c1001-6f54-4a9c-8b2d-56e39a18c201` | Write with response |
| ACK | `3f7c1002-6f54-4a9c-8b2d-56e39a18c201` | Read and notify |

One UTF-8 JSON object per newline, **maximum 256 bytes including newline**:

```json
{"v":1,"seq":42,"distance_cm":31,"status":"live","sample_age_ms":180,"rover_connected":true,"source":"rover"}
```

All seven fields are required; unknown/duplicate fields, nonintegral numbers,
embedded NULs and invalid enums are rejected. Version is 1. Sequence is uint32,
1 through 4294967295, strictly increasing per connection. Reconnect before
rollover. Distance is integer 1–150 or null. Age is uint32 milliseconds or null.
Status is `live`, `stale`, `invalid` or `unavailable`; source is `rover` or `demo`.
Live requires distance and age; invalid/unavailable require null distance.
`rover_connected` is a JSON boolean. Demo never claims a real rover connection.

RX reassembles arbitrary chunks, queues at most two complete frames, expires
partial frames after two seconds, and discards an oversized/expired frame
through its next newline. BLE callbacks only frame/enqueue bounded data, under
a mutex. JSON parsing, validation, sample updates and drawing run in the main
loop. Framing errors may be coalesced while that loop is busy.

ACK is exactly eight bytes, emitted after validation/application in the UI loop:

| Byte(s) | Meaning |
| --- | --- |
| 0–1 | ASCII `BS` (`42 53` hex) |
| 2 | Protocol version `01` |
| 3 | `00` applied, `01` invalid, `02` overflow, `03` old sequence |
| 4–7 | Little-endian uint32 sequence; zero for unattributable framing/schema errors |

Example applied sequence 42: `42 53 01 00 2a 00 00 00`. Reading ACK returns the
latest result; the sender subscribes before writing. It keeps one message
outstanding, writes sequentially with response using `min(180, negotiated MTU−3)`
bytes, and falls back to 20 bytes if MTU is unavailable. Writes have a two-second
overall deadline; missing ACK after three seconds closes the session. Error ACKs
also close/reconnect the sender session.

Duplicate/older/invalid packets do not refresh data freshness. At 1.5 seconds
of sample age the numeric value blanks. At three seconds without a valid update
(including a connection that never sent one), the application times out. BLE
disconnect clears partial/queued data, the displayed sample and sequence tracking,
then resumes advertising. Connection epochs prevent old queued/parsed packets
or their ACKs from reaching a new session. The Link error count is cumulative
until board reset.

## Offline verification

No hardware upload, serial access, BLE scan/connection or physical measurement is
part of agent-side verification. The operator must still validate actual radio,
display pixels, touch response and simultaneous Wi-Fi/BLE operation.

```sh
(cd /Users/faiyaz/Bullseye/mission-control/server && .venv/bin/python -m pytest -q)
/Users/faiyaz/Bullseye/firmware/esp32-display/tests/run-native.sh
```

Native checks use macOS Command Line Tools clang++ with AddressSanitizer and
UndefinedBehaviorSanitizer, plus the installed Homebrew cJSON development
library (`CJSON_PREFIX=/opt/homebrew` by default; install `cjson` if missing).
They exercise the actual framing, validation, model, renderer and touch headers
using fake screen/I2C adapters. Pixel checks verify all five animations, clean
erasure of changed cells, repaint after page changes, bounded drawing and touch
swipe-only navigation and default Auto without changing telemetry freshness. Host cJSON is
1.7.18; the target core bundles
1.7.19. Python tests deny live socket/BLE access and use fake BLE clients and
HTTP transports. They cover fragmentation, schemas, ACKs/timeouts, source outage,
sample age, device selection and reconnect-with-latest behavior. Existing rover
tests cover cached state and control behavior; the sender adds no rover requests.

Compile only, with the installed Arduino CLI and the working partition settings:

```sh
'/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli' compile \
  --fqbn 'esp32:esp32:esp32s3:FlashSize=32M,FlashMode=opi,PSRAM=opi,CDCOnBoot=default,PartitionScheme=default,UploadSpeed=115200' \
  --build-path /private/tmp/bullseye-ble-display-build --jobs 4 \
  /Users/faiyaz/Bullseye/firmware/esp32-display/bullseye_display
```

Full-screen face verification on November 8, 2026: **34 display-sender tests
passed**; the full **116 backend/sender tests** passed earlier in the pixel-face
update. The cached-API integration check verifies
no sensor acquisition or pilot/control mutation during forwarding. Native
sanitizer checks passed for framing, schemas, ACK encoding, freshness,
reconnects, pixel animations, rendering bounds/dirty updates and three-page
touch navigation. Actual firmware artwork was also rendered and inspected
offline. The target build passed with **665,187 bytes flash (50% of 1,310,720)**
and **34,036 bytes global RAM (10% of 327,680; 293,644 bytes remaining)**.
These are build-time figures, not
runtime heap/PSRAM measurements. Flash percentage uses the selected default app
partition, rather than all 32 MB of physical flash.

The operator has reported the earlier firmware's BLE demo and real UNO readings
working together. The new Face page and its animations still need operator
upload and physical checks. Frontend code was unchanged in this update; its
37 tests and Node 24 production build last passed on November 7.

References: [hardware handoff](/Users/faiyaz/Onebox/docs/reference/hardware-handoff-ble-display.md),
[Bleak macOS identifiers/permissions](https://bleak.readthedocs.io/en/latest/backends/macos.html),
[Bleak client write/notification API](https://bleak.readthedocs.io/en/latest/api/client.html),
and [local source provenance](SOURCE_REFERENCES.md#standalone-ble-display).
