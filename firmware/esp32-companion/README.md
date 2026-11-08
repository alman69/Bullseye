# Bullseye Companion — one upload for display and scooper

Upload **one file** to the existing ESP32-S3 touchscreen board:

```text
/Users/faiyaz/Bullseye/firmware/esp32-companion/bullseye_companion/bullseye_companion.ino
```

This sketch contains the animated full-screen pixel face, swipeable Distance /
Link pages, the unchanged BLE telemetry protocol, and both scooper servos with
Wi-Fi control from the **existing `/bench` Mission Control UI**. It does not
connect to the rover camera stream or send UNO/motor commands. Original vendor,
display-only and scooper-only sources remain available separately.

The generated `.ino` has all project headers embedded. Open that actual file
with Arduino IDE; no copying of other project files or tabs is needed. External
libraries already used by the display remain required.

## Hardware and upload settings

Keep the existing display/touch wiring and initialization unchanged:

- ST7365P LCD on SPI12/13/11, CS10, DC8, reset9, backlight 3V3.
- `screen.begin(8000000)`, rotation 3, 480×320.
- Touch SDA4/SCL5, reset6/INT7; 100 kHz, 5 ms I2C timeout, 20 ms polling.
- Servo signals: **GPIO41 left / GPIO42 right**. The existing right-servo
  inversion, trims and limits come from `esp32-scooper/ScooperConfig.h`.
- Power the SG90 servos from a suitable **separate 5 V supply with shared GND**.
  The S3 USB powers its logic/display. Do not power servos from 3V3.

**Both servos are commanded to 90° at boot**, after display/BLE initialization.
The inherited squeeze is 90° → 60° → 170°. These are software commands, not
measured positions; horn alignment and mechanical limits need operator checks.

| Arduino setting | Value |
| --- | --- |
| Board / Espressif core | ESP32S3 Dev Module / 3.3.12 |
| Flash mode / size | OPI 80 MHz / 32 MB |
| PSRAM | OPI PSRAM |
| Partition | Default 4MB with spiffs |
| USB CDC On Boot | Disabled |
| Upload / serial speed | 115200, UART USB connector |

Libraries: the existing working DFRobot_GDL, Adafruit GFX and Adafruit BusIO.
BLE, cJSON, Wi-Fi, Network and LEDC come from the installed core. No ESP32Servo,
BluetoothSerial or additional Arduino library is needed.

## Connect everything

1. Stop `send-display.sh` with Ctrl-C while uploading. Close/reopen the combined
   sketch in Arduino IDE, select the same board settings, and upload it yourself.
2. Expect the same full-screen animated face, with Auto on and no labels or
   buttons. Swipes still cycle Face → Distance → Link → Face.
3. Power the rover AP. Both the Mac and the companion S3 join
   `ELEGOO-2436CBF61B44`. The companion uses **192.168.4.50**; the camera/rover
   remains **192.168.4.1**. The S3 retries Wi-Fi every 10 seconds. Wi-Fi is for
   scooper commands; BLE independently carries display telemetry.
4. First check **status only**, from the Mac on rover Wi-Fi:

   ```sh
   curl --max-time 5 http://192.168.4.50/status
   ```

   Expect JSON containing `firmware: "bullseye-companion"`, `api: 2`,
   `servo_ready: true`, a nonzero `boot_id`, commanded angles and current state.
   A PWM failure disables motion and is reported rather than enabling buttons.
5. Restart the old Mission Control server, so its backend matches the new
   firmware, then run the usual command:

   ```sh
   /Users/faiyaz/Bullseye/mission-control/run-rover-demo.sh
   ```

   Open **http://127.0.0.1:8001/bench**. The frontend on this branch includes the
   Scooper panel. **Claim controls → Scooper Connect**, then use **Squeeze**,
   **Release**, or **Straight down** after checking the mechanism's range. Rover
   driving can remain disabled. Connecting stops any already-reported scooper
   movement; reconnection never replays an earlier command.
6. Restart the existing BLE sender in another terminal:

   ```sh
   /Users/faiyaz/Bullseye/mission-control/send-display.sh
   ```

   Connect the rover bridge in the browser if actual ultrasonic telemetry is
   wanted. `send-display.sh --demo` still works for the display without it.
   The face needs neither link to keep animating. The display's Link page reports
   the BLE/relayed rover telemetry connection, not proof of scooper Wi-Fi health.

`SCOOPER_URL=http://192.168.4.50` is the backend default. No USB data tether is
required for scooper control once flashed; USB may still supply board power.
The existing tunnel arrangement is unchanged: browsers talk to Mission Control,
and Mission Control talks to the two rover-network devices. Remote browsers need
no direct route to 192.168.4.50.

## STOP and connection loss

- The sticky/global website **STOP** cancels queued/in-flight scooper work and
  sends a newer ordered STOP, alongside the existing UNO stop behavior.
- The device rejects duplicate/older command IDs, including a squeeze packet
  delivered after a newer STOP. IDs are tied to the current device boot.
- Pilot disconnect/release/expiry still stops the rover and scooper through the
  coordinator. Only the pilot can start scooper actions; any viewer may STOP.
- Missing **server-to-companion HTTP contact for two seconds** stops active
  scooper motion locally. A detected companion Wi-Fi loss also stops it. Normal
  `/status` polling supplies contact; BLE telemetry does not renew this lease.
- STOP/watchdog hold the last commanded angles; they do not remove holding
  torque or guarantee freedom from a mechanical stall. `relax` is available via
  serial to turn pulses off. Physical stopping and servo current need operator
  verification.
- Slow/incomplete HTTP clients cannot hold the drawing/servo loop: parsing and
  socket I/O are bounded and nonblocking. Header/response deadline is 500 ms.

## Protocol and local calibration

`GET /status` is read-only. All wireless actions use:

```text
POST /command?name=squeeze|release|home|stop|relax&boot=<boot_id>&seq=<increasing uint32>
```

The updated backend handles IDs automatically. An old backend using only
`?name=` receives 409 and must be restarted/updated; it cannot start an unordered
move. Command acknowledgement means accepted by the firmware, not independently
verified servo motion. HTTP connections close after each bounded response.

Serial Monitor (115200, newline) supports `status`, `wifi`, `stop`, `relax`,
`home`, `squeeze`, `release`, and `set 1|2 degrees [speed]`. Local calibration
does not require the HTTP heartbeat. Servo poses/pins/trims remain in the
original `ScooperConfig.h`; after editing, regenerate the upload file below.

## Maintaining the single file

Source modules remain readable/testable in `esp32-display/`, `esp32-scooper/`
and this folder. The generator embeds them in order and adds the common loop.
It never includes vehicle credentials or any Onebox networking. The existing
Onebox-derived touch driver attribution is retained.

```sh
python3 /Users/faiyaz/Bullseye/firmware/esp32-companion/generate.py
python3 /Users/faiyaz/Bullseye/firmware/esp32-companion/generate.py --check
/Users/faiyaz/Bullseye/firmware/esp32-companion/tests/run-native.sh
```

Compile only:

```sh
'/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli' compile \
  --fqbn 'esp32:esp32:esp32s3:FlashSize=32M,FlashMode=opi,PSRAM=opi,CDCOnBoot=default,PartitionScheme=default,UploadSpeed=115200' \
  --build-path /private/tmp/bullseye-companion-build --jobs 4 \
  /Users/faiyaz/Bullseye/firmware/esp32-companion/bullseye_companion
```

The combined build uses **1,220,410 bytes flash (93% of the selected 1,310,720-byte
app partition)** and **56,888 bytes globals (17%; 270,792 bytes remaining)**.
Those are build-time numbers, not measurements of runtime heap/PSRAM usage.

Native sanitizer checks cover bounded/fragmented HTTP, invalid requests,
out-of-order STOP, link watchdog, failed output initialization and the real
shared servo/face/BLE core loop. Backend tests cover command ordering,
cancellation, restart/reconnect and pilot rules; frontend tests exercise the
existing Scooper panel and rover controls. **No upload, serial/BLE interaction
or physical combined-board test was performed by the agent.** Verification on
November 8, 2026: **149 backend tests**, **53 frontend tests**, TypeScript /
production build, native display and combined-controller sanitizer checks all
pass. The standalone scooper motion checks were also compiled/run during review.

Vision baseline work was preserved separately in local commit **70c528c** on
`codex/vision-baseline`. This integration is on **codex/display-scooper** based
on the teammate's latest Wi-Fi scooper branch; it does not require merging the
vision experiment into the upload.
