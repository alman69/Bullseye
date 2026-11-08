# Bullseye laptop vision (Phase 1)

Laptop-side color-target detection (red, yellow, green, blue) for the ELEGOO Smart Robot Car V4.0's
ESP32-WROVER camera. It uses Python, OpenCV and NumPy: HSV thresholds, no
machine learning. It does **not** drive motors, does **not** serve Mission
Control, and does **not** change or flash any firmware.

| File | Purpose |
|---|---|
| `camera.py` | `CameraStream`: the **single** connection to the ESP32 MJPEG stream (background thread, newest frame only, auto-reconnect). `FileSource`: same interface for saved images/videos. |
| `detector.py` | `ColorDetector.detect(frame, color) -> Detection`. Pure image processing, no GUI. |
| `config.py` / `config.json` | Stream URLs, detector filters, HSV ranges (calibration is saved here). |
| `main.py` | OpenCV test GUI (overlay, color keys, calibration, snapshots) and `--headless` JSON mode. |
| `tests/` | Automated tests with synthetic images and a fake local ESP32 stream server. |

## 1. Install (do this while you still have internet)

The ESP32 Wi-Fi has no internet, so install everything first. These steps were checked on macOS with
the system `python3` (3.9.6).

```bash
cd ~/Projects/Bullseye/software/vision
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

The last command should end with `OK`. Every later command below runs from `software/vision/`.

## 2. Connect to the rover's Wi-Fi (macOS)

1. Power the rover on. The ESP32 creates an **open** network named `ELEGOO-` + chip ID
   (e.g. `ELEGOO-1A2B3C4D5E6F`), on channel 9, at IP `192.168.4.1`.
2. Menu bar → Wi-Fi → pick the `ELEGOO-…` network.
3. macOS may jump back to a network with internet. In **System Settings → Wi-Fi → (i)** next to
   your usual network, turn off **Auto-Join** while testing.
4. Check that the camera answers:
   ```bash
   curl -s http://192.168.4.1/status
   ```
   It should print JSON with `"framesize"`, `"quality"`, … Please send us that output once
   (it tells us which framesize numbers this firmware uses).
5. **Only one viewer at a time.** The ESP32 stream server serves a single client. Close any browser
   tab on `192.168.4.1` and the ELEGOO phone app before you start Python.

## 3. Run

```bash
.venv/bin/python main.py                 # live camera, target = red
.venv/bin/python main.py --color blue    # start on blue
.venv/bin/python main.py --calibrate     # open the calibration window too
```

| Key | Action |
|---|---|
| `r` `y` `g` `b` | select red / yellow / green / blue target |
| `c` | toggle the calibration window |
| `w` | write the current HSV ranges to `config.json` |
| `s` | save a snapshot to `captures/`: `_raw.jpg`, `_annotated.jpg`, `_result.json` |
| left-click | show the HSV value of that pixel (`p` clears it) |
| `n` | next image (offline folder mode) |
| `q` / `Esc` | quit |

**Overlay:** target color and DETECTED or LOST (with time since lost). The selected target
gets a thick box and a center dot. Other regions of the same color that pass the filters get thin
gray boxes. You also see the frame center line plus the CENTER dead zone (gray lines), the
LEFT/CENTER/RIGHT direction and `horizontal_error`, the area in px and % of the frame, the
metrics (see §6), and a red **CAMERA RECONNECTING** banner while the stream is down.

**Offline mode** (no rover needed) runs the same detector on saved frames:

```bash
.venv/bin/python main.py --source captures/                       # folder of images, n = next
.venv/bin/python main.py --source captures/20261107-120000-000_red_raw.jpg
```

**Headless mode** (no windows) prints one JSON line per processed frame:

```bash
.venv/bin/python main.py --headless --color green --duration 30 > green_run.jsonl
```

## 4. Calibrate colors with the real rocks

Lighting changes colors a lot, so calibrate in the actual arena light.

1. `.venv/bin/python main.py --calibrate`, put one rock in view, press its key (`r`/`y`/`g`/`b`).
2. **Left-click the rock** in the main window a few times. Note the `HSV at (...)` numbers.
3. In the **Calibration (mask)** window, move the sliders until **only the rock** stays visible:
   - **H low / H high**: hue band (OpenCV hue is 0–179). For **red**, set H low *greater* than
     H high (e.g. 170 and 10). That means "170..179 plus 0..10", because red sits at both ends
     of the hue scale.
   - **Yellow** is around H 20–35. Keep yellow's H low **above** red's H high (defaults: red ends at 10,
     yellow starts at 20) so an orange-ish patch is never counted as both.
   - **S low**: raise it to drop gray/brown cardboard (low saturation).
   - **V low**: raise it to drop dark shadows. Lower it if the rock vanishes in shadow.
4. Check the other rocks and the empty arena: they must stay black for this color.
5. Repeat for each color, then press **`w`**. `config.json` is updated and the terminal prints the saved values.
   Values persist across runs. Commit `config.json` if you want to share them.

Other settings in `config.json` → `detector`:

| Key | Default | Meaning |
|---|---|---|
| `min_area_px` | 400 | smaller regions are ignored (noise, very far rocks) |
| `max_area_ratio` | 0.6 | regions bigger than 60% of the frame are ignored (walls, floor, lighting) |
| `min_extent` | 0.3 | contour area / bounding-box area; rejects thin streaks and edges |
| `blur_kernel` / `morph_kernel` | 5 / 5 | noise reduction (odd numbers; 0 disables) |
| `center_deadband` | 0.12 | `abs(horizontal_error)` ≤ this → `CENTER` |

`camera` → `rotate` (0/90/180/270) fixes an upside-down mount. `camera` → `controls` is an
optional dict of runtime sensor settings sent to `http://192.168.4.1/control` after each connect,
e.g. `{"awb": 0}` to lock white balance after calibration. These settings live in ESP32 RAM only
and reset on power-off, so the firmware is never changed. Leave it `{}` unless you need it.

## 5. Output for navigation

```json
{"target_color": "red", "detected": true, "center_x": 412, "center_y": 300,
 "frame_width": 800, "frame_height": 600, "horizontal_error": 0.03,
 "area": 4500.0, "area_ratio": 0.00938, "bbox": [380, 270, 64, 60],
 "direction": "CENTER", "candidates": 2, "frame_id": 1234,
 "timestamp": 1794069602.28, "processing_ms": 1.1}
```

- `horizontal_error = (center_x − width/2) / (width/2)`: −1 is the left edge, 0 is centered, +1 is the right edge.
- `direction`: `LEFT` / `CENTER` / `RIGHT`, or `NONE` when nothing is detected. In that case
  `center_*`, `horizontal_error`, `area`, `area_ratio` and `bbox` are `null`.
- **Best-candidate rule:** among regions that pass all filters, pick the **largest contour area**.
  `candidates` is how many passed. A passing region is only a *candidate*: a colored object
  that is not a rock can still pass.
- **`area` / `area_ratio` are not distance.** They grow as the rover gets closer and can drive
  relative behavior ("keep approaching while it grows"), but rock size, viewing angle and partial
  occlusion all change them. Proximity must be confirmed with the ultrasonic sensor.
- `timestamp` is `time.time()` when the frame **finished arriving on the laptop**. It is not the
  moment the camera captured it (the firmware sends no capture time).
- No confidence score is reported: HSV thresholding has no meaningful probability.

### Using the detector from other code (no GUI)

```python
from camera import CameraStream
from config import load_config
from detector import ColorDetector

cfg = load_config()
cam = CameraStream.from_config(cfg["camera"]).start()   # create ONE per process
detector = ColorDetector.from_config(cfg)

last_id = -1
while True:
    frame = cam.wait_for_frame(after_id=last_id, timeout=1.0)   # newest frame only
    if frame is None:
        print("no frame:", cam.stats()["state"])                # e.g. reconnecting
        continue
    last_id = frame.frame_id
    result = detector.detect(frame.image, "red", frame.frame_id, frame.received_wall).to_dict()
```

**One connection rule:** the ESP32 serves one stream client, so only one `CameraStream` may exist.
Navigation and (later) Mission Control should run in the same process and share it. Each
consumer keeps its own `last_id` and calls `wait_for_frame()` / `latest()`. `frame.jpeg` holds
the original JPEG bytes, so a future web page can be fed without re-encoding. `frame.image` is
read-only and shared, so `.copy()` it before drawing on it.

## 6. What the metrics mean

| Shown as | Definition |
|---|---|
| `stream N fps` | JPEG frames received **and decoded** from the ESP32 per second (2 s window) |
| `processing N fps` | frames run through the detector per second. Lower than stream FPS means frames are being skipped, which is intentional so lag cannot build up |
| `detect N ms` | wall time of `detector.detect()` for that frame (on this Mac, ~1 ms for a synthetic 800×600 frame) |
| `age N ms` | time since the displayed frame finished arriving on the laptop |

None of these is **camera-to-laptop latency**. The firmware does not timestamp captures, so the
total delay (sensor exposure + JPEG encode + Wi-Fi + decode) can only be measured externally;
see test 7 below.

## 7. Camera facts from the firmware (`firmware/esp32-camera/...`)

- Port 80: `/` (web UI), `/status`, `/control?var=&val=`, `/capture`, `/Test` (also a stream). Port 81: `/stream`.
- Default frame size is **SVGA 800×600**, JPEG quality 10, 2 frame buffers, 10 MHz XCLK, no mirror/flip.
- Opening the stream always turns the firmware's face detection on, but it only runs on frames
  ≤ 400 px wide. **At the default 800×600 it never runs.** If you ever lower the frame size to
  320×240 via `/control`, also put `"face_detect": 0` in `camera.controls`, or the ESP32 will run
  face detection on every frame and FPS will collapse.
- The firmware's framesize numbers depend on which ESP32 core built it (the vendor comments
  disagree), so check `/status` before you set `framesize`.
- The multipart stream has no boundary before the first frame. `camera.py` handles this by
  parsing each part's `Content-Length`.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `Waiting for camera (connecting/reconnecting)… URLError … timed out` | You're not on the `ELEGOO-…` Wi-Fi, or the rover is off. Check with `curl -s http://192.168.4.1/status`. |
| `/status` works but the stream never starts | Another client holds the stream: close browser tabs on `192.168.4.1` and the ELEGOO app. Power-cycle the rover if needed. |
| `No route to host` / `Errno 65` on recent macOS | Allow **Local Network** access for Terminal (or your IDE) in System Settings → Privacy & Security → Local Network, then restart the terminal. |
| Mac keeps leaving the ELEGOO network | Turn off Auto-Join for other networks (§2). |
| Very low stream FPS | Move closer to the rover. Avoid 2.4 GHz congestion, and close other devices on the ELEGOO network. |
| Image upside down / sideways | Set `camera.rotate` to 180 / 90 / 270. |
| Target flickers between DETECTED and LOST | Re-calibrate in the current light (widen V low / S low a little) and raise `min_area_px` if specks appear. |
| `ModuleNotFoundError: cv2` | You ran the system python; use `.venv/bin/python`. |

## 9. Tests

### Automated (no hardware)

```bash
.venv/bin/python -m unittest discover -s tests -v
```

These cover:
- each color, picking the requested color among red/yellow/green/blue, and a red + yellow scene
- red and yellow hue ranges never both match the same color
- red hue wrap-around
- largest-candidate rule, small noise, salt noise, a huge region and a thin streak being rejected
- the empty-frame / lost result and the JSON fields
- horizontal error sign and direction
- config save/load
- the ESP32-style MJPEG parsing (missing first boundary, split reads, no Content-Length, garbage)
- reconnect after the camera closes the stream, reconnect after a stalled stream, an unreachable camera
- newest-frame-only behavior, prompt stop
- offline folder source, overlay and snapshot output

### Physical camera checklist

Each test below has its own command. Fill in the table and attach snapshots (`s`), which save to `captures/`.

| # | Test | Command / procedure | Record |
|---|---|---|---|
| 1 | Accuracy per color | `main.py`, each rock alone at ~50 cm, centered, then left and right. Press its key (`r`/`y`/`g`/`b`). | DETECTED? direction correct? 1 snapshot per color |
| 2 | Multiple colors | All rocks in view; cycle through their keys | correct rock boxed each time? snapshot |
| 3 | Distance | One rock at 20, 50, 100, 150, 200 cm | max distance still DETECTED; `area` at each |
| 4 | Lighting | Room lights on / off / one lamp from the side; re-check test 1 (no re-calibration) | which colors fail; snapshot of failures |
| 5 | False positives | Empty arena, slowly pan the camera by hand | any boxes? snapshot of each false box |
| 6 | Moving camera | Rock in view; turn the rover by hand ~90° in 1 s, then slowly | does it keep DETECTED? motion blur? |
| 7 | FPS and latency | `main.py --headless --duration 60 > fps.jsonl` with a rock in view. For total latency: point the camera at a phone stopwatch showing ms, photograph the phone **and** the laptop window together with a second phone, and subtract the two times; repeat 3× | stream fps, processing fps, detect ms, age ms; 3 latency samples |
| 8 | Target disappears | Remove the rock (or cover it) for ~5 s, then put it back | shows `LOST n.ns` and recovers? |
| 9 | Stream disconnect | While running: switch the rover off ~10 s, then on. Also walk the laptop out of range | shows `CAMERA RECONNECTING`, recovers by itself? time to recover; app never froze? |

Summarize `fps.jsonl` with:

```bash
.venv/bin/python -c "import json,statistics as s;r=[json.loads(l) for l in open('fps.jsonl')];m=[x['metrics'] for x in r if x['detection']];d=[x['detection'] for x in r if x['detection']];print('stream fps',s.median(x['stream_fps'] for x in m),'| processing fps',s.median(x['proc_fps'] for x in m),'| detect ms',s.median(x['processing_ms'] for x in d),'| age ms',s.median(x['age_ms'] for x in m),'| detected',sum(x['detected'] for x in d),'/',len(d))"
```

**Results: first physical-camera session, 2026-11-07** (default HSV values, indoor room light, objects
held by hand, rover stationary). Color detection was accepted by the team after this session.
Only test 1 and part of tests 2, 7 and 9 were run; the rest are still open.

| # | Result | Notes |
|---|---|---|
| 1 | Accepted | **Yellow:** a yellow box was detected and correctly marked CENTER. **Red:** detected, but the target was a red image on a laptop screen, not a physical object (screens emit light, so colors differ), so it still needs a real red rock. |
| 2 | Partial | Switching between `r` and `y` worked. The red-on-screen frame had 8 red candidates (all real red swatches) and the largest one was chosen. Red and yellow rocks were never in view together. |
| 3 | Not run | |
| 4 | Not run | |
| 5 | Not run | |
| 6 | Not run | |
| 7 | Partial | Snapshots showed **stream 15.0–15.9 fps** at 800×600, processing FPS equal to the stream (no skipped frames), **detect 0.9–2.4 ms**, and frame age 2–55 ms. The 60 s headless run and the camera-to-laptop latency were not measured. |
| 8 | Not run | |
| 9 | Partial | No deliberate power-off. The stream **stalled twice without being touched** (probably Wi-Fi); each time it was detected after the 3 s timeout and reconnected about 0.5 s later. One `Corrupt JPEG data` warning appeared just before the first stall. |

**Known issue:** an object with dark print on it (the yellow box) can split into two masks. The
detector then boxes the larger piece, which can turn a centered object into `RIGHT`/`LEFT`. A
solid-colored rock is less likely to split; check this with the real rocks before changing the code.

## 10. Navigation dry run (no motors)

`--navigate` adds two things on top of the live video:
- the **floor check** (`freespace.py`)
- the **navigation planner** (`planner.py`)

It shows what the rover *would* do next. **It never sends anything to the rover.** It doesn't open
the port-100 command connection, so it can run while Mission Control's bench page is idle.

```bash
.venv/bin/python main.py --navigate --color red
```

| On screen | Meaning |
|---|---|
| Colored bars at the bottom | One per strip (5 strips): how far up the **white floor** reaches. Green = clear, red = blocked (shorter than the red horizontal line, `floor.blocked_height`) |
| White outlined strips | The rover's path (`floor.corridor_half_width`); any red bar here means "blocked ahead" |
| `DRY RUN (no motors) state: …` | Planner state: SEARCH, APPROACH, AVOID, EXPLORE, ARRIVED or GIVE_UP |
| `NEXT: TURN LEFT 140 ms` + arrow | The one timed move it would send now (vendor N2 command), plus the reason |
| `NO FLOOR VISIBLE …` | The bottom of the image isn't white floor. Check the camera view or `floor.s_max` |

**Keys:** the usual ones, plus `f` (floor-mask window, where white = counted as floor) and `x`
(reset the planner, e.g. after ARRIVED). Changing the color also resets the planner.

**How it decides:**
- The planner decides **every 0.5 s** (`navigation.decision_period_s`), roughly one move + settle in
  the real loop.
- It **assumes each move happened**, so its counters keep advancing while you hold the rover still.
  For example, SEARCH counts 8 turns and then switches to EXPLORE.
- The ultrasonic sensor isn't read in the dry run, so **arrival is vision-only**: the target is
  centered and its `area_ratio` ≥ `navigation.arrive_area_ratio`. The real loop also requires a valid
  ultrasonic reading ≤ `arrive_distance_cm`.

### Dry-run test procedure (push the rover by hand)

Set the rover on the arena floor, start the command above, and move the rover **by hand** (or slide
it) between snapshots. Press `s` at each step below. The snapshot JSON includes the planner state,
the action and the floor strips.

| # | Set-up | Expected |
|---|---|---|
| A | Open floor, no target in view | SEARCH, turning in one direction. After ~4 s (8 decisions) EXPLORE → FORWARD toward open floor |
| B | Target straight ahead, ~1 m, clear path | APPROACH → FORWARD 300 ms |
| C | Target at the left/right edge | TURN LEFT / TURN RIGHT, longer the further off-center |
| D | Target close (~20 cm) and centered | ARRIVED (vision only). Note the `area_ratio` shown at the moment it triggers |
| E | Yellow obstacle between the rover and the target | AVOID, turning toward the side with more floor |
| F | Facing a cardboard wall at ~20 cm | All bars red; the action turns or backs off |
| G | Facing a wall at ~50 cm | Bars partly up; the center should **not** be blocked |
| H | A corner | Bars red; it turns toward the more open side |
| I | Target behind an obstacle, then you remove the target | APPROACH → after 3 decisions SEARCH, turning toward where it was last seen |
| J | Strong shadow across the floor | Shadow still counts as floor (bars stay green) |

**Please send back:**
- the snapshots (`captures/*_annotated.jpg` and `*_result.json`)
- for each row: did it match **Expected**?
- whether `NO FLOOR VISIBLE` ever appeared on open floor (that would mean the camera can't see the
  floor right in front of the rover)

### Tuning (`config.json`)

| Setting | Default | Adjust when |
|---|---|---|
| `floor.s_max` | 50 | Floor shows as blocked (raise it), or cardboard shows as floor (lower it). Click pixels to read their S value; floor and cardboard should be on opposite sides of this number |
| `floor.v_min` | 90 | Dark shadows on the floor are counted as obstacles (lower it) |
| `floor.roi_top` | 0.5 | Fraction of the image height where the analyzed area starts. Lower = looks further ahead |
| `floor.blocked_height` | 0.35 | A wall at ~50 cm counts as blocked (lower it), or the rover gets too close before reacting (raise it) |
| `floor.corridor_half_width` | 0.45 | Which strips count as "ahead" (0.45 = the middle 3 of 5) |
| `navigation.arrive_area_ratio` | 0.08 | Set it to the target's `area_ratio` at ~20 cm (test D) |
| `navigation.search_turn_ms` / `avoid_turn_ms` | 300 | Calibrate on the floor so one step turns ~45° |
| `navigation.forward_ms`, `turn_pwm`, `drive_pwm` | 300 / 60 / 60 | Calibrate on the floor |

The headless log works too: `main.py --navigate --headless --duration 60 > nav.jsonl` adds a
`"navigation"` object per frame.

**Not in the dry run:** real motor commands, the ultrasonic sensor, and the servo sweep. These come
when the planner moves into Mission Control's rover adapter. Every move there will be one N2 timed
command, and servo/LED commands will be sent only while stopped (see `docs/HANDOFF.md`).
