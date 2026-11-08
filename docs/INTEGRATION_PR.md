# PR title

Integrate ore missions and automatic home return with saved palette calibration

## Summary

Combines `origin/AlexTrainingAutonomousBranch` at `5625198` with the preserved local vision and
bench setup at `c6f3a91`. The operator keeps the simpler click-to-calibrate palette and traced rover
mask. Red and green are ore targets; blue is an obstacle. Yellow remains a calibration class,
with cardboard, near-black home, and floor as separate materials. The existing operator profiles,
camera settings, and mask geometry are retained.

Accepted examples save immediately and survive server restarts and rover power cycles. Atomic saves,
a file lock, and revision checks prevent a stale second process from overwriting newer calibration.
The home gate stays narrow while accepting near-black samples whose HSV saturation is unstable.

The `/bench` mission approaches red or green ore and stops. Reaching the ore signals the demo sample
with green rover LEDs and a happy/excited display response, then automatically starts home search and
return. LEDs start red, stay green through return, and reset red at home. There is no manual pickup
or deposit step; this marks arrival and does not claim physical grasping. Servo squeezing remains
retired, including rejection of old command endpoints. Historical firmware experiments remain in the repository; this change does not flash devices.

## Navigation changes

- Map the old `black` target alias to the palette's `home` class.
- Restrict ore missions to red/green, retain blue as an obstacle, and preserve all saved color profiles.
- Keep obstacle avoidance enabled for physical autonomous runs and bound search duration.
- Require fresh centered observations and acknowledged STOP before reporting arrival.
- Require valid sonar for home approach and exploratory forward motion.
- Stop short with an explanation when the remaining distance cannot fit a safe timed pulse.
- Automatically reacquire the actual home flag after the sample event; do not assume a blind half-turn.
- Restore the desired mission LED color safely after STOP clears the stock rover LEDs.
- Reject vision calibration/settings changes while a bench operation is active.

## Validation

- Server: **207 tests passed**, covering autonomous arrival, automatic return, cancellation,
  LED failures, blue/yellow target rejection, calibration conflicts, and BLE expression transport.
- Vision: **117 tests passed**, including restart persistence, concurrent saves, palette and masks.
- Web: **67 tests passed**; TypeScript and production build passed (Node 22+).
- Native display, companion, and archived scooper checks passed; display/companion use sanitizers.
- Offline Arduino builds passed for the standalone display and generated companion. Generated
  source consistency passed. No firmware flashing, BLE discovery, or rover motion was performed.
- Operator palette, rover masks, camera/ground settings, and measured step tables match the
  pre-merge backup. The explicit obstacle role is now blue.

Physical stopping distance, real obstacle avoidance, flag/shadow discrimination, and exploration
coverage have not been validated by this change. Timed approach/search remains experimental.
The [autonomy plan](AUTONOMY_PLAN.md) defines measured stopping, reliable sample feedback/return,
localization, exploration, and return-home acceptance work.

## Display setup

The happy expression is carried by the existing read-only BLE telemetry sender. Upload the updated
standalone display firmware once and run `mission-control/send-display.sh` alongside the rover
server. Older flashed firmware accepts normal telemetry but does not support the expression field.
The [display guide](BLE_DISPLAY.md) covers setup. The 3-second celebration does not require a
manual confirmation; loss of display telemetry clears the temporary happy override.

## Local handoff

Prepared on `codex/integrate-autonomy` for local `main`. The operator requested a local finish with
this PR description ready; no remote PR or GitHub merge is implied. After GitHub authentication is
available, push the integration branch and use this description for a PR against remote `main`.

Restart Mission Control after switching to the integrated code. Run `./mission-control/run.sh` for
the local simulator, or `./mission-control/run-rover-demo.sh` on rover Wi-Fi for `/bench`. Preserve
`software/vision/config.json` (or the configured `BULLSEYE_VISION_DIR`) when moving between checkouts.
