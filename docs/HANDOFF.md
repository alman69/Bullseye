# Bullseye team handoff (for teammates and their AI assistants)


## Current integration — 2026-11-08

The local integration combines the preserved vision/calibration work (`c6f3a91`) with
`origin/AlexTrainingAutonomousBranch` (`5625198`). The integration branch is
`codex/integrate-autonomy`; the operator requested a local result and a ready PR description,
with no GitHub publishing. Check the actual branch/log for completion status.

- Keep the local **palette** workflow: red, green, blue, yellow, brown cardboard, black home,
  and floor; click/box samples; frozen snapshots; traced rover polygons. Alex's older HSV
  calibration UI is not the primary workflow.
- Accepted examples and masks are saved immediately in `software/vision/config.json`. Restarting
  the server or rover does not erase them. A stale second server cannot silently overwrite newer
  calibration. The saved camera preset is reapplied when the real stream reconnects.
- **Manual squeezer controls are restored on `/bench`.** Claim the pilot seat, then
  Connect/Reconnect, Squeeze, Release, or Straight down. Driving can stay disabled.
  STOP also stops the squeezer. With combined companion firmware, stop `send-display.sh`
  first: Mission Control owns the shared BLE session. Automatic ore missions still use
  LED/display signalling without actuating the squeezer. No firmware was flashed.
- **Red and green are ore targets; blue is an obstacle.** Yellow remains a calibration class.
  A mission scans through a measured 360°, relocates using sonar clearance when absent, and
  scans again. It approaches a stable target to about 10 cm using sonar, stops, and disables
  motion. The user picks up the material by hand, confirms pickup, carries it home, and
  confirms delivery. Green LEDs/happy display signal arrival, not physical collection.
- Home means near-black, not arbitrary dark gray: brightness ≤40/255 and RGB channel spread
  ≤12/255. Color alone cannot distinguish a truly black shadow from the flag.
- Autonomy remains experimental timed approach/search. Obstacle avoidance stays on; search is
  bounded; approach and relocation require sonar; arrival requires fresh centered observations and
  acknowledged STOP. Physical stopping accuracy and arena exploration remain unverified.

Read [Simple vision](SIMPLE_VISION.md), the [exploration plan](AUTONOMY_PLAN.md), and the
[PR description](INTEGRATION_PR.md) for the current workflow. Older branch/test/calibration
reports below are historical unless updated explicitly. **Restart the server after pulling.**

## Ready-to-paste prompt for your AI assistant

> You are helping build **Bullseye**, an autonomous "Mars rover" for UB Hackathon 2026
> (24-hour event, team of three) on the ELEGOO Smart Robot Car V4.0. Repository:
> `git@github.com:alman69/Bullseye.git`. Before writing code, read `docs/HANDOFF.md`
> (this file), then `docs/SIMPLE_VISION.md`, `docs/AUTONOMY_PLAN.md`,
> `docs/MISSION_CONTROL.md` (especially "Vision (built in)"),
> `docs/ELEGOO_CONNECTION.md` and `software/vision/README.md`. Then inspect the code you will
> touch.
>
> **Rules:**
> - Keep solutions simple, because working functionality beats architecture.
> - Do not modify or flash the vendor rover firmware (`firmware/arduino-uno`,
>   `firmware/esp32-camera`) unless the team explicitly agrees.
> - No YOLO/ML models.
> - The ESP32 serves **one** camera client and **one** port-100 command client, and Mission
>   Control's server owns both. Never open a second connection from new code; build inside that
>   server.
> - Never claim a physical rover or camera test passed unless a human ran it and reported the
>   result.
> - Preserve operator calibration, and keep new motion changes bounded and testable.
> - The repo is **public**: never commit secrets, `.env`, or camera snapshots
>   (`software/vision/captures/` is gitignored).
> - Work on a branch and open a PR. The team co-owns all code; merging after tests pass is fine.
> - **After pulling code, restart the Mission Control server.** A running server keeps its old code
>   while the browser loads the new pages, which makes new features fail with
>   "Unknown vision command".

## 1. Mission and the current arena

**Mission:**
1. A user chooses red or green ore in Mission Control.
2. The rover scans 360°, relocates using fresh sonar when absent, and repeats.
3. It approaches the selected target to about 10 cm by sonar, stops, and disables motion.
4. The user picks up the material by hand and confirms pickup.
5. The user carries the material to home base and confirms delivery. The rover never returns automatically.

**Arena (as of 2026-11-08):**
- **Floor and walls:** matte white floor; unpainted cardboard walls.
- **Ore targets:** **red and green** objects; small paper balls can sit below the ultrasonic beam.
- **Obstacles:** **blue**. Yellow remains a supported calibration material, not an ore selection.
- **Lighting:** the **floodlight is always on**.
- **Gripper:** the rover now has a **popsicle-stick gripper** in front, and its two claws (blue/black
  tape) are always in the bottom of the camera view. **Ground/stop-line calibration distances use the
  stick tips** as zero. Raw sonar readings use the sensor origin; measure its mounting offset
  before treating them as an ore-arrival gap.

**Hardware:**
- Arduino UNO R3, ESP32-WROVER camera (Wi-Fi access point), TB6612, MPU6050, ultrasonic sensor and
  camera on one pan servo, RGB LEDs.
- A separate **ESP32-S3 touchscreen** shows telemetry over BLE (`docs/BLE_DISPLAY.md`).
- Two **SG90 servos** have restored manual controls; prior hardware experiments are recorded in §3.

## 2. Who started what (everyone may change anything)

| Area | Started by | Where |
|---|---|---|
| Vision (detector, classifier, floor check, planner) and vision-in-Mission-Control | Alex (`alman69`, repo owner) | `software/vision/`, `mission-control/server/bullseye/vision.py`, `mission-control/web/components/vision-*` |
| Mission Control (dashboard, server, `/bench` manual driving, rover bridge), BLE display | faiyaz | `mission-control/`, `firmware/esp32-display/`, `docs/MISSION_CONTROL.md`, `docs/ELEGOO_CONNECTION.md`, `docs/BLE_DISPLAY.md` |
| Gripper "scooper" (SG90 squeeze) | Luke Coppola | branch `feature/scooper` (not merged, §3) |

## 3. Historical repository and servo experiment notes

**Archive:** the following records explain earlier decisions and tests. They do not override the
current integration above; do not use the old Scooper Connect/Squeeze workflow.

**Combined integration prepared on `codex/display-scooper`:** the latest Wi-Fi
scooper branch is the base. `firmware/esp32-companion/bullseye_companion/`
contains one generated `.ino` for the same S3's animated display, BLE telemetry
and GPIO39/40 scooper **over one BLE connection owned by Mission Control**.
The combined sketch no longer joins Wi-Fi; the Mac's rover Wi-Fi is unchanged.
Stop `send-display.sh`, restart the backend, then Claim controls → Scooper Connect
to connect both functions. The existing `/bench` Scooper panel is retained.
The backend/device use ordered commands so delayed motion cannot overtake STOP;
the device also stops active motion on BLE/control-heartbeat loss. See the
[upload/operator guide](../firmware/esp32-companion/README.md). Compile and
offline tests pass; combined hardware upload/testing remain operator work.
The combined PWM adapter now checks LEDC attach/write results, avoiding the old
immediate-frequency-readback startup race. Actual PWM faults disable both servo
outputs while BLE/display telemetry stay connected; the UI and backend disable
motion separately. Native delayed-readback and failed-output tests cover this.
BLE heartbeat/action writes are now serialized per characteristic, including
bounded draining of canceled in-flight writes, fixing a CoreBluetooth write
future collision. Watchdog history and idle-disconnect reporting are corrected;
the two-second stop timeout is unchanged. The UI shows heartbeat diagnostics and
labels its diagram as commanded positions. See [physical scooper tests](SCOOPER_TEST.md).
Latest operator follow-up: both local `set 1` (left) and `set 2` (right) now
physically work at 80°, 90° and 110°. An earlier right-output problem resolved
after troubleshooting, but the operator is unsure which adjustment fixed it;
do not claim a confirmed root cause. Both small-range local command paths have
passed. The operator subsequently reported both servos passing the full
individual range tests with unchanged boot IDs. Simultaneous BLE-controlled
Squeeze still disconnects; it is not physically verified as a complete mission
action. Servo supply is from the S3/USB 5V pin via a breadboard split, sharing
the battery with the display/S3. The operator then confirmed **BLE stays
connected through Squeeze with both servos completely disconnected**. That
isolates the failure to conditions involving the attached servos rather than
the unloaded command sequence; power delivery/noise/wiring under load are
leading suspects, not measured/proven electrical diagnoses. Source voltage,
current rating and available separate servo supply are being clarified before
a power-path isolation test. Do not join separate positive supplies or backfeed
a USB-powered S3 header.
Latest signal-isolation follow-up: the operator reports that either attached
servo causes display flickering even without BLE, and disconnecting just the
signal wires clears it. Reported boot IDs remain unchanged in serial checks;
a full S3 reboot is therefore not established. Power remains shared through a
breadboard, with supply voltage/current rating unknown. The operator moved
signals to GPIO39/40; the shared configuration and generated companion sketch
now assign **left=39, right=40**. Upload and physical verification are pending;
this pin change is a diagnostic test, not a confirmed fix. Avoid GPIO35–37
(octal memory) and GPIO47/48 (1.8 V logic on the N32R16V module).
The vision baseline work was saved separately on `codex/vision-baseline`,
commit `70c528c`, before switching to this integration. Vendor firmware and
the original standalone display/scooper sketches remain preserved.

**`main`** has all of PRs #1–#10:

| PR | What |
|---|---|
| #1 | Laptop vision app (`software/vision/main.py`) |
| #2 | Floor free-space check, navigation planner (dry run), color classifier, ground stop line, calibrated steps |
| #3 | BLE telemetry display (ESP32-S3) |
| #4 | **Vision inside Mission Control:** overlay, vision panel, browser calibration; `software/vision` became the `bullseye_vision` package |
| #5 | Camera color settings sent from Mission Control; **rover mask** (ignore the gripper) |
| #6 | Stop distance can be 2 cm (stick-tip zero point) |
| #7, #9 + calibration commits | Saved arena calibration: camera preset, gripper mask, step table |
| #8 | "Clear all samples" button |
| #10 | **Color gate:** floodlit floor and walls no longer read as minerals |

**Not merged:**
- **`origin/feature/scooper`** (Luke, 1 commit, no PR):
  - **What it adds:** ESP32 firmware for two SG90 gripper servos, a `scooper.py` server link over USB
    serial (adds the **pyserial** dependency), and a Squeeze/Release/Straight-down panel on
    `/bench`. Its own commit says "Not yet compiled for the ESP32 or run on hardware".
  - **Checked 2026-11-08:** it **merges cleanly** with `main`, and the trial merge passes everything
    (server 141, vision 94, web 52, type check and build).
  - **⚠️ Not merged because** it uses the **same ESP32-S3 board as the BLE display** (GPIO41/42).
    Flashing one sketch replaces the other. **The team must decide** which board does what (or
    combine them) before merging or flashing.

**Local-only files on Alex's Mac** (not committed, on purpose):
- `software/vision/color_model.json`: the latest color model, fitted **before** the color gate
  existed. Refit after restarting the server (§8), then commit it.
- `software/vision/captures/samples.npz`: the current color samples (gitignored). The old samples
  are saved as `captures/samples_2026-11-08_before-camera-settings.npz`.
- `software/vision/config_room_light.json`: a personal backup from before the floodlight. Ignore it.
- `~/Projects/Bullseye-mc`: an old worktree of faiyaz's branch, now redundant (everything is on
  `main`).

## 4. Hardware facts (read from the firmware source, then partly confirmed on hardware)

**Camera** (`firmware/esp32-camera/.../app_httpd.cpp`, `CameraWebServer_AP.cpp`):

- **Wi-Fi:** an open access point named `ELEGOO-<chip id>`; the team's rover is `ELEGOO-2436CBF61B44`.
  The camera's IP is `192.168.4.1`. The access point has no internet.
- **Port 80:** `/` web UI, `/status` JSON, `/control?var=&val=` runtime sensor settings (RAM only,
  reset on power-off), `/capture`, `/Test` (also a stream).
- **Port 81:** `/stream`, which is MJPEG. Default **800×600 SVGA**, JPEG quality 10. **The first
  frame has no boundary line**, so parse by `Content-Length` or by the JPEG start/end markers.
- **One stream client at a time.** A browser tab on 192.168.4.1 or the ELEGOO phone app blocks
  everyone else.
- **Face detection:** opening the stream forces it on, but it only runs on frames ≤ 400 px wide.
  If you ever lower the resolution, send `face_detect=0` after each stream connect.

**Commands: TCP `192.168.4.1:100`.** The ESP32 relays brace-framed JSON to the UNO and relays the
UNO's replies back (`firmware/arduino-uno/.../ApplicationFunctionSet_xxx0.cpp` around line 1810):

| Command | Meaning | Notes |
|---|---|---|
| `{"N":2,"D1":dir,"D2":pwm,"T":ms,"H":id}` | Timed move: dir 1 = left, 2 = right, 3 = forward, 4 = backward | **The safe way to move**: it stops by itself after T ms. faiyaz's bridge uses T = 300 ms |
| `{"N":3,...}` / `{"N":4,"D1":L,"D2":R}` | Move with **no time limit** (N4 = left/right PWM, forward only) | Avoid unless there's an independent timeout |
| `{"N":100}` | STOP (replies `{ok}`) | **Also turns the RGB LEDs off**. Mind this after the "collected" LED is set |
| `{"N":21,"D1":2,"H":id}` | Ultrasonic distance → `{id_<cm>}` | Blocks inside the UNO while it measures. `0` means no echo (NOT clear space); `150` is the clamp (150+ cm). Reads short and is uncalibrated |
| `{"N":22,"D1":0,"H":id}` | Cached IR value | faiyaz uses it as a fast acknowledgement after each N2 |
| `{"N":8,"D1":0,"D2":r,"D3":g,"D4":b}` | Set all RGB LEDs (N7 = timed) | The bridge sends this only with a preceding acknowledged STOP |
| `{"N":5,"D1":1,"D2":angle}` | Pan servo | The firmware divides the angle by 10 and clamps it, so it moves in 10° steps, about 10–170° |

- **Heartbeat:** the ESP32 sends `{Heartbeat}` every second. Echo it back, or after about 4 s the
  ESP32 drops the client and sends STOP. Disconnecting also sends STOP.
- **Shield switch:** it must be on **Cam**, with USB unplugged, for Wi-Fi control.
- **Lift-off detection** (IR sensors) only gates the built-in tracking, obstacle and follow
  modes, not these commands.

**⚠️ Firmware trap (found in the UNO source, not yet tested on hardware):** sending a **servo (N5)
or LED (N7/N8)** command while a timed N2 move is running switches the UNO's mode **without
stopping the motors**, and the move's auto-stop timer is lost: **the wheels keep turning**. Send
servo and LED commands only while stopped.

**Measured on hardware:**
- faiyaz:
  - the TCP bridge works and STOP replies `{ok}`
  - distance replies match their requests
  - forward is the right direction, and driving from the browser works on the ground
- Alex:
  - **camera:** ~15–16 FPS at 800×600 earlier, and **~8 FPS** through Mission Control with the
    floodlight
  - **step table** (PWM 60, averages of 5): forward **2.7 / 7.5 / 15 cm** and turn
    **8.3 / 20.9 / 49°** for **0.2 / 0.5 / 1.0 s**

## 5. Architecture now

```
ESP32 camera ──MJPEG──► Mission Control server (FastAPI, Python 3.13)
                          ├─ camera.CameraHub ── the ONLY camera client ──► /ws/video (browser)
                          ├─ vision.VisionService (worker thread, newest frame):
                          │    camera preset → palette profiles
                          │    → rover mask → detection → floor check → planner DRY RUN
                          │    ──► /ws/vision (overlay) + state.vision (panels)
                          ├─ bench_demo.FactoryBridge ── the ONLY port-100 client (/bench manual driving)
                          ├─ autopilot.Autopilot ── explicit /bench scan + approach + manual handoff
                          └─ mission.MissionControl (state machine, Simulator / HardwareStub adapters)
Browser dashboard (/ and /bench): video + vision overlay, Vision panel, Calibration panel
Separate: BLE sender reads the cached /api/bench/state → ESP32-S3 display
```

**Current `/bench` sample mission:**
1. Claim controls, connect the bridge, and enable controls. Select red or green ore.
2. Start the search. The rover scans through a measured 360° turn. If absent, it selects
   a nearby sonar-clear heading, relocates a bounded distance, and repeats the scan.
3. On a stable target, align and approach using fresh sonar to about 10 cm. Stop and disable
   motion; green LEDs and the happy display signal arrival.
4. Pick up the material by hand and confirm pickup. Carry it to home base and confirm delivery.
   Manual driving can be enabled separately after pickup if the rover also needs moving.

Blue is an obstacle, never an ore destination. Yellow remains calibratable but is not a mission
selection. There is no automatic pickup or home return. See [current limits](AUTONOMY_PLAN.md).

Each move uses a bounded N2 pulse with STOP before and after, followed by fresh camera frames.
Obstacle avoidance is always enabled. The search timeout defaults to 120 seconds and is bounded
between 30 and 300 seconds. Arrival requires three fresh centered observations with STOP
acknowledgements. A remaining gap smaller than a safe timed step reports stopped-short rather
than falsely declaring arrival. STOP, lost pilot ownership, or connection loss cancels motion.
Vision settings cannot be changed during an active bench operation.

The home flag's visual size does not prove arrival: home approach needs valid sonar. Small minerals
may sit below the sonar beam, so camera arrival is an estimate that still needs measured arena
acceptance. This code has no map, reliable global position, coverage guarantee, or automatic gripper.
See [AUTONOMY_PLAN.md](AUTONOMY_PLAN.md) for the next implementation stages.

## 6. Current vision and persistence

The primary detector is `classifier.mode: "palette"`. Each material has up to eight small profiles;
new accepted examples add variations rather than continually widening one range. The first example
replaces that material's built-in profile. Floor and cardboard examples compete with objects, and
ambiguous pixels can remain unknown. Home has an additional fixed near-black gate. See
[SIMPLE_VISION.md](SIMPLE_VISION.md) for exact rules and the calibration workflow.

The authoritative operator file is **`software/vision/config.json`**, or `config.json` inside
`BULLSEYE_VISION_DIR` when configured. It stores `palette.profiles`, rover polygons and older mask
boxes, camera controls, and measured navigation steps. Each successful calibration command saves
immediately through an atomic replacement. Revision checks prevent a stale second process from
clobbering newer data. `color_model.json` and sample archives are retained for legacy comparisons;
there is no Fit step in palette mode.

The camera's own settings are RAM-only. Mission Control sends the saved preset when streaming starts
and after a gap of at least three seconds. Confirm that application succeeds after a rover restart.
Revisit colors if lighting or camera settings change; revisit the mask if framing or rover geometry
changes. Reboots alone do not require resampling.

Calibration uses exact frozen PNG pixels and a server snapshot ID. Click a material chip, click or
box a solid part, then return to live video. For the rover mask, trace each visible claw separately
and keep the opening clear. Masks exclude every material, including home and blue tape.

## 7. How to run (Alex's Mac is fully set up; others follow `docs/MISSION_CONTROL.md`)

- **Real rover** (camera, `/bench` driving, vision):
  - Join `ELEGOO-2436CBF61B44`, then run:
    ```
    ./mission-control/run-rover-demo.sh
    ```
  - Open http://localhost:8001/bench, then **Claim controls** → **Calibrate**.
- **Simulation** (no hardware; vision runs on simulated rocks): `./mission-control/run.sh`, then
  http://localhost:8000.
- **After `git pull`:**
  ```
  cd mission-control/server && .venv/bin/python -m pip install -r requirements.lock.txt -r requirements-ble-macos.lock.txt -e '.[ble,test]'
  cd ../web && npm run build
  ```
  Then **restart the server**.
- **BLE display:** `mission-control/send-display.sh` (see `docs/BLE_DISPLAY.md`).
- **Only one Mac at a time** may run the controller: camera, rover bridge and display each accept
  one client.

## 8. Current acceptance work

The existing operator camera preset, color examples, and mask geometry are preserved. Do not reset
or replace them just because the server or rover restarted.

1. Check the camera preset status and confirm the saved material sample counts and rover mask.
2. Check an empty arena for false colored objects; add floor/cardboard examples only where needed.
3. Check all four object colors at near and far distances, and the actual black flag against nearby
   shadows. Confirm no rover pixels are being detected.
4. Verify measured movement steps and a stop line against the physical stick-tip zero point. Keep
   sonar distance and camera/ground distance distinct until their mounting offset is measured.
5. Validate STOP and interrupted-run behavior with wheels raised, then short approaches in a clear
   arena. Measure the final physical gap; do not infer it from an ARRIVED label.
6. Verify stopped ore arrival triggers green LEDs and the happy display, followed by automatic home
   reacquisition from several headings. Keep green during return and reset red at home. Missing or
   invalid sonar must prevent a home forward approach.
7. Follow the staged [exploration plan](AUTONOMY_PLAN.md) before extending this into full missions.

The automated results for this integration belong in [INTEGRATION_PR.md](INTEGRATION_PR.md).
They do not certify physical stopping, obstacle avoidance, or exploration coverage.

## 9. Earlier classifier lessons and continuing camera gotchas

- **Nothing detected under the floodlight?** Three causes, all fixed:
  - colors were washed out (the red ball measured saturation 43–83), fixed by the camera preset
  - the model was never fitted (there's now a warning)
  - the gripper's blue tape was in view, fixed by the rover mask
- **"Unknown vision command" / mask boxes not saving / camera sliders doing nothing:** the server
  is running older code than the web page. Restart it. Check with
  `curl -s localhost:8001/api/vision/state`: it must contain `camera` and `rover_mask` keys.
- **Dark video in the dashboard** is usually just **Mask preview** being on: it dims everything
  except matched pixels.
- **A good overlap table isn't proof.** It only compares your samples with each other. Always do
  the empty-arena check (step 2 above).
- **Paper balls can be below the ultrasonic beam.** Camera arrival then remains an estimate.
  The current approach also uses near sonar readings when available; home arrival requires sonar.

## 10. Practical gotchas

- **Install dependencies while online.** Joining the ELEGOO network usually drops internet unless
  there's a second network (Ethernet or USB tethering).
- **macOS keeps switching away.** Turn off Auto-Join for other Wi-Fi networks. Terminal may need
  **Local Network** and **Bluetooth** permission.
- **Close other camera viewers** before running anything: browser tabs on 192.168.4.1, the ELEGOO
  app, the standalone vision app.
- **Safety:** test new motion with the wheels raised and keep the physical power switch reachable.
  Software STOP acknowledgements don't prove the wheels stopped.
