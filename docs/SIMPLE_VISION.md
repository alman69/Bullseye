# Arena color detection and quick calibration

Mission Control now defaults to a small, explicit color palette (`classifier.mode: "palette"`).
It detects **red, green, blue, yellow, brown cardboard walls, and black home-base candidates**
in the same frame. White floor is a background class, used by the existing free-space check.
The selected mission color remains the approach target; other colored objects remain obstacles
in the floor check. Wall/home detection is visual information, not a new autonomous return-home behavior.
The planner remains a dry run and never drives the rover.

## Quick setup

1. Start Mission Control normally (`./mission-control/run.sh`, or `./mission-control/run-rover-demo.sh`
   on the rover Wi-Fi). Open the dashboard or `/bench`, **Claim** controls, then **Calibrate**.
2. Try the built-in colors first. All six materials have live “visible / not in view” indicators.
3. For a color that needs adjustment, choose its chip. Video freezes automatically. **Click inside
   a solid part of that material**, or drag a small box inside it. The color is calibrated and saved
   immediately; there is no separate fit step. Click **Back to live video** to check the result.
4. Add another sample if the material looks different nearby, farther away, or in shadow. A chip
   shows how many samples are saved. Up to eight patches per material are supported. **Undo
   calibration**, **Reset selected color**, and **Use built-in colors** are reversible during the
   session (the last 20 calibration operations are retained for undo).
5. If an empty floor or wall is mistaken for an object, sample **Floor** or **Brown wall** in that
   region. Background materials compete with objects. Avoid glare, boundaries, and boxes containing
   multiple materials; ambiguous color selections are rejected.

Each later patch adds a separate accepted variation: silver and white can both be floor,
and teal, green and dark green can all be labeled green. The first patch replaces that class’s
built-in profile. Classes still compete; overlapping examples may produce unknown pixels.
Home base has an additional fixed near-black ceiling (HSV brightness ≤40/255, saturation ≤80/255),
including for old saved profiles. New home patches have a narrow brightness tolerance of 8–14 levels.
Gray-shadow samples are rejected as home. Shadows as black as the actual base remain indistinguishable
by color alone. Colored samples require brightness ≥20/255 and saturation ≥35/255.

This is automatic **patch calibration**, not an unsupervised guess about which objects are in an
unknown scene. The operator supplies the material identity with one chip selection and one image
click. Black surfaces and deep shadows can share the same camera color; “HOME?” is a candidate,
not proof of the base's identity. Test the real arena before relying on any result.

## Rover mask

Choose **Rover mask → Trace rover outline**. On the frozen image, drag a closed outline around one
claw or visible rover part, then release to save it. Repeat for the other claw. The outline closes
itself; leave the space between the claws clear so a collected object can still be detected.

- Masks are shaded on video and excluded from every material, including blue tape and black parts.
- The existing floor check treats masked rover pixels as excluded from obstruction checking.
- Existing rectangular masks remain valid; new masks use polygons, so fewer shapes are needed.
- **Undo last mask** removes the newest outline, then legacy rectangles once no outlines remain.
- **Clear mask** removes all outlines and rectangles. Up to 12 outlines are supported.
- Masked pixels cannot contaminate color calibration.
- Recheck masks after changing the camera mount, gripper position, rotation, or framing. A fixed
  image mask only works for rover parts that stay in the same image location.

## Advanced settings

Camera controls, stop-line calibration, field of view, measured drive steps, and tuning live under
**Advanced settings**. Set the camera before calibrating colors. Changing a camera setting expires
frozen calibration snapshots; resume live, then sample again. Keep the floodlight and camera settings
consistent during a run. Old color calibration is retained until reset or updated.

Floor strips and the planner preview are optional diagnostics. Normal vision shows all material
boxes and the selected target. On the simulator, the scene now uses white floor, brown walls and a
black base so the same palette can be checked without hardware.

## How detection works

`software/vision/bullseye_vision/palette.py` contains the complete palette:

1. Convert the lightly blurred camera image to HSV once per frame.
2. Compare pixels with small hue/saturation/brightness profiles. Hue wraps correctly across red's
   179/0 boundary. Hue is ignored for neutral black and white materials.
3. Select the closest valid material. Out-of-range pixels and close ties stay **unknown** rather
   than being forced into an object class.
4. Clean each material mask, exclude the rover, and apply the existing size/shape filters.
   Walls and home-base candidates do not use rock-distance/rock-size plausibility filters.

A selected patch yields a robust median and 90th-percentile spread with bounded margins. Multiple
patches represent lighting variations separately rather than broadening one distribution indefinitely.
Profiles compile once to a lookup table: hue retains all 180 OpenCV values, while saturation
and brightness use four-level bins (at most two levels of quantization error). This keeps per-frame
cost independent of the number of calibration patches. There is no online white-balance feedback or separate learned Gaussian model in palette mode.
No probability/confidence claim is attached to a threshold match.

Calibration is saved atomically in `software/vision/config.json` under `palette.profiles` and
`rover_mask.polygons`. Existing camera presets, rectangular masks, navigation settings, and the
legacy `color_model.json`/sample archive are preserved. Legacy HSV/classifier implementations remain
available for offline comparison; the dashboard's primary workflow uses the palette. Standalone
replay supports `main.py --detector palette --source <image-or-video>` and uses the same saved profiles.
The old OpenCV slider/sampling GUI is for legacy HSV/classifier modes; use Mission Control to
calibrate the palette.

Frozen images are lossless PNGs of the exact server pixels. Sampling refers to a bounded snapshot
ID, not whatever live frame arrives later. Up to eight snapshots are retained across browser clients;
expired snapshots require a fresh freeze. A live frame older than three seconds cannot be frozen.
Stopping calibration or losing pilot controls returns the browser to live video.

## Verification

Automated coverage includes circular red hue, all six materials under brightness variation,
background rejection, ambiguous colors, mixed/gray samples, recovering a muted color by calibration,
polygon masks with an unmasked claw gap, snapshot identity/expiry, save/reload/undo, pointer cancellation,
letterboxing, and browser freeze/resume behavior. These synthetic checks are regression tests,
not measurements of real-world accuracy.

Physical acceptance still needs the actual camera and objects:

- Empty arena: no red/green/blue/yellow detections; walls identified; examine black shadow candidates.
- All objects together: correct labels nearby and far away, with no cross-color detections.
- Real black base: visible while nearby dark shadows are checked explicitly.
- Claws: neither blue tape nor black rover parts detected, including while the gripper is operated.
- Calibration: one click improves the intended material; restart retains it; undo/reset behaves as expected.
- Performance: verify displayed FPS and processing time on the laptop with the real stream.

Run checks from the repo root (Node 22.12+ is required by the existing web toolchain):

```sh
(cd software/vision && ../../mission-control/server/.venv/bin/python -m unittest discover -s tests -q)
(cd mission-control/server && .venv/bin/python -m pytest -q)
(cd mission-control/web && npm test && npm run build)
```
