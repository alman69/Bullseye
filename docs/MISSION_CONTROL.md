# Bullseye Mission Control

A laptop-hosted React/TypeScript dashboard with a FastAPI coordinator, a clearly
labeled simulator, and a shared camera relay. No existing firmware is changed.
The mission dashboard's hardware adapter intentionally has all motion/mission
capabilities disabled. The separate [real-rover controller](ELEGOO_CONNECTION.md#local-browser-control-demo)
at `/bench` supports enabled manual control, held WASD, timed button steps, and
live uncalibrated ultrasonic readings using the original ELEGOO firmware.
The UB Hacking badge and its navy, cream, steel-blue, and rocket-red palette frame
the Bullseye identity; the supplied event lettering is preserved.

## Setup and launch

Use **Node 22.12+** (Node 24 is also supported), npm, and **Python 3.13+**.
The laptop's default Node 20.11 is too old for the current Vite toolchain; select
a newer runtime before running npm. Dependencies are locked in package-lock.json
and server/requirements.lock.txt.

On this laptop, the existing installation starts from **any terminal directory**:

```sh
/Users/faiyaz/Bullseye/mission-control/run.sh
```

Do not reinstall dependencies just to restart the preview. If you are already in
mission-control, use `./run.sh` (the `./` is required by zsh).

For a fresh installation at this repository location, these commands stop if a
directory change or installation fails. Adjust the absolute checkout path on a
different machine:

```sh
(cd /Users/faiyaz/Bullseye/mission-control/web && npm ci && npm run build)
(cd /Users/faiyaz/Bullseye/mission-control/server && python3.13 -m venv .venv && .venv/bin/python -m pip install -r requirements.lock.txt -e '.[test]')
/Users/faiyaz/Bullseye/mission-control/run.sh
```

Open **http://localhost:8000**. Claim the pilot seat, choose a sample, and launch.
The simulator searches for three seconds, approaches for three seconds, and then
stops for review. Confirm or reject the frozen candidate image. After collection,
select Return Home, use the manual controls if desired, and select Mark Home.
At widths up to 800 px, **Mission** contains objectives and sample decisions;
**Pilot** contains the shared camera and enlarged manual/return controls. Manual
input stops when leaving Pilot, opening a drawer, releasing input, losing focus,
or losing the control link. Switching tabs keeps the same camera viewer mounted.
STOP is available in the header, fullscreen camera, field guide, and Connections.

Open **Connections** to inspect browser connectivity, measured browser-to-laptop
round-trip time, camera source/frame age, pending ESP32/UNO integration, and the
current page address. A public HTTPS address does not establish rover connectivity.
The diagnostics never reveal the configured camera URL or credentials.

For development, run the backend with `./mission-control/run.sh --reload` and
`npm run dev` in mission-control/web in another terminal. Open Vite's printed URL;
its `/api` and `/ws` paths proxy to port 8000. Rebuild to update the port-8000 UI.

The background demonstration is retained as `components/ui/demo.tsx`. The dashboard
uses the supplied HalftoneNebula with the Bullseye palette, a right-hand planet, no pointer
interaction, DPR capped at 1, and 30 FPS. Its rendering pauses when hidden and
becomes a static frame for reduced motion; a CSS sky handles missing WebGL2.
The generated shadcn aliases map `@/*` to the frontend root, with reusable UI in
`components/ui` and global Tailwind/theme styles in `src/index.css`. The CLI was
initialized with `shadcn init -d -y`, followed by `shadcn add button card badge
alert-dialog slider sheet -y`. No external images are required.
Only the needed shadcn CSS variants are retained locally with their MIT notice;
the CLI itself is not a runtime dependency. Add future components through npx.

## Camera and operating mode

Copy server/.env.example to server/.env and restart the backend after changes.
The startup script loads that file. Direct Uvicorn launches require `--env-file
.env` or exported environment variables.

| Setting | Behavior |
| --- | --- |
| `BULLSEYE_MODE=simulation` | Default; simulated navigation, RGB, and movement |
| `BULLSEYE_MODE=hardware` | Hardware stub; motion and mission controls unavailable |
| `CAMERA_STREAM_URL=http://…` | Relay your real HTTP JPEG stream in either mode |
| `BULLSEYE_SIM_FAIL_COLLECTION_ONCE=1` | First collection fails; Retry then succeeds |

With no camera URL, simulation generates labeled training frames. Hardware mode
instead reports an unconfigured camera. A configured camera failure never silently
switches to simulated footage. **Real video with simulation mode still means
simulated mission behavior.** The UI labels both sources independently.

The vendor source suggests `http://192.168.4.1:81/stream`; this is an example,
not verification of the installed firmware. Use the actual working stream URL.
Only the backend opens it, so remote visitors need no route to 192.168.4.1.
One upstream connection serves every viewer. JPEG extraction handles split
multipart boundaries, bounds individual frames to 2 MB, limits output to 10 FPS,
and keeps only the newest queued frame per viewer. Two-second-old frames are
marked stale; acquisition retries after connection errors. Review snapshots stay
frozen independently of the continuing video; the latest 20 are retained in memory.

## Share through a tunnel

Install cloudflared from Cloudflare's official distribution, start the built app,
then run:

```sh
cloudflared tunnel --url http://127.0.0.1:8000
```

Share the HTTPS URL printed by cloudflared. Anyone with that link can watch,
claim an available pilot seat, and press STOP. There is no login. Only one pilot
may issue mission, sample-review, or movement commands. This is intentional for
the controlled hackathon demo.

Cloudflare Tunnel supports WebSockets. Quick Tunnels do not support SSE; this app
does not use it. A Quick Tunnel's URL changes on restart and is temporary.
Sources: [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/),
[Cloudflare Tunnel FAQ](https://developers.cloudflare.com/cloudflare-one/faq/cloudflare-tunnels-faq/).

Before a real-camera tunnel demo, verify the laptop can simultaneously:

1. Open the rover stream from the backend's network.
2. Reach the Internet for cloudflared.
3. Serve the public URL on a phone using cellular data, including video and controls.

The camera's access point alone may supply no Internet. Use rover Wi-Fi plus
Ethernet/USB tethering/a second network adapter, or a verified common network.
This application does not reconfigure camera firmware or laptop routing.

### Wi-Fi rover + Ethernet Internet on macOS

```text
Remote browser ↔ HTTPS/WebSockets ↔ Cloudflare
                                      ↕ Ethernet / Internet
                         Laptop: FastAPI and future OpenCV
                                      ↕ Wi-Fi
                                  ESP32 ↔ UNO
```

1. Connect Ethernet to an Internet-connected network and Wi-Fi to the rover AP.
2. In System Settings → Network → action menu → Set Service Order, place Ethernet
   above Wi-Fi. [Apple's instructions](https://support.apple.com/en-om/guide/mac-help/mchlp2711/mac).
3. Check that the two networks use different IP subnets. Discover service/interface
   names with `networksetup -listnetworkserviceorder`; do not assume Ethernet is en0.
4. Inspect routes using the actual rover IP. For the vendor's example IP:

   ```sh
   route -n get default
   route -n get 192.168.4.1
   ```

   Internet traffic should have a working route through Ethernet, while the rover
   address should resolve to its Wi-Fi interface. If both networks overlap in
   address space, resolve that conflict before the demo; service order alone is
   not enough. The example IP is not proof of the physical camera's address.
5. Verify Internet access and fresh real frames in Mission Control simultaneously.
   Do not test this by repeatedly opening additional camera streams: use the shared
   relay's live source and frame-age indicators.
6. Start cloudflared separately, then open its HTTPS URL on a phone using cellular
   data. Verify video, shared mission state, and simulated controls first.

This arrangement does not require macOS Internet Sharing, network bridging,
an Internet-connected ESP32, or remote devices joining rover Wi-Fi. Keep the
laptop awake and both processes running. The selected Internet network must
allow cloudflared's outbound tunnel connections. A temporary Quick Tunnel changes
URL on restart; share the currently printed public URL, not localhost.

Remote latency can interrupt timely movement updates. Real motion must retain
the independent UNO timeout; browser/laptop connectivity is not a physical stop
guarantee. Physical network and rover verification remain a separate milestone.

## API and teammate integration contract

All URLs are relative to the same origin. Video uses binary JPEG messages on
`/ws/video`. There is no OpenCV or autonomous hardware navigation yet. The separate
`/bench` controller implements real manual transport; the mission API below keeps
using the disabled hardware stub until navigation integration is ready.

`GET /api/state` returns the same state broadcast by `/ws/control`: operating mode,
phase, target, mission ID/timestamps, capabilities, current review, collection
error, pilot ID, camera health, requested motion, nullable range, last stop
acknowledgement, LED state, and up to 100 newest-first activity events. Range is
simulated in simulation mode and null when unavailable in hardware mode. Nothing
persists across server restarts; restart does not resume a mission.

On control WebSocket connection the server sends:

```json
{"type":"hello","session_id":"server-generated-session-id"}
```

Commands and acknowledgements:

```json
{"id":"unique-request-id","type":"select_target","payload":{"color":"red"}}
{"type":"ack","id":"unique-request-id","status":"accepted"}
```

Rejected acknowledgements contain `status: "rejected"` and a human-readable
`error`. An accepted acknowledgement means the coordinator accepted the command,
not that the physical rover performed it. State broadcasts have the form
`{"type":"state","state":{...}}` and are the source of truth for UI progress.

| Command | Payload / condition |
| --- | --- |
| `claim`, `heartbeat`, `release` | Empty; heartbeat once per second, ownership expires at 3 seconds |
| `ping` | Empty; any viewer; acknowledgement only, no ownership/deadline changes or rover packets |
| `select_target` | `{ "color": "red" \| "green" \| "blue" }`; idle/completed |
| `start` | Empty; idle/completed and mission capability |
| `confirm`, `reject` | `{ "mission_id": "…", "review_id": "…" }`; current pending review only |
| `retry_collection` | Empty; collecting with a recorded failure |
| `return` | Empty; collected; starts assisted return |
| `mark_home` | Empty; returning; operator confirms home arrival |
| `drive` | `{ "linear": -1..1, "angular": -1..1 }`; idle/completed/returning |
| `stop` | Empty; any visitor, always available |
| `reset` | Empty; pilot only, stopped |

`POST /api/stop` also latches STOP and returns current state. Review images are
served by `GET /api/reviews/{review_id}/image`, or 404 if unavailable/evicted.
The review records its ID, mission ID, requested color, capture time, source,
simulation status, and immutable image URL.

Implement `RoverAdapter` in server/bullseye/adapters.py and select it in create_app
when ready. Keep its capabilities false until each operation has been validated:

- `search(color)` is an async iterator yielding `approaching`, then `candidate`.
  A candidate must already satisfy navigation's distance/obstacle checks. The
  coordinator requests stop, freezes the current fresh frame, and waits for review.
  Reject starts another search iteration for the same mission.
- `collect(color)` returns only after the RGB operation has been acknowledged;
  raise on timeout/failure. This simulates collection, not physical retrieval.
- `begin_return()` prepares assisted return; do not claim autonomous homing.
- `drive(linear, angular, ttl_ms)` uses positive linear for forward and positive
  angular for right turn; the hardware implementation must enforce a 300 ms TTL
  on the UNO itself. Do not infer physical motor polarity without testing it.
- `stop()` must bypass queued motion and return true only for a genuine adapter
  acknowledgement. The stub returns false and the UI says the stop is unverified.
- Cancelled navigation/collection tasks must release their work and never resume
  movement. A real transport must independently handle lost laptop connectivity.
- `led_color` represents the last acknowledged indicator command. It is not
  electrical/visual verification that the LED changed.
- `connected` reports the transport connection, and `distance_cm` is the latest
  valid measured range or null. Never report a missing echo as guaranteed clearance.

Manual input renews every 100 ms. The server stops expired drive requests, requests
stop on pilot disconnect/lease expiry, and never replays commands on reconnect.
STOP cancels background mission work and requires an explicit pilot reset.
A backend/browser timeout cannot guarantee a physical stop after a network or
power failure; the UNO timeout and physical power switch remain necessary.

Browser latency probes run every five seconds over the existing control WebSocket,
with the normal 2.5-second acknowledgement timeout. Missing replies show a timeout,
and disconnection clears the old measurement. They do not replace pilot heartbeats
and do not keep a pilot lease alive. This RTT includes the public tunnel when used;
it does not measure laptop-to-ESP32 or ESP32-to-UNO latency.

## Validation

```sh
cd mission-control/web
npm run build
npm test
cd ../server
.venv/bin/python -m pytest -q
```

Tests cover full missions, rejection, frozen snapshots, outdated/duplicate
decisions, collection failure/retry, STOP during collection, concurrent pilot
claims, invalid movement, drive/lease timeouts, disconnected pilots, hardware
gating, binary video, fragmented HTTP/JPEG input, upstream reconnection, stale
frames, and dropping queued frames for slow viewers.

For manual QA: use two browsers, exercise the mission and public STOP, try keyboard
and touch controls, check phone layout, and test the nebula with reduced motion
and disabled WebGL2. Physical camera, rover integration, and external tunnel
validation require the actual hardware URL and simultaneous Internet access.

Verified on the development laptop on 2026-11-07: the TypeScript/production build,
19 backend tests and 26 frontend tests pass. Browser QA covered the real WebGL2
background, a full simulated mission with rejection and confirmation, assisted
return/completion, two viewers with exclusive pilot ownership, public STOP,
fullscreen/guide STOP access, and phone layout down to a 325 px viewport without
horizontal overflow. Renderer fallback, reduced motion, frame limiting, context
restoration, and remount cleanup are also covered by automated browser-API mocks.
The branding/mobile update additionally checks public-versus-local access labels,
five-second latency probes and recovery, probes that never renew pilot ownership,
held-input stopping on tab/drawer/link changes, keyboard release/focus loss, and
speed-slider visibility after a hidden panel is shown. The desktop layout remains
available, while the phone Pilot view keeps video and the full direction pad close
together with 44 px minimum primary touch targets. The original badge asset was
copied unchanged and checked against the supplied file.
The physical stream, real movement, and public tunnel have not been verified.
