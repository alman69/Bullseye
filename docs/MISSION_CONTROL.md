# Bullseye Mission Control

A laptop-hosted React/TypeScript dashboard with a FastAPI coordinator, a clearly
labeled simulator, and a shared camera relay. No existing firmware is changed.
The hardware adapter intentionally has all motion/mission capabilities disabled.

## Setup and launch

Use **Node 22.12+** (Node 24 is also supported), npm, and **Python 3.13+**.
The laptop's default Node 20.11 is too old for the current Vite toolchain; select
a newer runtime before running npm. Dependencies are locked in package-lock.json
and server/requirements.lock.txt.

From the repository root:

```sh
cd mission-control/web
npm ci
npm run build
cd ../server
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt -e '.[test]'
cd ../..
./mission-control/run.sh
```

Open **http://localhost:8000**. Claim the pilot seat, choose a sample, and launch.
The simulator searches for three seconds, approaches for three seconds, and then
stops for review. Confirm or reject the frozen candidate image. After collection,
select Return Home, use the manual controls if desired, and select Mark Home.

For development, run the backend with `./mission-control/run.sh --reload` and
`npm run dev` in mission-control/web in another terminal. Open Vite's printed URL;
its `/api` and `/ws` paths proxy to port 8000. Rebuild to update the port-8000 UI.

The background demonstration is retained as `components/ui/demo.tsx`. The dashboard
uses the supplied HalftoneNebula with crimson, a right-hand planet, no pointer
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
cloudflared tunnel --url http://localhost:8000
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

## API and teammate integration contract

All URLs are relative to the same origin. Video uses binary JPEG messages on
`/ws/video`. There is no OpenCV, autonomous hardware navigation, or real motor
transport implemented yet; that work belongs behind the adapter boundary.

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
17 backend tests, and 6 renderer tests pass. Browser QA covered the real WebGL2
background, a full simulated mission with rejection and confirmation, assisted
return/completion, two viewers with exclusive pilot ownership, public STOP,
fullscreen/guide STOP access, and phone layout down to a 325 px viewport without
horizontal overflow. Renderer fallback, reduced motion, frame limiting, context
restoration, and remount cleanup are also covered by automated browser-API mocks.
The physical stream, real movement, and public tunnel have not been verified.
