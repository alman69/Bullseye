# Scooper physical baseline test

The website diagram and firmware angles are **commanded positions**, not measured
servo feedback. SG90 motion must be observed by the operator. Software tests or
BLE acknowledgements alone cannot establish that a shaft moved.

## First test: one servo, without Bluetooth

This uses commands already in the combined sketch. No new upload is needed for
this first isolation test. The newest sketch also prints extra diagnostics.

1. Clear the mechanism's range and support the rover so it cannot drive away.
   Keep the existing suitable servo supply powered and common GND connected.
   Do not change connections while powered or force the shafts by hand.
2. Stop Mission Control with Ctrl-C, and stop `send-display.sh` if it is running.
   This disconnects BLE so a remote STOP cannot interrupt the local test.
3. Connect to the **touchscreen/scooper S3**, not the UNO, through its working
   UART USB connector. Open Arduino Serial Monitor at **115200 baud**, with
   **Newline** selected. Uploading is not part of this test. If opening the port
   resets the board, the servos may be commanded to the 90° home pose.
4. Send:

   ```text
   status
   ```

   `ready=1` means the firmware accepted the PWM setup. It does not independently
   verify power, signal voltage or physical movement. If `ready=0` or a PWM
   error is printed, stop and copy the error before trying movement.
5. Send each line separately, waiting until motion settles:

   ```text
   set 1 90 30
   set 1 80 30
   status
   set 1 90 30
   ```

   `1` selects the left servo, `30` limits the slew to 30°/s. From the 90° pose,
   the 80° command is a small 10° move, then it returns. With the first command,
   a servo that was previously elsewhere can travel farther to reach 90°.
   Allow that move to settle before sending the next line.
6. Report whether the **left servo physically moved and returned**, plus the
   Serial Monitor output. If desired, `stop` holds the current commanded angle;
   `relax` removes PWM pulses and holding torque. A loaded mechanism can move
   when torque is removed, so support it first.

If the left servo passes, repeat with `set 2 90 30`, `set 2 80 30`, `status`,
`set 2 90 30` for the right. Its physical rotation is mirrored intentionally.

## What the result establishes

| Observation | Interpretation |
| --- | --- |
| Each shaft moves and returns | Local PWM, current wiring and power can move that servo under this test load; investigate BLE control next. |
| Commands say OK and angles change, but shaft does not move | Firmware accepted a target; physical output/power/servo behavior is still unverified. Do not conclude the wiring or servo is good from the diagram. |
| PWM attach/write error or `ready=0` | The output driver reported a software/peripheral error. Copy the stage and GPIO. |
| No serial response | Check selected S3 port, 115200 baud, Newline, and that another program is not holding the port. This does not diagnose the servo. |

## Second test: BLE, after the local test passes

Close Serial Monitor, start `mission-control/run-rover-demo.sh`, refresh `/bench`,
Claim controls and Connect in the Scooper panel. Leave rover driving disabled.
Mission Control owns BLE; do not run the separate display sender at the same time.

The BLE write fix serializes heartbeats and actions on their shared GATT
characteristic. Backend diagnostics report heartbeat counts and maximum send
gaps when supported. If a test stops or disconnects, save the panel's error and
the last activity messages. Reopening Serial Monitor may reset the board and
erase diagnostics; capture existing serial logs first if that matters.

On the newest firmware, `status` also prints `stop_reason`, `control_age_ms`,
`max_loop_gap_ms`, and `PWM_fault`. `4294967295` for contact age means no remote
control packet has been received since startup. A loop gap near/over 2000 ms
would be evidence that firmware work blocked heartbeat handling; a BLE-link-loss
reason points to disconnection. These measurements help distinguish causes.
The two-second device watchdog remains enabled.

The newest sketch also accepts the **read-only** `pwm` command. Wait two seconds
after a position command, then send `pwm`: expect 50 Hz on each active output.
With the current GPIO39/40 configuration, right logical 80° is mirrored to 100°
and approximately duty 1274 / 16384 on GPIO40; home 90° is duty 1188. These are
peripheral register readbacks, not an electrical measurement at the connector.

## If only one servo moves

With **both USB and servo power disconnected**, swap only the signal leads
between GPIO39 and GPIO40, leaving supply/GND wiring intact. Restore power with
the mechanism clear, keep Mission Control stopped, and test `set 1 80 30` /
`set 1 90 30`, waiting between lines. This now tests the previously stationary
servo on the signal path that moved the other servo.

- If that servo now moves, investigate the original GPIO40 signal path or its
  connector/output; this does not yet distinguish software output from wiring.
- If it still does not move on the known working path, inspect that servo's
  supply, connector, load and servo itself next.
- After the crossover test, disconnect both supplies and **restore GPIO39 to
  left / GPIO40 to right before any normal Squeeze sequence**. The right output
  is intentionally mirrored, so swapped signals are only a diagnostic setup.

Operator reports on November 8: the left servo moved and returned on GPIO41.
The original right servo did not visibly move on GPIO42, although status
reported right 80° then 90°, `ready=1`, `watchdog=0`. After swapping only signal
leads, that previously stationary servo **did move on GPIO41**.

Latest operator follow-up: **both `set 1` and `set 2` now physically work at 80°,
90° and 110°, with 1 controlling the left servo and 2 the right**. The operator
is unsure what adjustment restored operation, so do not assign a confirmed
cause to the earlier stationary output. Both local command paths have now
passed that reported small-range test. Remote BLE motion and the full
squeeze/release range still need operator verification.

Subsequent operator report: both servos also passed the progressive individual
range tests, including the configured endpoints. `boot=` remained unchanged
through the individual tests. This does not establish reset behavior during
the failing BLE Squeeze attempt or simultaneous loaded operation.

The operator confirmed servo power comes from the S3/USB 5V pin, split on a
breadboard, sharing the same battery source as the S3/display. A power-path or
attached-servo effect is a hypothesis, not a confirmed diagnosis. The operator
then disconnected both servos completely and reported that **BLE stayed
connected through Squeeze with only the S3/display powered**. The software
sequence can complete without motor load; attached-servo/power/wiring effects
are now the leading investigation path. Supply voltage/current under load
have not been measured, and the exact electrical cause is not established.

A cleared watchdog warning never resumes a previous sequence. Only a fresh
operator action can start another move. No physical pass is claimed until the
operator reports it.

## Check animation versus an actual reboot

The full-screen face intentionally blinks and automatically cycles five moods
at four seconds each, returning to Happy about every 20 seconds. That alone is
not evidence of a reset. With Mission Control stopped, send `status` twice,
ten seconds apart, without moving anything. Compare `boot=` values and inspect
any boot/reset messages between them. A changed boot ID means a new firmware
boot was observed; a repeated expression with an unchanged ID can be animation.

The updated backend also preserves the most recent device boot ID across BLE
reconnections and counts observed changes. The UI explicitly notes that manual
resets count too. Reconnect without pressing Reset to distinguish a BLE-only
drop from a board restart. No motor action is required for this check.

## Progressive range test, only after idle operation is stable

Current configured logical limits are **50°–175° per servo**. The mission's
squeeze endpoints are **60° and 170°**, with home at 90°. These limits come from
firmware configuration, not a measurement of safe travel with this linkage.

Keep BLE/server control stopped. Clear the linkage's travel, and send each line
**individually**, waiting until that move finishes; pasting the whole list only
replaces targets and does not test each position. `15` is the slew in °/s.

```text
set 1 90 15
set 1 80 15
set 1 60 15
set 1 90 15
set 1 110 15
set 1 140 15
set 1 170 15
set 1 90 15
```

Only if every preceding position is free of binding, the remaining configured
endpoints are `set 1 50 15`, then back to 90°, and `set 1 175 15`, then back
to 90°. The 175°→90° return takes about six seconds at this speed. Repeat with
joint `2` only after joint 1 passes. Stop the test and remove servo power if the
mechanism binds, buzzes hard, heats or the screen/board actually resets. `stop`
holds torque and must not be treated as removal of power from a stalled servo.

These commands exercise one servo at a time. They do not establish that the
supply can run two loaded servos together while BLE and the display are active.

## Isolate motor load from BLE/software behavior

1. Stop Mission Control and remove all USB/servo power before changing wiring.
2. Disconnect both servos completely, including signal and supply leads. Leave
   the display/touch wiring intact. Removing only servo V+ while leaving signal
   attached is not this test.
3. Restore the S3's normal USB power with the display only. Close Serial Monitor,
   start `mission-control/run-rover-demo.sh`, then Claim controls → Scooper Connect.
4. Press Squeeze once with no servos attached and watch BLE for ten seconds.

The same firmware/PWM sequence runs without drawing servo motor current. If
BLE remains connected, investigate the attached-servo/power/wiring path next.
If it still drops, the failure is reproducible without motor load and requires
BLE/firmware investigation. Neither result by itself measures supply voltage
or proves a particular component failed. Restore connections only with power off.

For the next supply-isolation test, identify the exact battery/USB source and
available regulated servo supply, including voltage/current ratings, before
choosing wiring. Power the S3/display by its normal USB method and give the
servos their own suitable power path; their signal reference still requires
common ground. Do not join different positive supplies or inject another supply
into a USB-powered S3's 5V header. The [official DevKitC-1 v1.1 guide](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/user_guide_v1.1.html)
lists board supply methods as mutually exclusive; it does not establish the
available servo current on this particular board, battery, or breadboard path.
