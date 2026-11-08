# From timed approach to reliable exploration


The integrated `/bench` mission approaches red or green ore, stops to signal the sample with green
rover LEDs and a happy response on the connected BLE display, then automatically searches for the black home flag and returns.
Reaching the ore is the demo sample event; no grasping or manual pickup is required. This is a
baseline to validate before building a complete exploration system. The rover does not yet know its
global position or which floor areas it has visited. A successful simulated mission does not establish physical arrival accuracy.

## What the integration provides

- Local palette calibration and rover-mask UI are retained. Red and green are ore targets, blue is
  an obstacle, and yellow remains a calibration class. Brown cardboard is wall, near-black is a
  home candidate, and floor is background.
- Each accepted example saves immediately, including additional lighting/color variations. Server
  restarts and rover power cycles retain the computer's calibration file. Stale writers are rejected.
- The Alex training branch contributes timed approach/search, fresh-frame decisions, glimpse
  recovery, sonar checks, and return-to-home targeting. The local palette replaces its older color UI.
- Physical autonomous commands always use obstacle avoidance and a bounded search time. Home
  approach requires a valid sonar reading. Arrival needs repeated fresh centered observations and
  acknowledged STOP; an unexecutable tiny final step does not count as arrival. Integer sonar
  readings use a 1 cm early-stop buffer; physical error still needs measurement.
- At stopped ore arrival, `sample_reached` signals the demo sample: rover LEDs turn green and the
  display celebrates. The mission then automatically searches for home and returns with green LEDs.
  At `home_arrived`, it stops and resets red. Ore searching starts with red LEDs.
- Manual servo controls are available separately. The autonomous sample event records arrival, not physical possession. No manual
  pickup/deposit confirmation or arbitrary wait for a person is part of the mission.

Color and size are still heuristics. A black shadow can resemble the home flag, the sonar can see a
wall behind a small target, and timed motion drifts with battery, traction, and load. The phases below
address those limits in order. Complete each acceptance gate before enabling the next behavior.

## 1. Establish measured stopping and replay evidence

Record short camera sequences, frame timestamps, detector outputs, sonar replies, requested motion,
STOP acknowledgements, and operator-measured final gaps. Keep recordings outside Git. Annotate
target identity, occlusion, base visibility, and shadows so the same failures can be replayed.

Measure the camera pose, stick-tip zero point, sonar origin/beam height, and distance offset between
them. Re-measure forward and turning tables at the active PWM, loaded rover weight, and representative
battery levels. Mark a minimum reliable pulse; interpolation below that pulse must remain a
stopped-short result. Define an operator-approved ore-arrival gap and home gap rather than treating
sonar centimeters as stick-tip centimeters.

Acceptance:

- Replay empty floor/wall/shadow clips without false target arrival or a false home arrival.
- Run ten supervised approaches per color from varied headings/distances. Record all final gaps;
  every claimed arrival must be within the agreed tolerance, with no contact or overshoot.
- Verify camera loss, delayed frames, invalid sonar, stale control tokens, and failed STOP replies
  produce a stopped/failed outcome rather than mission success.
- Restart the server and power-cycle the camera; verify identical saved profiles and masks, and
  confirm camera controls were reapplied before comparing detections.

## 2. Make the sample event and automatic return dependable

Keep navigation states explicit: `searching`, `approaching`, `sample_reached`, `returning`,
`home_arrived`, and `aborted`. Only a successful stopped arrival enters `sample_reached`; a timer,
a single detection, or an LED command alone cannot trigger the sample event. Then start home search
without requiring operator pickup confirmation. The mission records reaching the ore, not grasping it.

Add a short stationary verification window for target identity, alignment, and measured distance.
Use hysteresis so one frame cannot toggle arrival. If the target is lost, evidence conflicts, or
motion cannot safely close the remaining gap, keep the rover stopped and explain the reason.
Persist a mission journal for diagnostics, but require fresh ownership/enablement after restart;
never resume motor commands automatically.

Keep feedback consistent with motion: red for ore search, green once the sample event succeeds and
throughout return, and red after confirmed home arrival. The happy/excited display response comes
from the same sample event. Send LED commands only while stopped; the stock rover's STOP command
clears its LEDs, so the desired mission color must be restored safely between timed steps.

Acceptance:

- Verify the order: stopped ore arrival, sample feedback, automatic home search, stopped home arrival.
- Reject duplicate/stale mission commands and retain STOP priority during every state transition.
- Ensure a failed STOP or arrival check cannot start return or announce a sample.
- Verify the board LEDs remain green across return steps and the display receives the sample event.
- After process loss or power loss, boot stopped and disarmed, with the interrupted mission visible.
- Separate software STOP acknowledgement from physical stopped verification in logs and UI.

## 3. Track local motion and uncertainty

Start with a local coordinate frame at home and integrate the measured pulse model. Record uncertainty
with every movement; this is estimated odometry, not measured wheel travel. Use available heading
sensors only after verifying their transport and calibration. Do not assume that the stock firmware
exposes encoder or IMU data to Mission Control.

Add repeatable visual landmarks or a deliberate base marker geometry to correct drift. If hardware
can support measured wheel travel later, introduce it through an adapter with tests. Record obstacle
observations with timestamp and pose uncertainty; discard stale or poorly localized observations.
Stop or relocalize when uncertainty exceeds a tested bound.

Acceptance:

- Compare estimated and measured end poses for straight, turning, and closed-loop routes.
- Quantify drift across battery levels and surfaces and set the allowed localization uncertainty.
- Demonstrate relocalization to the home landmark from multiple headings and ranges.
- Never mark uncertain unseen space as clear merely because no obstacle was detected.

## 4. Explore bounded, reachable space

Use a small local occupancy grid or waypoint graph with `unknown`, `observed free`, `occupied`, and
`visited` states. Inflate obstacles by the measured rover footprint plus localization uncertainty.
Choose a reachable frontier based on travel cost, unexplored visibility, and the cost of returning
home. Keep a visit history so the rover does not repeatedly turn into the same dead end.

Begin with a configured arena boundary and conservative motion budget. Replan after each bounded
move using fresh camera/sonar observations. Limit time, distance, retries, and uncertainty. If all
frontiers are blocked or no valid observation arrives, stop with a specific result. A target sighting
can interrupt exploration; its location and confidence should be stored for reacquisition.

Acceptance:

- Simulated and replay scenarios cover corridors, cul-de-sacs, an unreachable target, repeated
  sightings, occlusion, and a dynamically blocked route without endless loops.
- In a measured test arena, report coverage of reachable floor and revisit count against ground
  truth. Set a target coverage threshold before the run, not after observing the result.
- Every run terminates by target arrival, exhausted reachable frontiers, a stated budget limit,
  operator STOP, or a sensor/localization fault.

## 5. Return home with route and identity checks

Plan a route through known free space toward the saved home region. Reserve enough motion/time
budget for return. Revalidate the route as obstacles change, then switch to a short final approach
only after reacquiring the home marker. Do not infer home from a 180° turn or a single dark blob.

Keep the strict near-black palette gate and add marker geometry, expected location, multi-frame
consistency, and a clear approach corridor. Use calibrated sonar distance for the wall and an
appropriate visual marker check for identity. If home remains ambiguous, hold and request operator
help instead of declaring completion.

Acceptance:

- Return from several mapped locations and headings, including with an initially hidden flag.
- Reject equally black shadows/objects outside the expected home geometry/location.
- Stop short on missing sonar, a blocked final corridor, a missing flag, or excessive pose uncertainty.
- Record the final physical gap, confirm motion stops, and verify completion resets the LEDs to red.

## 6. Roll out in controlled stages

Use recorded replay first, then the simulator with fault injection, then wheels-raised command tests,
then a cleared physical arena, then cluttered runs. Keep manual driving, STOP, and the current color
calibration workflow available. Add a prominent mission state and reason for holding; keep maps and
diagnostic tuning optional until they help the operator make a decision.

The release gate is a reproducible acceptance log covering successful approaches, interrupted runs,
sample feedback, automatic return, return-home identity, and measured stopping distance. Do not label this fully autonomous
exploration until coverage, drift bounds, and failure handling pass the same physical test course.
