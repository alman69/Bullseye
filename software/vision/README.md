# Bullseye laptop vision (Phase 1)

Laptop-side color-target detection for the ELEGOO Smart Robot Car V4.0's
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
| `r` `g` `b` | select red / green / blue target |
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

1. `.venv/bin/python main.py --calibrate`, put one rock in view, press its key (`r`/`g`/`b`).
2. **Left-click the rock** in the main window a few times. Note the `HSV at (...)` numbers.
3. In the **Calibration (mask)** window, move the sliders until **only the rock** stays visible:
   - **H low / H high**: hue band (OpenCV hue is 0–179). For **red**, set H low *greater* than
     H high (e.g. 170 and 10). That means "170..179 plus 0..10", because red sits at both ends
     of the hue scale.
   - **S low**: raise it to drop gray/brown cardboard (low saturation).
   - **V low**: raise it to drop dark shadows. Lower it if the rock vanishes in shadow.
4. Check the other two rocks and the empty arena: they must stay black for this color.
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
- each color, and picking the requested color among red/green/blue
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
| 1 | Accuracy per color | `main.py`, each rock alone at ~50 cm, centered, then left and right. Press `r`/`g`/`b`. | DETECTED? direction correct? 1 snapshot per color |
| 2 | Multiple colors | All 3 rocks in view; cycle `r`/`g`/`b` | correct rock boxed each time? snapshot |
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

**Results: not yet run on the physical camera.** Fill this in after testing.

| # | Result | Notes |
|---|---|---|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
| 7 | | |
| 8 | | |
| 9 | | |
