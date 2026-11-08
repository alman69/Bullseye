# Bullseye team handoff (for teammates and their AI assistants)

Snapshot written **2026-11-07** by the vision owner (Alex). It describes the project state at
`main` = `f65d3b0` plus the open vision PR. Things change fast during the hackathon:
**check `git log` and open PRs before trusting any status below.**

## Ready-to-paste prompt for your AI assistant

> You are helping build **Bullseye**, an autonomous "Mars rover" for UB Hackathon 2026
> (24-hour event, team of three) on the ELEGOO Smart Robot Car V4.0. Repository:
> `git@github.com:alman69/Bullseye.git`. Before writing code, read `docs/HANDOFF.md`
> (this file), `docs/MISSION_CONTROL.md`, `docs/ELEGOO_CONNECTION.md`,
> `docs/FIRMWARE_SETUP.md`, and, if it is merged or you are on branch
> `feature/vision-phase1`, `software/vision/README.md`. Then inspect the code you
> will touch.
>
> **Rules:**
> - Keep solutions simple, because working functionality beats architecture.
> - Do not modify or flash the vendor firmware in `firmware/` unless the team explicitly agrees.
> - Do not add YOLO/ML models.
> - The ESP32 serves **one** camera stream client and **one** port-100 command client, so
>   never open a second connection from new code. Reuse the existing owners (see
>   "Single-owner rule").
> - Never claim a physical rover or camera test passed unless a human ran it and reported
>   the result.
> - Propose a plan and wait for your human's approval before large changes.
> - The repo is **public**: never commit secrets, `.env`, or camera snapshots of people.
> - Work on a branch and open a PR. Coordinate before editing another teammate's area.

## 1. Mission

1. A user picks a target rock color in the locally hosted Mission Control website.
2. The rover searches for that color with its onboard camera.
3. It drives to the correct rock while avoiding obstacles.
4. At the rock it stops and asks Mission Control to confirm the sample.
5. After confirmation, the rover's RGB LEDs show the rock's color.
6. It returns to its home base (manual assistance allowed).

**Hardware:** Arduino UNO R3, ESP32-WROVER camera (Wi-Fi access point), TB6612 motor driver,
MPU6050, ultrasonic sensor and camera on one pan servo, and RGB LEDs.

**Architecture:** the laptop does all vision and decision-making. The ESP32 streams video and
relays JSON commands to the UNO over UART. The UNO runs the motors and sensors. Firmware is the
unmodified ELEGOO factory code.

**Arena:** white floor, unpainted brown cardboard walls, painted mineral blocks, and colored
obstacles. A floodlight will be added later. See the palette in §8.

## 2. Who owns what

| Area | Owner | Where |
|---|---|---|
| Computer vision / color detection | Alex (GitHub `alman69` repo owner) | `software/vision/` on branch `feature/vision-phase1`, PR #1 |
| Mission Control website + server, rover command bridge, manual driving | faiyaz | `mission-control/`, `docs/MISSION_CONTROL.md`, `docs/ELEGOO_CONNECTION.md` (on `main`) |
| Third teammate | not recorded here | `docs/ELEGOO_CONNECTION.md` mentions planned "teammate's firmware work" for an independent UNO motion timeout. **Confirm who owns that and whether flashing is allowed** |

## 3. Repository and branch status

| Ref | Contents |
|---|---|
| `main` (`f65d3b0`) | Vendor firmware + docs (`5e7db7d`), then three commits by faiyaz: Mission Control dashboard, simulator and camera relay; real-rover manual control demo (`/bench`); live ultrasonic polling |
| `feature/vision-phase1` → [PR #1](https://github.com/alman69/Bullseye/pull/1) | Laptop vision app. **Not merged.** It conflicts with `main` in `.gitignore` only (both sides added Python ignores); resolve by keeping both blocks without duplicates |

The old repository (`alman69/RoFinder`) was deleted, so ignore any reference to it or to its
"PR #2".

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

**Hardware results so far (reported by humans):**
- faiyaz: TCP bridge, STOP `{ok}`, matched distance replies, forward direction, and browser
  driving on the ground all work.
- Alex: live camera at **~15–16 FPS at 800×600**, color detection accepted, and auto-reconnect
  after two unprompted Wi-Fi stalls. Details are in `software/vision/README.md` §9.

## 5. Vision subsystem (`software/vision/`, branch `feature/vision-phase1`)

**Stack:** Python + OpenCV + NumPy, using HSV thresholds (no ML). Verified on macOS with Python
3.9.6, opencv-python 4.10.0.84 and numpy 2.0.2. `requirements.txt` has markers that pick numpy ≥ 2.1
on Python ≥ 3.13.

- **`camera.py`:**
  - `CameraStream` is the single MJPEG reader. It keeps only the newest frame, reconnects with
    backoff, and treats a stall longer than `timeout_s` (3 s) as a disconnect.
  - `wait_for_frame(after_id)` / `latest()` return a `Frame` with `image` (read-only BGR), `jpeg`,
    `frame_id`, `received_at` and `received_wall`.
  - `FileSource` replays saved images or video through the same interface.
- **`detector.py`:** `ColorDetector.from_config(cfg).detect(frame_bgr, "red") -> Detection`, with no
  GUI.
  - Pipeline: blur → HSV → mask (if `h[0] > h[1]` the hue wraps, as for red) → open/close →
    contours.
  - Filters: `min_area_px`, `max_area_ratio`, `min_extent`.
  - **Best candidate = the largest passing contour.**
- **Colors:** red `h[170,10]`, yellow `h[20,35]`, green `h[40,85]`, blue `h[95,130]`. These are
  defaults; calibrate them in the arena.
- **`Detection.to_dict()`:** `target_color, detected, center_x, center_y, frame_width,
  frame_height, horizontal_error (-1 = left edge … +1 = right edge), area, area_ratio, bbox,
  direction (LEFT/CENTER/RIGHT/NONE), candidates, frame_id, timestamp, processing_ms`.
  - **`area` is not distance.** The ultrasonic sensor verifies proximity.
  - There is no confidence field.
- **`main.py`:**
  - Test GUI with keys `r/y/g/b` to pick the color, `c` to toggle the HSV slider calibration and
    `w` to save it to `config.json`, `s` for a snapshot to `captures/` (gitignored), click for the
    HSV under the cursor, and `q` to quit.
  - `--headless` prints JSON lines; `--source` runs offline on saved frames.
- **Metrics:**
  - stream FPS, processing FPS, detect ms, and frame age since it was received on the laptop
  - camera-to-laptop latency is **not** measured (the firmware sends no capture timestamp)
- **Tests:** 32 unittest cases (`python -m unittest discover -s tests` in `software/vision`)
  using synthetic images and a fake ESP32 server.

**Known vision issues:**
- An object with dark printing on it can split into two masks, which skews LEFT/RIGHT.
- Brown cardboard's hue (about 10–20) sits between red and yellow. Raise **S low** if cardboard
  triggers false detections.

## 6. Mission Control (`mission-control/`, by faiyaz; full details in `docs/MISSION_CONTROL.md`)

- **Server:** FastAPI on **Python 3.13+**. The web app uses React/TypeScript/Vite on **Node
  22.12+**.
  - `./mission-control/run.sh` serves the dashboard at `http://localhost:8000` (simulation by
    default).
  - `run-rover-demo.sh` serves the real-rover manual page at `http://localhost:8001/bench`.
  - The docs use absolute paths like `/Users/faiyaz/Bullseye/...`; adjust them to your own checkout.
- **`mission.py`:** the mission state machine:
  idle → searching → approaching → awaiting_confirmation → collecting → collected → returning →
  completed, plus a latched STOP. There is one pilot seat, and anyone can press STOP. A frozen
  review snapshot is shown for confirmation.
  - **Colors are red/green/blue only. Yellow still needs adding here and in `web/lib/protocol.ts`.**
- **`adapters.py`:** the `RoverAdapter` protocol is **the integration point for navigation and
  vision**.
  - Methods: `search(color)` yields `"approaching"` then `"candidate"`; `collect(color)` (LED
    acknowledgement); `begin_return()`; `drive(linear, angular, ttl_ms)`; `stop()`.
  - Implementations: `Simulator`, and `HardwareStub` (all capabilities disabled).
- **`camera.py`:** `CameraHub` is the server's single upstream camera connection. It holds JPEG
  bytes only (no decoding) and publishes at most 10 FPS to `/ws/video`.
- **`bench_demo.py`:** `FactoryBridge` is the single port-100 connection.
  - Moves: N2 timed moves (300 ms), each acknowledged by an N22 reply. The keyboard hold is a
    350 ms lease renewed every 100 ms.
  - STOP: N100 (bypasses the queue).
  - Ultrasonic: N21 at about 2 Hz when idle. Driving pauses for each reading.
- **Tests:**
  - Web: `npm test` (37 passing) and `npm run build` both succeeded on `f65d3b0` on Alex's Mac
    (Node 24).
  - Server: `pytest`. Not re-run by Alex, because that Mac has no Python 3.13.

## 7. Single-owner rule and the planned integration

Because the ESP32 accepts only one client of each kind, the **Mission Control server process
should own both connections**:
- the camera, through `CameraHub`
- commands, through `FactoryBridge`

Navigation and vision should run **inside that process**:
- decode `CameraHub.latest` with `cv2.imdecode`
- call `ColorDetector.detect`
- drive through the bridge

The standalone vision app (`software/vision/main.py`) is for calibration and testing **when the
server is not running**. Implementing a real `RoverAdapter` that does this is the next big step.
It touches faiyaz's code, so agree on it first.

Integration to-dos:
- Add `opencv-python` and `numpy` to `mission-control/server/pyproject.toml`.
- Add yellow to the server and web colors.
- Either make the LED color survive STOP (N100 clears it), or re-send N8 after each stop.

## 8. Decisions and proposals (status in brackets)

- **[Decided]** Color detection is accepted for now (red and yellow tested; real red rock still
  pending).
- **[Decided]** Walls stay unpainted cardboard; the floor is white.
- **[Proposed, not built]** Obstacle avoidance uses **the camera first, with the ultrasonic
  sensor as a safety check**:
  - **Floor detection:** low-saturation pixels count as white floor. In 5 vertical strips across
    the bottom half of the image, measure how far up the floor continues. A short strip means
    something is close in that direction (cardboard, boxes, the non-target rock), so steer to the
    strip with the most floor. The target rock's pixels are excluded.
  - **Ultrasonic:** every few moves, stop and read it. Under about 20 cm with the target not
    centered means an obstacle: back up and turn. Under about 20 cm with the target centered and
    large means arrival: stop and request confirmation.
  - **Driving:** reactive steering with N2 moves (turn a little toward the target, or step
    forward). No map or path planning, since there are no wheel encoders.
  - **Arena rules:** no white or very light obstacles, and no tape on the floor.
- **[Proposed, not built]** Sample-based color calibration for the painted blocks:
  - drag a box over each block, several times at different distances and lighting
  - the app computes the HSV ranges, handling red's wrap-around automatically
  - it prints an overlap table against the other colors, the floor and the cardboard
  - after calibrating, consider locking the camera's white balance via `/control`
- **[Open]** Who does the UNO firmware motion timeout, and is flashing allowed?
- **[Open]** Which laptop runs the demo? It needs Python 3.13 and Node 22+ for Mission Control,
  plus the vision dependencies, installed **before** joining the rover's internet-less Wi-Fi.

### Arena color palette [Planned, 2026-11-07]

| Item | Color | Approx. OpenCV hue (0–179) | Notes |
|---|---|---|---|
| Mineral 1 | **Red** block (primary red; not orange-red or pink) | 0–8 and 172–179 | Existing painted block |
| Mineral 2 | **Green** block (kelly/grass green; not lime or teal) | 50–75 | To paint |
| Mineral 3 | **Blue** block (royal/primary blue; not navy or sky) | 105–120 | To paint |
| Obstacles | **Yellow** (lemon; not golden or orange) | 25–32 | Existing yellow block, or yellow crumpled paper (see below) |
| Walls | Plain cardboard, unpainted | ~10–20, medium saturation | Must not be white, or the floor check can't see them |
| Floor | Matte white | any hue, very low saturation | Keep it clean; no tape lines |

Why this palette:
- Red, green and blue are the furthest apart on the hue scale (60 units each) and already match
  Mission Control's colors and the RGB LEDs.
- Yellow is closest to cardboard, but confusing an obstacle with a wall is harmless (both mean
  "avoid").
- With floor-based avoidance, *anything* that is neither floor nor the current target is treated
  as an obstacle, including the minerals not being searched for.

**Avoid** orange or brown (between red, yellow and cardboard), pink, magenta or purple (too close
to red or dark blue), cyan or teal (between green and blue), white or light gray (looks like floor),
and black (looks like shadow).

**Painting:** matte paint, every side the same color, no logos or stripes. Test one patch of each
paint through the camera before painting everything.

**Crumpled-paper obstacles:** they work with the camera, but follow these rules:
- **Saturated colored paper**, colored on both sides (for example yellow construction paper).
  Never white printer paper.
- **Crumple loosely into a rounded lump.** Tight creases create glare spots that look like white
  floor, plus dark shadows that break up the color.
- **At least ~10 cm across.**
- **Weighted inside** (a coin or a small stone) so it doesn't move when bumped or blown.
- **Don't count on the ultrasonic sensor for them:** paper scatters sound and low lumps can sit
  below the beam, so the camera floor check is what avoids them. If a solid echo matters, wrap
  yellow paper smoothly around a small box or cup instead.

**Floodlight:** prefer a neutral or daylight LED (about 5000 K or higher), mounted high and angled
down, never pointing at the camera. Check for flicker banding on video. Do the final color
calibration and any white-balance lock **under the floodlight**, and keep a room-light backup
config (`main.py --config <file>`).

## 9. Practical gotchas

- **Install dependencies while online.** Joining the ELEGOO network usually drops internet unless
  there's a second network (Ethernet or USB tethering).
- **macOS keeps switching away.** Turn off Auto-Join for other Wi-Fi networks. Terminal may need
  **Local Network** permission.
- **Close other viewers and controllers** before running anything: browser tabs on 192.168.4.1,
  the ELEGOO phone app, and other scripts.
- **OpenCV keys only work in the video window.** Focus the video window, not the terminal.
- **Safety:** test new motion with the wheels raised and keep the physical power switch reachable.
  Software STOP acknowledgements don't prove the wheels stopped.
