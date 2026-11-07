# Direct ELEGOO connection: first bench test

The preserved factory source exposes a raw TCP server at **192.168.4.1:100** on
the ESP32 access point. The phone app is not needed to speak this protocol.
This is a source-derived expectation; the running firmware and physical bridge
still need to pass the test below. No firmware changes or flashing are involved.

**Owner-reported test, November 7, 2026:** the laptop connected to
`ELEGOO-2436CBF61B44`, exchanged ESP32 heartbeats, received `{ok}` for STOP,
and received a uniquely matched UNO distance reply. Distance consistently reads
short and remains uncalibrated. The owner subsequently reported all four wheels
turning forward during the initial bench pulse. Physical stopping latency and
behavior on control loss have not been measured.
The owner also reported the one-second forward test working, including slower
rotation at base PWM 40. Reverse and turns are still unverified.

The separate camera HTTP service and TCP command service can work independently.
A live camera does not prove that commands reach the UNO.

## Keep Internet access, or make a short offline test

With only one Wi-Fi interface, joining the rover's access point normally replaces
the campus Wi-Fi Internet connection. The laptop's local Python tools and local
Mission Control continue to run; this chat and Cloudflare Tunnel need Internet.

For simultaneous access, use Ethernet for Internet and Wi-Fi for the rover (or
supported phone USB tethering for Internet). The networks must use different
subnets. No Internet Sharing or bridge is needed. See the dual-network checklist
in [Mission Control setup](MISSION_CONTROL.md#share-through-a-tunnel).

With neither option available, prepare this command while online, briefly join
the rover network, run it, and reconnect to campus Wi-Fi to share the output.
Python's standard library is enough; there are no dependencies to download.

## Test 1: STOP and an ultrasonic reading, with no driving

**Checks:** TCP reachability, a UNO software acknowledgement, and a uniquely
identified ultrasonic reply through the ESP32/UART bridge. The client echoes
ESP32 `{Heartbeat}` requests while waiting; those alone do not establish UNO
connectivity.

1. Power the car off. Disconnect the USB-B cable from the UNO, and place the
   Upload/Cam switch on **Cam**. Keep the existing rover wiring in place. Use
   the normal supported battery-powered configuration for this Wi-Fi test.
2. Support the chassis securely with all four wheels clear of the surface.
   Keep hands away from the wheels and the physical switch within reach.
3. Place a broad, flat object about **30 cm in front of the ultrasonic sensor**.
   Measure from the sensor face, along its current pointing direction.
4. Power on the car. Close the ELEGOO app on other devices, then connect the
   laptop's Wi-Fi to its **ELEGOO-…** access point. The command bridge serves one
   active controller; another app can occupy it. Do not select an autonomous
   mode on the remote during this test.
5. In a terminal, from any directory, run:

   ```sh
   python3 /Users/faiyaz/Bullseye/mission-control/server/bullseye/elegoo_probe.py probe
   ```

6. Copy the output. Rejoin campus Wi-Fi if you need to send the result here.
   If the rover moves unexpectedly, switch its power off.

If the initial test times out, repeat this same no-motion test with raw traffic
shown and a five-second response window:

```sh
python3 /Users/faiyaz/Bullseye/mission-control/server/bullseye/elegoo_probe.py probe --verbose --timeout 5
```

This is not a different movement test. Received chunks, complete/unmatched replies,
and heartbeat counts are printed even on failure. If the STOP reply is missing but
the socket stays open, the probe still tries the sensor query; it continues to
label STOP as unverified. The Wi-Fi SSID suffix is not a client setting: use the
actual ELEGOO hotspot name, while the client connects to the default IP.

**Expected:** `TCP connected`, `UNO returned {ok} for STOP`, and a matched distance
reply near the measured target distance. STOP acknowledgement means that the
vendor command handler replied; it is not independent proof that wheels stopped.
The final STOP is best effort, including on errors or Ctrl-C. The tool never sends
a movement command, and never reconnects or replays requests automatically.

**Interpretation:**

| Result | What it establishes / next check |
| --- | --- |
| Connection refused or timed out | The TCP command bridge is not reached; check network/IP, power, and another app occupying the single connection. No UNO conclusion yet. |
| TCP connected, zero received bytes | Socket accepted; application servicing is not established. Check whether the phone app still occupies the command connection. |
| ESP32 heartbeats, but no STOP or sensor reply | ESP32 application is exchanging data; UNO/UART path or firmware compatibility remains unverified. Check Cam position and power before changing firmware. |
| Sensor reply matched, but STOP did not | UNO reply path works for the query; STOP acknowledgement is still unverified. No movement should be enabled. |
| Other complete replies | Preserve the verbose output to check the installed protocol before changing hardware or firmware. |
| STOP reply, but no uniquely matched distance | Some UNO response exists; the sensor/protocol query is not yet verified. |
| Matched reading near 30 cm | Commands and replies traversed the full bridge; this is one measurement, not a complete sensor calibration. |
| Matched `0 cm` | Bridge responded, but distance is invalid/no echo. Never interpret it as clear space. |
| Matched `150 cm` | Vendor driver clamps longer readings to 150 cm; this does not validate the 30 cm target measurement. |

To request STOP only, replace `probe` with `stop`. Do not use this diagnostic as a
remotely verified emergency stop. Physical power remains available during testing.

## Test 2: one short, observed forward pulse

This uses a separate tool; the original `elegoo_probe.py` still cannot send motion.
It checks whether the motor path works, the actual forward direction, and whether
all wheels visibly stop. Keep the car **securely supported with every wheel clear
of the surface**, hands clear, and the physical power switch reachable. Keep the
same battery-powered, USB-disconnected, **Cam** configuration and close the app.

Connect to the ELEGOO hotspot, then run from any terminal directory:

```sh
python3 /Users/faiyaz/Bullseye/mission-control/server/bullseye/elegoo_bench.py forward --wheels-raised --verbose
```

The command requires a preflight STOP acknowledgement, sends **one** N2 forward
command at base PWM **80/255** with a nonzero **200 ms** firmware timer, and requests
STOP after a brief completion-reply wait (nominally at most 300 ms). Completion,
timeout, Ctrl-C, and connection errors all attempt STOP; cleanup tries again.
It never reconnects, repeats movement, or falls back to unbounded N3/N4 commands.
Actual timing is not guaranteed by the existing firmware or laptop scheduling.
The UNO can adjust left/right PWM for yaw, so the requested base PWM is not an
independently enforced per-motor limit.

**Look for:** a brief twitch/spin followed by all four wheels stopping. Record
which wheels moved and whether their direction would propel the car toward its
ultrasonic sensor. Both completion and STOP replies are software acknowledgements.
If any wheel continues spinning, use **physical power OFF**. If nothing moves,
report that and the full output before increasing speed or changing wiring.
Do not put the car on the ground for this first test.

The tool accepts other directions for later individual bench tests; use only the
forward test first. Base PWM is capped at 100/255 and durations at 1000 ms; zero
duration, missing `--wheels-raised`, and out-of-range arguments are rejected before
opening a socket. The dashboard hardware adapter remains disabled.

## Test 3: one-second forward bench pulse

After the initial pulse spins all four wheels forward and all stop, keep the
same securely supported, wheels-raised arrangement, base PWM, power, and Cam
configuration. Keep hands clear and physical power OFF available. Run once:

```sh
python3 /Users/faiyaz/Bullseye/mission-control/server/bullseye/elegoo_bench.py forward --duration-ms 1000 --speed 80 --wheels-raised --verbose
```

This requests **one second** of forward rotation, followed by STOP. Watch whether
all wheels continue turning and whether all stop afterward; if any keeps spinning,
switch physical power OFF. Save the output and note whether rotation was steady,
uneven, or stuttering. Do not put the rover on the ground for this test.

The host waits for the unique completion reply for at most a nominal **1.1 seconds**
before requesting STOP, while continuing to echo ESP32 heartbeats. Scheduling,
UART transit, and the factory loop can delay the physical response. The CLI's
default remains 200 ms; 1000 ms is its maximum accepted duration. It still sends
one N2, never automatically retries, and requires the preflight STOP reply.

## Before dashboard movement

The separate browser **bench demo** below exposes finite steps under the same
wheels-raised conditions. This does not enable continuous driving in the mission
dashboard, validate command-loss timing, or enable autonomous navigation.

Do not enable dashboard hardware controls based solely on this probe. First verify
each direction with wheels elevated, then verify stopping and command-loss behavior.
The source maps timed command **N2** as left=1, right=2, forward=3, backward=4;
its fields are `D1` direction, `D2` base PWM speed, and `T` duration in milliseconds.
Physical wheel direction has not been verified. `T=0` disables its duration limit.
The N3/N4 paths have no command duration limit and are unsuitable for this initial
test. No motion command is exposed by the diagnostic.

The factory timeout is checked from the UNO loop, which also performs a blocking
ultrasonic measurement. Do not assume a strict 300 ms device stop deadline or use
the ESP32's several-second heartbeat as its replacement. The teammate's firmware
work must establish the independent motion timeout before real dashboard driving
is enabled. The existing hardware adapter remains disabled.

## Local browser control demo

Build the updated frontend once with Node 22.12+ (or Node 24), from any directory:

```sh
(cd /Users/faiyaz/Bullseye/mission-control/web && npm run build)
/Users/faiyaz/Bullseye/mission-control/run-rover-demo.sh
```

Open **http://localhost:8001/bench**. Port 8001 keeps the existing simulation on
8000 available. The script uses the existing Python virtualenv; there are no new
dependencies. It explicitly enables `BULLSEYE_MODE=hardware` and
`BULLSEYE_BENCH_ENABLED=1`. A normal server launch keeps the bench API disabled.
No TCP command connection or movement is opened automatically.

1. Secure the rover with all wheels raised; physical power OFF must remain in
   reach. Use battery power, USB disconnected, switch **Cam**, and close all app
   or CLI controllers. Join `ELEGOO-2436CBF61B44` on the laptop.
2. Open the page and **Claim controls**. Click **Connect rover bridge**. This
   opens one TCP connection, exchanges STOP and a uniquely identified sensor
   query, and then echoes ESP32 heartbeats continuously. Failed connections do
   not automatically reconnect or replay movement.
3. Confirm the wheels-raised checkbox, then **Enable single steps**. Start with
   **Forward**, base PWM **40**, duration **0.2 seconds**. Each button click sends
   one N2 followed by STOP. Holding does not repeat, overlapping steps are
   rejected, and delayed requests from the preceding step cannot advance.
4. Test reverse/left/right individually and observe the actual wheel direction.
   The page offers 0.2/0.5/1 second steps and base PWM 40–100. These are requested
   values; existing firmware may adjust motor PWM and delay physical stopping.
5. **STOP** is available to every viewer, latches steps off, and interrupts the
   pending action without waiting for its completion. It reports software STOP
   acknowledgements separately from physical verification. Check the wheels.
   Re-enable explicitly after STOP; reconnection never resumes motion.

Arrow keys/WASD send one step on initial keydown, with no key-repeat; releasing
that key requests STOP early. Space requests latched STOP. Page blur, hiding,
pilot WebSocket disconnect, or a three-second pilot lease expiry requests STOP
and disables steps. Ownership renews once per second; latency pings do not renew
it. Mouse/touch direction buttons intentionally complete their single timed step
after a click rather than implementing hold-to-drive.

The frontend, `/ws/bench`, `/api/bench/state`, `/api/bench/stop`, and shared
`/ws/video` use the same origin. Browser commands are acknowledged when accepted
by the coordinator; the activity log later reports hardware completion/failure.
One browser owns the pilot seat; another can watch or STOP. Command IDs are
deduplicated, and the arm token rotates after each action and on re-enabling.
Only one hardware operation can run at a time; there is no movement queue.

The existing camera relay defaults to **http://192.168.4.1:81/stream** in bench
mode. This is the vendor-source URL and remains a physical setup check. Override
`CAMERA_STREAM_URL` in `mission-control/server/.env` if the known working stream
uses another URL. `ROVER_HOST` and `ROVER_PORT` are laptop-only settings. Camera
configuration/credentials are never editable from the shared page. Distance is
a connection-time, uncalibrated snapshot, not collision protection.

Without a second Internet connection the local page still works on rover Wi-Fi.
Chat and Cloudflare Tunnel will not. When dual connectivity is available, use
`cloudflared tunnel --url http://127.0.0.1:8001` and append `/bench` to the public
URL. The normal mission dashboard still uses the disabled hardware stub; the
global `/api/stop` also stops this bench controller. Real autonomy and continuous
manual driving await firmware timeout validation.

## Source references

- `firmware/esp32-camera/ESP32_CameraServer_AP_20220120/ESP32_CameraServer_AP_20220120.ino`:
  TCP port 100, brace framing, heartbeat echo, UART relay, disconnect STOP.
- `firmware/arduino-uno/SmartRobotCarV4.0_V1_20230201/ApplicationFunctionSet_xxx0.cpp`:
  N100 STOP, N21 distance query, N2 timed movement and direction mapping.
- The same folder's `DeviceDriverSet_xxx0.cpp`: zero/no echo and 150 cm clamp.
- [Source provenance and protocol PDF](SOURCE_REFERENCES.md).
