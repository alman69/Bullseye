# Bullseye team handoff (for teammates and their AI assistants)

Snapshot updated **2026-11-08, about 05:00 EST** (`main` = PR #10 merged, plus this handoff). It covers
everything up to PR #10. Things change fast during the hackathon: **check `git log`, open PRs and
`git branch -r --no-merged origin/main` before trusting any status below.**

## Ready-to-paste prompt for your AI assistant

> You are helping build **Bullseye**, an autonomous "Mars rover" for UB Hackathon 2026
> (24-hour event, team of three) on the ELEGOO Smart Robot Car V4.0. Repository:
> `git@github.com:alman69/Bullseye.git`. Before writing code, read `docs/HANDOFF.md`
> (this file), then `docs/MISSION_CONTROL.md` (especially "Vision (built in)"),
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
> - Propose a plan and wait for approval before large changes.
> - The repo is **public**: never commit secrets, `.env`, or camera snapshots
>   (`software/vision/captures/` is gitignored).
> - Work on a branch and open a PR. The team co-owns all code; merging after tests pass is fine.
> - **After pulling code, restart the Mission Control server.** A running server keeps its old code
>   while the browser loads the new pages, which makes new features fail with
>   "Unknown vision command".

## 1. Mission and the current arena

**Mission:**
1. A user picks a target color in Mission Control.
2. The rover searches with its camera, drives to the mineral while avoiding obstacles, and stops.
3. Mission Control confirms the sample.
4. The RGB LEDs show the color.
5. The rover returns home (manual help allowed).

**Arena (as of 2026-11-08):**
- **Floor and walls:** matte white floor; unpainted cardboard walls.
- **Minerals:** crumpled **paper balls** in red, green and blue. They sit **below the ultrasonic
  beam**.
- **Obstacles:** a **yellow (lime) sheet**.
- **Lighting:** the **floodlight is always on**.
- **Gripper:** the rover now has a **popsicle-stick gripper** in front, and its two claws (blue/black
  tape) are always in the bottom of the camera view. **All distances are measured from the
  stick tips** (the zero point).

**Hardware:**
- Arduino UNO R3, ESP32-WROVER camera (Wi-Fi access point), TB6612, MPU6050, ultrasonic sensor and
  camera on one pan servo, RGB LEDs.
- A separate **ESP32-S3 touchscreen** shows telemetry over BLE (`docs/BLE_DISPLAY.md`).
- Luke's unmerged branch drives two **SG90 servos** for the gripper from that same S3 board (§3).

## 2. Who started what (everyone may change anything)

| Area | Started by | Where |
|---|---|---|
| Vision (detector, classifier, floor check, planner) and vision-in-Mission-Control | Alex (`alman69`, repo owner) | `software/vision/`, `mission-control/server/bullseye/vision.py`, `mission-control/web/components/vision-*` |
| Mission Control (dashboard, server, `/bench` manual driving, rover bridge), BLE display | faiyaz | `mission-control/`, `firmware/esp32-display/`, `docs/MISSION_CONTROL.md`, `docs/ELEGOO_CONNECTION.md`, `docs/BLE_DISPLAY.md` |
| Gripper "scooper" (SG90 squeeze) | Luke Coppola | branch `feature/scooper` (not merged, §3) |

## 3. Repository status

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
| `{"N":8,"D1":0,"D2":r,"D3":g,"D4":b}` | Set all RGB LEDs (N7 = timed) | Not yet in the Mission Control bridge |
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
                          │    camera preset → white balance → color classifier + color gate
                          │    → rover mask → detection → floor check → planner DRY RUN
                          │    ──► /ws/vision (overlay) + state.vision (panels)
                          ├─ bench_demo.FactoryBridge ── the ONLY port-100 client (/bench manual driving)
                          └─ mission.MissionControl (state machine, Simulator / HardwareStub adapters)
Browser dashboard (/ and /bench): video + vision overlay, Vision panel, Calibration panel
Separate: BLE sender reads the cached /api/bench/state → ESP32-S3 display
```

**What isn't built yet:** **autonomous driving.** The planner only reports the move it *would*
make. Nothing in vision sends motor commands.

## 6. Vision details (`software/vision/bullseye_vision` + `mission-control/server/bullseye/vision.py`)

All calibration lives in **`software/vision/config.json`** and **`color_model.json`**, shared by
the standalone app and Mission Control. The dashboard writes them.

- **Camera preset** (`camera.controls`): **saturation +2, exposure (`ae_level`) −2,
  brightness −2, contrast 0**.
  - Sent to the ESP32's `/control` when video starts and again after any gap of 3 s or more (camera
    restart).
  - These are RAM-only settings, not firmware, and the camera's own web page can't save them.
  - **Any camera change means resampling the colors.**
- **Rover mask** (`rover_mask.boxes`, 9 boxes over the claws): ignored for every color, and
  treated as floor by the floor check.
- **Color classifier** (`colorclass.py`):
  - **Classes:** red, green, blue and yellow minerals, plus floor and cardboard (an "obstacle"
    class exists but isn't sampled).
  - **Fitting:** one Lab Gaussian per class, learned from drag-box samples, plus white balance
    referenced to the floor color.
  - **Color gate (PR #10):** a pixel only counts as a mineral if its color points far enough in that
    mineral's direction (above the most-tinted floor or cardboard sample, ≥ `min_chroma` 12, and
    cutting ≤ 20% of the mineral's samples).
    - **Why:** under the floodlight the floor is tinted (Lab chroma 18–28; warm far away, bluish
      near the claws), which caused false red, blue and green.
    - **Result on real data:** zero false detections on an empty frame, while the balls keep
      85–100% of their pixels.
- **Fit only takes effect when "Fit color model" is clicked;** the Colors tab warns if that hasn't
  happened. "Clear all samples" exists for resampling after a camera change.
- **Floor check** (`freespace.py`): in 5 strips, how far up the floor reaches; it uses the
  classifier's floor class when there is one, otherwise HSV (`floor.s_max` 45).
- **Ground / stop line** (`ground.py`): block captures at known distances give a mapping from bottom
  edge to distance and the stop line. **Not calibrated yet.**
- **Planner** (`planner.py`, dry run):
  - **States:** SEARCH / APPROACH / AVOID / EXPLORE / ARRIVED / GIVE_UP.
  - **Steps:** each step is one N2 timed move, sized from the step table and the field of view.
  - **Arrival** is camera-based: the block's bottom edge reaches the stop line, or it slips out of
    the bottom of the view while centered at the line. The ultrasonic sensor is only a wall
    backstop, ignored near the target.
  - **Turns:** search and avoid turns are 1000 ms (≈49°).
- **Tests:** vision 94, server 129, web 48 pass on `main`. Tests use built-in defaults
  (`tests/support.py`), never the arena calibration files.

## 7. How to run (Alex's Mac is fully set up; others follow `docs/MISSION_CONTROL.md`)

- **Real rover** (camera, `/bench` driving, vision):
  - Join `ELEGOO-2436CBF61B44`, then run:
    ```
    ~/Projects/Bullseye/mission-control/run-rover-demo.sh
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

## 8. Calibration status and next steps

**Done:**
- the step table
- the camera preset
- the gripper mask
- color samples, with a model fitted **before** the gate

**Next, in order** (dashboard Calibration panel, about 20 minutes):
1. **Restart the server** so it runs PR #10, then **3 Colors → Fit color model**. No resampling
   needed; this adds the gates.
2. **Empty-arena check:** with no balls in view, the red, green, blue and yellow chips must each say
   **NOT IN VIEW**. If one doesn't, turn on "Mask: target" to see where, add 2 floor or cardboard
   boxes there, and fit again.
3. **Ball check:** each ball at **30 cm and 1 m** must show **DETECTED**. If far balls are missed,
   set Tuning → Min block size to about 80.
4. **Stop line:**
   - Red ball **centered**, measured from the **stick tips**: capture at 3, 10, 20 and 30 cm, then
     Fit.
   - Tuning: set Stop distance to about **3 cm** and Min block size to about **80**.
5. **Field of view:** ball at 30 cm, centered, then 10 cm to the side → Compute.
6. **Dry run** (push the rover by hand, target red):
   - **B:** ball 1 m ahead → FORWARD "About X cm to go"
   - **C:** ball at the edge → TURN toward it
   - **D:** ball at the stop line → ARRIVED
   - **E:** obstacle in between → AVOID
7. **Commit** `color_model.json` and `config.json` once checks 2–6 pass.

**Then, the big remaining work:**
- **A real `RoverAdapter`** (autonomous driving): execute the planner's moves through
  `FactoryBridge` as N2 timed steps (servo and LED only while stopped). Add the ultrasonic wall
  backstop. On ARRIVED: stop, request confirmation, then pick up with the scooper.
- **Set the LED after confirmation.** N100 STOP clears the LEDs, so re-send N8 after stops, or
  stop another way.
- **Decide the ESP32-S3 board** (display vs. scooper), then merge `feature/scooper`.

## 9. Lessons from 2026-11-08 (save yourself the time)

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
- **The ultrasonic sensor can't see the paper balls** (they're below the beam), so arrival is
  camera-only, using the stop line.

## 10. Practical gotchas

- **Install dependencies while online.** Joining the ELEGOO network usually drops internet unless
  there's a second network (Ethernet or USB tethering).
- **macOS keeps switching away.** Turn off Auto-Join for other Wi-Fi networks. Terminal may need
  **Local Network** and **Bluetooth** permission.
- **Close other camera viewers** before running anything: browser tabs on 192.168.4.1, the ELEGOO
  app, the standalone vision app.
- **Safety:** test new motion with the wheels raised and keep the physical power switch reachable.
  Software STOP acknowledgements don't prove the wheels stopped.
