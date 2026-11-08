# Bullseye scooper

Two SG90 servos swing popsicle sticks together to squeeze an ore block up. The
firmware runs on the **separate ESP32-S3 board that also drives the 3.1" IPS
touchscreen** (see `firmware/esp32-display` on `codex/mission-control`). It does
not run on either rover board and does not touch the vendor firmware.

The motion:

1. **Power-up:** both sticks point straight down.
2. **Squeeze:** both swing out about 30°, then back in to about 80° inward of
   straight down (a 110° swing) to get under the block and lift it.
3. **Release:** out 30° to drop the block, then straight down.

**Status:** not yet compiled for the ESP32 or run on hardware. The angles are the
ones you described, but they still need checking on the real scooper. Uploading
this sketch **replaces the display sketch** on the board until the two are merged.

## Files

| File | What it is |
| --- | --- |
| `bullseye_scooper/ScooperConfig.h` | **Edit this.** Pins, limits, trims, the down/out/in angles and speeds |
| `bullseye_scooper/Scooper.h` | Nonblocking motion logic (slew, sequences, stop/relax). No Arduino dependency |
| `bullseye_scooper/ServoOutput.h` | 50 Hz SG90 PWM on the ESP32 LEDC peripheral (no extra library) |
| `bullseye_scooper/bullseye_scooper.ino` | Initialization and USB serial commands at 115200 baud |
| `tests/` | Native logic checks: `firmware/esp32-scooper/tests/run-native.sh` |

## SG90 pinout

Both servos use the header that has GPIO37. **Don't use GPIO 35, 36 or 37:** the
board's octal PSRAM uses them.

| SG90 wire | Left stick | Right stick |
| --- | --- | --- |
| Signal (orange) | **GPIO41** | **GPIO42** |
| V+ (red) | Separate 5 V supply, at least 1.5 A | same supply |
| GND (brown) | Supply GND **and** an ESP32 GND pin | same |

The ground must be shared, or the signal has no reference and the servos
twitch. Don't power the SG90s from the ESP32's 3V3 pin or its USB 5V: two
stalling SG90s pull over 1 A, which resets the board and flickers the screen.
A 470–1000 µF capacitor across the servo supply helps. The 3.3 V signal is fine
for SG90s.

**At power-up both sticks snap straight down.** Keep fingers clear.

## Upload

Use the same Arduino IDE settings as the display sketch: ESP32S3 Dev Module,
Espressif core **3.3.12** (the LEDC calls need core 3.x), OPI 80 MHz flash,
32 MB flash, OPI PSRAM, Default 4MB with spiffs, USB CDC On Boot **Disabled**,
upload 115200 through the **UART** USB connector. No libraries to install.

## Calibrating (Serial Monitor, 115200, newline)

1. Power up, then fit the horns so both sticks point straight down, as close as
   the spline allows. Fix what's left with `trimDeg`, in degrees, positive or negative.
2. `set 1 60` should swing the **left** stick **outward**. If it goes inward, flip
   `inverted` on both joints. Then check `set 2 60` swings the right stick outward.
3. Try `squeeze` with no block, then with a block. Adjust `kOutDeg`/`kInDeg` and the
   speeds in `ScooperConfig.h`. `home` returns straight down; `stop` freezes.
4. When gripping, the sticks press on the block and the SG90s draw stall current.
   If they buzz or get hot, set `kInDeg` so they just touch the block.

| Command | Effect |
| --- | --- |
| `squeeze` | Down → out 30° → in 80° |
| `release` | Out 30° → straight down |
| `home` | Both sticks straight down |
| `stop` or `x` | Freeze now and cancel a squeeze/release |
| `relax` | Stop PWM so the servos go limp. The next move re-arms |
| `set <1-2> <deg> [dps]` | Move one stick (logical degrees: 90 = down, lower = out) |
| `status`, `help` | Print angles and state, or commands and the pinout |

Every command is answered with one `ok` or `error: <reason>` line. Progress lines
look like `state=sequence squeeze:out left=60 right=60`. Mission Control parses
this protocol, so keep `mission-control/server/bullseye/scooper.py` in sync when
you change it.

## Squeeze button in Mission Control

The `/bench` page has a **Scooper** panel with Squeeze, Release and Straight down
buttons, plus Connect. The server opens the board's USB serial port; set
`SCOOPER_PORT=COM5` (or whatever COM port it shows) if it doesn't auto-detect one ESP32
port. Only the pilot can press the buttons, but the rover controls don't need to
be enabled. The global **STOP** also freezes the sticks.

1. Upload this sketch, then **close the Arduino Serial Monitor**. Only one program
   can open the port.
2. Plug the board's UART USB into the laptop and start the bench server.
3. Claim controls, press **Connect** in the Scooper panel, then **Squeeze**.

The board stays tethered by USB for now. For it to ride on the rover, the next
step is a wireless link, most likely a command added to faiyaz's BLE display
link. Agree on that with the team first, since it changes the display protocol.
