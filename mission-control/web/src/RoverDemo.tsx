import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Cable, CircleDot, Radio, ShieldCheck, Square, Terminal, Unplug, Wifi } from "lucide-react";
import HalftoneNebula from "@/components/ui/halftone-nebula";
import { Button } from "@/components/ui/button";
import { CameraView } from "@/components/camera-view";
import { VisionCalibration } from "@/components/vision-calibration";
import { VisionPanel } from "@/components/vision-panel";
import { ScooperPanel } from "@/components/scooper-panel";
import { AutopilotPanel } from "@/components/autopilot-panel";
import { useVisionUi } from "@/hooks/use-vision-ui";
import { useMission } from "@/hooks/use-mission";
import { useRoverKeyboard } from "@/hooks/use-rover-keyboard";

const SKY = { planetX: 0.74, planetY: 0.2 };

export default function RoverDemo() {
  const { state, connected, isPilot, act, stop, error, clearError, latency } = useMission("/ws/bench", "/api/bench/stop");
  const canCalibrate = connected && isPilot && !state?.bench?.busy && !state?.bench?.stop_pending;
  const visionUi = useVisionUi(act, canCalibrate);
  const [speed, setSpeed] = useState(40);
  const [duration, setDuration] = useState(200);
  const stage = useRef<HTMLDivElement>(null);
  const bench = state?.bench;
  const canStep = !!(connected && isPilot && bench?.armed && bench.status === "ready" && !bench.busy && !bench.stop_pending);
  const tokenUsed = useRef<string | null>(null);
  const latest = useRef({ isPilot, canStep, bench, speed, duration, act, stop });
  latest.current = { isPilot, canStep, bench, speed, duration, act, stop };

  const step = (direction: string) => {
    const current = latest.current;
    const token = current.bench?.arm_token;
    if (!current.canStep || !token || tokenUsed.current === token) return;
    tokenUsed.current = token;
    void current.act("bench_pulse", { direction, speed: current.speed, duration_ms: current.duration, arm_token: token }).then((accepted) => {
      if (!accepted && tokenUsed.current === token) tokenUsed.current = null;
    });
  };
  useRoverKeyboard({
    enabled: !!(connected && isPilot && bench?.armed && bench.status === "ready"),
    isPilot, busy: !!bench?.busy, stopPending: !!bench?.stop_pending,
    token: bench?.arm_token ?? null, speed, reservedToken: tokenUsed, act, stop,
  });

  useEffect(() => {
    if (bench?.armed && window.matchMedia?.("(max-width: 800px)")?.matches)
      stage.current?.scrollIntoView?.({ block: "start", behavior: "instant" });
  }, [bench?.armed]);

  const previous = useRef({ connected, isPilot });
  useEffect(() => {
    if (previous.current.connected && previous.current.isPilot && !connected) void stop();
    previous.current = { connected, isPilot };
  }, [connected, isPilot, stop]);

  const bridgeReady = bench?.status === "ready";
  const busy = !!(bench?.busy || bench?.stop_pending);
  const awaitingPickup = bench?.auto?.state === "awaiting_pickup";
  return <>
    <div className="nebula-backdrop" aria-hidden="true"><HalftoneNebula preset="bullseye" params={SKY} interactive={false} maxDpr={1} maxFps={30} /><div className="nebula-shade" /></div>
    <div className="app-shell bench-shell" data-armed={!!bench?.armed}>
      <header className="topbar">
        <a className="brand" href="/bench"><img className="event-badge brand-badge" src="/assets/ub-hacking-2026.png" alt="UB Hacking 2026 badge" /><span>BULLSEYE<span className="brand-sub">ROVER CONTROL</span></span></a>
        <div className="bench-header-right"><span className="micro">REAL ROVER · SEARCH + DRIVE</span><Button className="stop-button" onClick={() => void stop()} aria-label="STOP rover"><Square size={12} fill="currentColor" /> STOP</Button></div>
      </header>
      <main className="bench-main">
        <div className="bench-title"><div><p className="eyebrow">ROVER OPERATIONS / 01</p><h1>Find it. Bring it home<span>.</span></h1><p>Choose a color for automatic search, then pick up the material and bring it home.</p></div><span className={`bench-pill ${bench?.armed ? "ready" : ""}`}><CircleDot size={14} />{bench?.direction ? `${bench.direction.toUpperCase()} · ${bench.drive_mode === "hold" ? "KEYBOARD" : "STEP"}` : bench?.stop_pending ? "STOP REQUESTED" : bench?.armed ? "CONTROLS ENABLED" : "CONTROLS DISABLED"}</span></div>
        {error && <div className="bench-error" role="alert">{error}<Button variant="ghost" onClick={clearError}>Dismiss</Button></div>}
        <div className="bench-grid" ref={stage}>
          <div className="bench-left">
            <CameraView state={state} connected={connected} onStop={() => void stop()} vision={state?.vision?.available ? visionUi.overlay : undefined} />
            {visionUi.calibrating && <VisionCalibration vision={state?.vision} ui={visionUi} canAct={canCalibrate} act={act} />}
            <AutopilotPanel state={state} canStep={canStep} canAct={connected && isPilot && !busy} act={act} onStop={() => void stop()} />
            <VisionPanel vision={state?.vision} ui={visionUi} canAct={canCalibrate} act={act} />
            <section className="panel bench-status" aria-label="Connection diagnostics">
              <div><Radio size={17} /><span>Browser → laptop</span><strong>{connected ? latency.rttMs != null ? `${latency.rttMs} ms` : "Connected" : "Disconnected"}</strong><small>Round-trip to this server only</small></div>
              <div><Cable size={17} /><span>ESP32 → UNO</span><strong>{bridgeReady ? "UNO replied" : bench?.status === "connecting" ? "Connecting…" : "Not connected"}</strong><small>{bench?.heartbeats || 0} ESP32 heartbeats echoed</small></div>
              <div><ShieldCheck size={17} /><span>Last STOP reply</span><strong>{bench?.stop_pending ? "Waiting…" : state?.last_stop_acknowledged === true ? "Acknowledged" : "Unverified"}</strong><small>Software acknowledgement</small></div>
              <div className={`bench-distance ${bench?.distance_status ?? "unavailable"}`} aria-live="polite"><Wifi size={17} /><span>Ultrasonic distance</span><strong>{bench?.distance_status === "invalid" ? "No echo" : state?.distance_cm != null ? `${state.distance_cm >= 150 ? "150+" : state.distance_cm} cm` : "Unavailable"}</strong><small>{bench?.distance_status === "stale" ? "Stale" : bench?.distance_status === "live" ? "Live" : bench?.distance_status === "invalid" ? "Invalid" : "Awaiting reading"}{bench?.distance_age_ms != null ? ` · ${(bench.distance_age_ms / 1000).toFixed(1)}s ago` : ""} · uncalibrated</small></div>
            </section>
          </div>
          <div className="bench-right">
            <section className="panel bench-pilot" aria-label="Pilot and bridge setup">
              <div className="panel-heading"><div className="heading-icon"><Radio size={16} /><h2>Pilot seat</h2></div><span className="micro">{isPilot ? "YOU HAVE CONTROL" : state?.pilot_id ? "VIEWER" : "AVAILABLE"}</span></div>
              <div className="bench-panel-body">
                <div className="bench-pilot-row"><div><strong>{isPilot ? "You’re the pilot" : state?.pilot_id ? "Another pilot is connected" : "Ready when you are"}</strong><p>One pilot. STOP is available to everyone.</p></div><Button variant="outline" disabled={!connected || (!isPilot && !!state?.pilot_id)} onClick={() => void act(isPilot ? "release" : "claim")}>{isPilot ? "Release" : "Claim controls"}</Button></div>
                <Button className="bench-connect" variant="outline" disabled={!connected || !isPilot || busy || !!bench?.armed} onClick={() => void act("bridge_connect")}><Unplug size={16} />{bench?.busy && !bench.direction ? "Checking bridge…" : bridgeReady ? "Reconnect bridge" : "Connect rover bridge"}</Button>
                <p className="bench-help">Join the ELEGOO Wi-Fi first. Close the phone app and CLI tests. Battery on, USB disconnected, shield switch on Cam.</p>
                <Button className="primary-button bench-enable" aria-pressed={!!bench?.armed} disabled={!connected || !isPilot || (!bench?.armed && (!bridgeReady || busy || awaitingPickup))} onClick={() => bench?.armed ? void stop() : void act("bench_arm")}>{bench?.armed ? "Disable controls" : "Enable controls"}</Button>
                {awaitingPickup && <p className="bench-help">Pick up the material and confirm below the camera before enabling movement.</p>}
                {bench?.error && <p className="bench-inline-error" role="status">{bench.error}</p>}
              </div>
            </section>
            <ScooperPanel scooper={bench?.scooper} canUse={connected && isPilot} onAction={(kind) => void act(kind)} />
            <section className="panel bench-controls" aria-label="Rover direction controls">
              <div className="panel-heading"><div className="heading-icon"><CircleDot size={16} /><h2>Drive controls</h2></div><span className="micro">KEYBOARD + BUTTONS</span></div>
              <div className="bench-panel-body">
                <div className="bench-pad">
                  <Button className="bench-up" disabled={!canStep} aria-label="Step forward" onClick={() => step("forward")}><ArrowUp /><span>Forward</span></Button>
                  <Button className="bench-turn" disabled={!canStep} aria-label="Step left" onClick={() => step("left")}><ArrowLeft /><span>Left</span></Button>
                  <Button className="stop-button bench-center" aria-label="STOP rover from direction pad" onClick={() => void stop()}><Square fill="currentColor" />STOP</Button>
                  <Button className="bench-turn" disabled={!canStep} aria-label="Step right" onClick={() => step("right")}><ArrowRight /><span>Right</span></Button>
                  <Button className="bench-down" disabled={!canStep} aria-label="Step backward" onClick={() => step("backward")}><ArrowDown /><span>Reverse</span></Button>
                </div>
                <p className="bench-help bench-keyboard">Hold WASD or arrow keys to drive. Release to stop. Buttons run one timed step. Space stops and disables controls.</p>
                <label className="bench-speed" htmlFor="bench-speed">Base speed <strong>{speed}<small> / 255 PWM</small></strong></label>
                <input id="bench-speed" className="bench-range" type="range" min={40} max={100} step={5} value={speed} disabled={busy} onChange={(event) => setSpeed(Number(event.target.value))} />
                <label className="bench-duration" htmlFor="bench-duration">Button step duration<select id="bench-duration" value={duration} disabled={busy} onChange={(event) => setDuration(Number(event.target.value))}><option value={200}>0.2 seconds</option><option value={500}>0.5 seconds</option><option value={1000}>1 second</option></select></label>
                <p className="bench-help">Manual driving has no automatic obstacle avoidance. Driving briefly pauses for distance updates. STOP and focus loss disable controls.</p>
              </div>
            </section>
          </div>
        </div>
        <section className="panel bench-log"><div className="panel-heading"><div className="heading-icon"><Terminal size={16} /><h2>Activity log</h2></div><span className="micro">LATEST {state?.events.length || 0} EVENTS</span></div><div className="bench-log-body">{state?.events.slice(0, 12).map((event) => <div className={`bench-log-row ${event.level}`} key={event.id}><time>{new Date(event.at).toLocaleTimeString()}</time><p>{event.message}</p></div>) || <p>Waiting for the bench server. Launch mission-control/run-rover-demo.sh.</p>}</div></section>
        <footer className="bench-footer">BULLSEYE / UB HACKING 2026<span>AUTOMATIC SEARCH · MANUAL PICKUP + RETURN</span></footer>
      </main>
    </div>
  </>;
}
