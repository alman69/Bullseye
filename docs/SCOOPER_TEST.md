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
With the original wiring/configuration, right logical 80° is mirrored to 100°
and approximately duty 1274 / 16384 on GPIO42; home 90° is duty 1188. These are
peripheral register readbacks, not an electrical measurement at the connector.

## If only one servo moves

With **both USB and servo power disconnected**, swap only the signal leads
between GPIO41 and GPIO42, leaving supply/GND wiring intact. Restore power with
the mechanism clear, keep Mission Control stopped, and test `set 1 80 30` /
`set 1 90 30`, waiting between lines. This now tests the previously stationary
servo on the signal path that moved the other servo.

- If that servo now moves, investigate the original GPIO42 signal path or its
  connector/output; this does not yet distinguish software output from wiring.
- If it still does not move on the known working path, inspect that servo's
  supply, connector, load and servo itself next.
- After the crossover test, disconnect both supplies and **restore GPIO41 to
  left / GPIO42 to right before any normal Squeeze sequence**. The right output
  is intentionally mirrored, so swapped signals are only a diagnostic setup.

Operator reports on November 8: the left servo moved and returned on GPIO41.
The original right servo did not visibly move on GPIO42, although status
reported right 80° then 90°, `ready=1`, `watchdog=0`. After swapping only signal
leads, that previously stationary servo **did move on GPIO41**. This establishes
that both servos can move on the tested GPIO41 path, without establishing that
the GPIO42 path works. The reverse crossover result is still pending.
Both physical observations and status are needed; a changing angle number alone
does not count as a motor test passing.

A cleared watchdog warning never resumes a previous sequence. Only a fresh
operator action can start another move. No physical pass is claimed until the
operator reports it.
