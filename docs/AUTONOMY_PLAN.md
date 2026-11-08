# Color search and manual pickup

The `/bench` sample mission searches for the selected red or green material, stops about 10 cm
away using ultrasonic distance, and hands collection to the operator. The user picks up the
material by hand and carries it back to home base. Neither arrival feedback nor an elapsed timer
confirms physical collection. The mission does not drive home automatically.

## Current sequence

1. **Choose and start.** Claim the pilot seat, connect the rover bridge, enable controls, and
   choose red or green. Existing palette calibration and traced rover masks are preserved.
2. **Scan.** Rotate in measured, bounded steps while checking fresh camera frames for that color.
   Without a confirmed target, finish a full estimated 360° sweep before moving forward. Record
   valid ultrasonic readings around the sweep.
3. **Relocate when absent.** Of the sampled headings with sonar and floor clearance, select the
   one requiring the smallest turn (tie: more clearance). Turn toward it, recheck sonar and floor,
   and move one measured step of at most 15 cm while preserving wall clearance. Start another
   360° scan. No echo is unknown space, never clear space.
4. **Approach the selected material.** Confirm the target across fresh frames, aim with short
   turns, and remeasure sonar before every forward step. Camera size and the floor stop line
   cannot substitute for a valid ultrasonic arrival reading. Stop about 10 cm from the object,
   using a 1 cm early-stop allowance for integer sonar readings.
5. **Pick up by hand.** After repeated centered arrival checks and acknowledged STOP, disable
   rover motion. Green LEDs and the happy display indicate arrival. Pick up the material yourself
   and confirm pickup in the UI; the rover does not operate its gripper.
6. **Carry home.** Carry the material to home base, then confirm delivery. Re-enable manual
   controls separately if you also want to drive the rover. A pickup/delivery confirmation never
   launches automatic return.

The UI places current stage, selected color, distance, and the next required action near the camera.
STOP remains available throughout. Blue is an obstacle; yellow remains a calibration class.
Near-black home detection remains available for diagnostics and legacy direct navigation, but is
not part of this mission's manual delivery confirmation.

## What the sensors can establish

The rover has no global map, measured wheel odometry, or complete model of reachable floor.
“Nearby open space” means a locally observed sonar-clear direction for a short relocation, not a
proof that it is the globally nearest opening or that the whole arena has been searched. Timed
turns estimate 360° using saved turn calibration; battery, traction, PWM, and load affect coverage.

Sonar measures from its sensor origin, not from the gripper tips. A small material below the beam
can produce a reading from the wall behind it. Use materials the sensor can detect and measure the
mounting offset during physical validation. A color match alone cannot establish object identity.
A simulated success does not establish physical arrival accuracy.

The search is bounded by the configured timeout (30–300 seconds, normally 120). Missing camera
frames, unavailable sonar, blocked relocation, an unsafe final step, failed STOP, lost pilot
ownership, or connection loss produce a stopped/failed result. Reconnecting never resumes motion.

## Acceptance checks before physical use

- Measure forward and turning steps at the active PWM, representative battery levels, and actual
  rover load. Confirm one complete scan covers the surroundings without premature translation.
- Exercise scenes with no target, a target behind the rover, an interrupted sighting, a blocked
  heading, invalid sonar, and an obstacle entering the selected opening. Check every relocation
  against a fresh distance reading and repeat the scan afterward.
- Run supervised approaches for both colors from varied headings. Record the measured gap and
  sonar reading for every claimed arrival. No camera-only or below-beam target should count as
  a confirmed 10 cm approach.
- Verify that arrival leaves wheels stopped and controls disabled. Confirming pickup and delivery
  must issue no motor movement, and no timer may start a home search.
- Check duplicate/stale confirmations, STOP at every phase, camera loss, failed STOP replies,
  pilot disconnect, and process restart. Never resume an interrupted mission automatically.
- Retain camera sequences, sensor readings, pulse durations, and STOP acknowledgements outside
  Git for replay. Distinguish a software acknowledgement from observed physical stopping.

## Future exploration work

Reliable arena coverage needs pose estimates with measured uncertainty, an occupancy map inflated
by the rover footprint, and a visit history to avoid repeated dead ends. Start with recorded replay
and a bounded test arena; validate drift, reachability, and obstacle changes before extending the
search. Automatic home navigation would be a separate feature with route and home-identity checks,
not a consequence of confirming manual pickup.
