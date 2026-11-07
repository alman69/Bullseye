# Direct ELEGOO connection: first bench test

The preserved factory source exposes a raw TCP server at **192.168.4.1:100** on
the ESP32 access point. The phone app is not needed to speak this protocol.
The procedure below reproduces the owner's successful connection tests.
No firmware changes or flashing are involved.

**Owner-reported test, November 7, 2026:** the laptop connected to
`ELEGOO-2436CBF61B44`, exchanged ESP32 heartbeats, received `{ok}` for STOP,
and received a uniquely matched UNO distance reply. Distance consistently reads
short and remains uncalibrated. The owner subsequently reported all four wheels
turning forward during the initial bench pulse. Physical stopping latency and
behavior on control loss have not been measured.
The owner also reported the one-second forward test working, including slower
rotation at base PWM 40. Individual wheel-speed calibration remains pending.
The owner subsequently reported successful operation of the browser controller
on the ground. This is a functional test, not calibration or a stop-latency
measurement. The browser now supports held-key input and live distance updates.

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

The separate browser controller below supports held keys and finite button steps.
This does not enable autonomous navigation or validate a strict hardware stop
deadline. The command-line bench test above retains its original test conditions.

Do not enable dashboard hardware controls based solely on this probe. First verify
each direction with wheels elevated, then verify stopping and command-loss behavior.
The source maps timed command **N2** as left=1, right=2, forward=3, backward=4;
its fields are `D1` direction, `D2` base PWM speed, and `T` duration in milliseconds.
Forward direction was owner-verified. `T=0` disables the duration limit.
The N3/N4 paths have no command duration limit and are unsuitable for this initial
test. No motion command is exposed by the diagnostic.

The factory timeout is checked from the UNO loop. N21 ultrasonic queries and
autonomous modes can perform blocking measurements. Do not assume a strict
300 ms device stop deadline or use
the ESP32's several-second heartbeat as its replacement. The teammate's firmware
work must establish the independent motion timeout for reliable navigation.
The normal mission dashboard's hardware adapter remains disabled.

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

1. Use battery power, USB disconnected, switch **Cam**, and close other app or
   CLI controllers. Join `ELEGOO-2436CBF61B44` on the laptop.
2. Open the page and **Claim controls**. Click **Connect rover bridge**. This
   opens one TCP connection, exchanges STOP and a uniquely identified sensor
   query, and then echoes ESP32 heartbeats continuously. Failed connections do
   not automatically reconnect or replay movement.
3. Click the red **Enable controls** button; it becomes **Disable controls**.
   There is no wheels-raised checkbox. Enabling alone never starts movement.
4. Hold **WASD / arrow keys** to drive and release to request STOP. The most
   recently pressed direction wins; releasing it falls back to another key
   still physically held. Native OS key-repeat is ignored. Mouse/touch direction
   buttons keep their existing one-click, one-step behavior with 0.2/0.5/1 second
   durations. Base PWM remains 40–100 (default 40).
5. **Disable controls**, **STOP**, and **Space** stop and disable movement.
   Every viewer may STOP. Re-enabling after STOP is explicit; reconnecting never
   resumes motion. Page blur/hiding, pilot disconnect, or expired ownership also
   disable controls. A release stops the keyboard gesture without disabling the
   pilot's controls.

Held input has its own **350 ms** server lease, renewed every **100 ms** while
the key is physically held. Only one unacknowledged renewal is sent at a time.
Late renewals/end events cannot restart motion or affect a new gesture. Pilot
ownership separately renews every second and expires after three seconds; latency
pings do not renew it. Browser disconnection clears the held keys; reconnection
does not replay them.

The bridge sends N2 movement with a nonzero **300 ms** firmware timer. It then
waits for a uniquely identified **N22/D1=0** response reading the UNO's cached IR
value before sending another movement update. This read does not change motor
mode or trigger the blocking ultrasonic measurement; its value is only used to
confirm UNO processing, not for navigation. No N3/N4 unbounded movement is sent.
A missing/malformed cached response stops the action rather than queueing moves.
Timed button clicks remain separate and retain their pre/post STOP checks.

Ultrasonic distance updates target **2 Hz while idle** and roughly **1 Hz while
held driving**. N21 performs a blocking measurement inside the UNO, so driving
briefly stops before that query, then resumes only while the current key lease
remains valid. This can produce short pauses; firmware changes would be needed
for smooth driving with nonblocking telemetry. Sensors and motion transactions
are serialized; STOP bypasses that transaction lock. There is no measurement queue.
The browser shows reading age, Live/Stale/Invalid status, zero as **No echo**, and
150 cm as the vendor's clamped **150+ cm**. Distance is uncalibrated and is not
automatic obstacle avoidance. A stale value is not presented as fresh clear space.

The frontend, `/ws/bench`, `/api/bench/state`, `/api/bench/stop`, and shared
`/ws/video` use the same origin. Browser commands are acknowledged when accepted
by the coordinator; the activity log later reports hardware completion/failure.
One browser owns the pilot seat; another can watch or STOP. Command IDs are
deduplicated, and the arm token rotates after each action and on re-enabling.
Only one movement operation can run at a time; there is no movement queue.

The existing camera relay defaults to **http://192.168.4.1:81/stream** in bench
mode. This is the vendor-source URL and remains a physical setup check. Override
`CAMERA_STREAM_URL` in `mission-control/server/.env` if the known working stream
uses another URL. `ROVER_HOST` and `ROVER_PORT` are laptop-only settings. Camera
configuration/credentials are never editable from the shared page.

Without a second Internet connection the local page still works on rover Wi-Fi.
Chat and Cloudflare Tunnel will not. When dual connectivity is available, use
`cloudflared tunnel --url http://127.0.0.1:8001` and append `/bench` to the public
URL. The normal mission dashboard still uses the disabled hardware stub; the
global `/api/stop` also stops this manual controller. Autonomous navigation and
strict physical stop-time guarantees still require firmware validation. No
firmware is modified by the browser controller.

## Source references

- `firmware/esp32-camera/ESP32_CameraServer_AP_20220120/ESP32_CameraServer_AP_20220120.ino`:
  TCP port 100, brace framing, heartbeat echo, UART relay, disconnect STOP.
- `firmware/arduino-uno/SmartRobotCarV4.0_V1_20230201/ApplicationFunctionSet_xxx0.cpp`:
  N100 STOP, N21 distance query, N22 cached IR response, N2 timed movement and direction mapping.
- The same folder's `DeviceDriverSet_xxx0.cpp`: zero/no echo and 150 cm clamp.
- [Source provenance and protocol PDF](SOURCE_REFERENCES.md).
