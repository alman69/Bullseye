# Bullseye scooper

Two SG90 servos swing popsicle sticks together to squeeze an ore block up. The
firmware runs on the **separate ESP32-S3 board that also drives the 3.1" IPS
touchscreen** (see `firmware/esp32-display`). It does not run on either rover
board and does not touch the vendor firmware.

The motion:

1. **Power-up:** both sticks point straight down.
2. **Squeeze:** both swing out about 30°, then back in to about 80° inward of
   straight down (a 110° swing) to get under the block and lift it.
3. **Release:** out 30° to drop the block, then straight down.

It's **controlled over Wi-Fi**, like the rest of Mission Control. The ESP32-S3
joins the rover's own `ELEGOO-…` access point at the fixed address
**192.168.4.50**. The `/bench` page's Squeeze button reaches it there.

**Status:** not yet compiled for the ESP32 or run on hardware. The angles are the
ones you described, but they still need checking on the real scooper. Uploading
this sketch **replaces the display sketch** on the board until the two are merged.

## Files

| File | What it is |
| --- | --- |
| `bullseye_scooper/ScooperConfig.h` | **Edit this.** Pins, limits, trims, the down/out/in angles and speeds |
| `bullseye_scooper/WifiConfig.h` | **Edit this** for another rover: ELEGOO network name, fixed address |
| `bullseye_scooper/Scooper.h` | Nonblocking motion logic (slew, sequences, stop/relax). No Arduino dependency |
| `bullseye_scooper/ServoOutput.h` | 50 Hz SG90 PWM on the ESP32 LEDC peripheral (no extra library) |
| `bullseye_scooper/bullseye_scooper.ino` | Initialization, Wi-Fi + HTTP commands, USB serial commands for bench tests |
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

## Wi-Fi

| Setting | Value (in `WifiConfig.h`) |
| --- | --- |
| Network | `ELEGOO-2436CBF61B44`, the team rover's open camera access point |
| Scooper address | `192.168.4.50` (fixed; the camera is `192.168.4.1`) |
| Mission Control setting | `SCOOPER_URL=http://192.168.4.50` (the default) |

- **The rover must be on** for the scooper to join. The scooper retries every 10 s,
  so the power-up order doesn't matter. The servos work from power-up whether or
  not Wi-Fi is connected.
- **For a different rover**, change `kWifiSsid`. The name is printed on the
  camera's serial output and shown in the laptop's Wi-Fi list.
- **Device limit:** the camera's access point allows **4 devices** at once by
  default. The laptop plus the scooper make 2, so a phone or two can still join.
- **The network has no password**, so anyone who joins it can send scooper
  commands. That's the same as the rover itself.

HTTP API (Mission Control uses this; keep `mission-control/server/bullseye/scooper.py` in sync):

| Request | Reply |
| --- | --- |
| `GET /status` | `{"state":"holding","sequence":null,"step":null,"angles":{"left":90,"right":90}}` |
| `POST /command?name=squeeze` | `200 {"ok":true,"status":{...}}`, or `409 {"ok":false,"error":"..."}` if refused |

Commands: `squeeze`, `release`, `home`, `stop`, `relax`. To test from the laptop
while it's on the ELEGOO Wi-Fi: open `http://192.168.4.50/status` in a browser, or
run `curl -X POST "http://192.168.4.50/command?name=squeeze"`.

## Upload

Use the same Arduino IDE settings as the display sketch: ESP32S3 Dev Module,
Espressif core **3.3.12** (the LEDC calls need core 3.x), OPI 80 MHz flash,
32 MB flash, OPI PSRAM, Default 4MB with spiffs, USB CDC On Boot **Disabled**,
upload 115200 through the **UART** USB connector. No libraries to install:
`WiFi` and `WebServer` come with the ESP32 core.

## Calibrating (Serial Monitor, 115200, newline)

USB is only for flashing and these bench tests; Mission Control doesn't need it.

1. Power up, then fit the horns so both sticks point straight down, as close as
   the spline allows. Fix what's left with `trimDeg`, in degrees, positive or negative.
2. `set 1 60` should swing the **left** stick **outward**. If it goes inward, flip
   `inverted` on both joints. Then check `set 2 60` swings the right stick outward.
3. Try `squeeze` with no block, then with a block. Adjust `kOutDeg`/`kInDeg` and the
   speeds in `ScooperConfig.h`. `home` returns straight down; `stop` freezes.
4. When gripping, the sticks press on the block and the SG90s draw stall current.
   If they buzz or get hot, set `kInDeg` so they just touch the block.
5. `wifi` shows whether it joined the ELEGOO network and its address.

| Command | Effect |
| --- | --- |
| `squeeze` | Down → out 30° → in 80° |
| `release` | Out 30° → straight down |
| `home` | Both sticks straight down |
| `stop` or `x` | Freeze now and cancel a squeeze/release |
| `relax` | Stop PWM so the servos go limp. The next move re-arms |
| `set <1-2> <deg> [dps]` | Move one stick (logical degrees: 90 = down, lower = out) |
| `status`, `wifi`, `help` | Angles and state, Wi-Fi connection, or commands and the pinout |

## Squeeze button in Mission Control

The `/bench` page has a **Scooper** panel with Squeeze, Release and Straight down
buttons, plus Connect. Only the pilot can press the buttons, but the rover
controls don't need to be enabled. The global **STOP** also freezes the sticks.

1. Power the rover and the scooper. Wait for the Serial Monitor to show
   `Wi-Fi: connected`, or just give it a few seconds.
2. With the laptop on the same ELEGOO Wi-Fi, start the bench server and open `/bench`.
3. Claim controls, press **Connect** in the Scooper panel, then **Squeeze**.

If Connect says the scooper isn't reachable, check that it's powered and that
`wifi` in the Serial Monitor shows `connected`.
