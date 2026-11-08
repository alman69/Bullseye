# Sample search and manual grab

The `/bench` sample mission finds the selected **ruby** (red) or **emerald** (green), stops about
10 cm in front of it and hands the rover to the pilot. The pilot drives up, squeezes the gripper,
drives the sample back to home base and presses **Delivered**. The rover never grabs or drives home
by itself. The code is `mission-control/server/bullseye/autopilot.py` (one short loop).

## Controls are always on

With `run-rover-demo.sh` the server connects to the rover by itself, **reconnects automatically**
if the connection drops (Wi-Fi blip, ESP32 timeout), and keeps the drive controls enabled. The
page takes the free pilot seat by itself. **STOP** halts the rover and any search at once; the
controls come back on after the STOP is acknowledged. Motion is never replayed after a reconnect
and an interrupted search never resumes on its own. Set `BULLSEYE_BENCH_AUTO=0` for the old
behaviour (Claim controls → Connect rover bridge → Enable controls).

## The loop

1. **One 360° scan.** Turn right in measured steps of 37.5° (¾ of the camera's 50° view, so views
   overlap). After each step check the camera (the color must appear in two fresh frames) and
   record an ultrasonic reading for that heading. This is the **only** full turn of the search.
2. **Found → approach.** Turn to center the sample, then step forward. Steps are 15 cm while the
   gap is over 35 cm and the sample is high in the image, 7.5 cm closer in, and 2.7 cm when it is
   nearly at the bottom of the image. The gap is the closer of the sonar reading and the camera's
   ground estimate. Stop at 10 cm (1 cm early for whole-centimetre sonar), or when the sample's
   bottom edge reaches `navigation.arrive_bottom_y` (default 0.85), which covers paper balls
   below the sonar beam. A blue obstacle in the path stops the search.
3. **Not found → explore.** Turn to the heading with the most ultrasonic room from the scan and
   drive forward in steps of up to 15 cm, checking the camera and sonar between steps, until the
   sonar is within `navigation.wall_distance_cm` (25 cm) of a wall or a blue obstacle is ahead.
4. **At a wall** (the sample is probably not that way): turn 90° to one side in camera-checked
   steps and read the sonar. If that side has 100 cm or more, take it. Otherwise check the other
   side and take the more open one; if both are walls, turn around. Then keep exploring. The first
   side alternates between walls. Three wall checks in a row without driving forward stops the
   search ("walls on every side").
5. **Lost the sample mid-approach:** look up to 60° toward the side it was last seen, then the
   other side, then keep exploring. Still no second 360.
6. **Hand over.** Acknowledged STOP, green LEDs, happy display. Autonomy is off; drive up with
   WASD / arrows, **Squeeze**, drive home, press **Delivered** (LEDs back to red).

STOP ends the search any time; a 10-minute safety limit ends one that never finds anything. No
echo (`0`) is unknown space, never open space. Walls are judged by the sonar only; the camera's
floor check no longer stops the rover (it flagged cardboard walls and shadows as blocked, which
caused the repeated 360° scans). Only blue obstacles block in the camera.

## Limits to check on the real rover

- Turn and forward steps come from the measured step table (Calibration → Steps). Battery, PWM and
  floor change them; re-measure if scans fall short of 360° or approaches over/undershoot.
- The sonar measures from the sensor, not the gripper tips. Measure that offset and the final gap
  for both samples from several headings before trusting "10 cm".
- `arrive_bottom_y` is a guess until checked with the real paper ball and gripper mask; calibrate
  the ground stop line for a camera distance estimate.
- There is no map or odometry: "open space" is the best direction from one scan, not a guarantee
  of covering the arena.
