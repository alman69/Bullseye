import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Cable, CircleDot, Radio, ShieldCheck, Square, Terminal, Unplug, Wifi } from "lucide-react";
import HalftoneNebula from "@/components/ui/halftone-nebula";
import { Button } from "@/components/ui/button";
import { CameraView } from "@/components/camera-view";
import { useMission } from "@/hooks/use-mission";

const SKY = { planetX: 0.74, planetY: 0.2 };
const KEYS: Record<string, string> = { ArrowUp: "forward", w: "forward", ArrowDown: "backward", s: "backward", ArrowLeft: "left", a: "left", ArrowRight: "right", d: "right" };

export default function RoverDemo() {
  const { state, connected, isPilot, act, stop, error, clearError, latency } = useMission("/ws/bench", "/api/bench/stop");
  const [speed, setSpeed] = useState(40);
  const [duration, setDuration] = useState(200);
  const [wheelsRaised, setWheelsRaised] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const bench = state?.bench;
  const canStep = !!(connected && isPilot && bench?.armed && bench.status === "ready" && !bench.busy && !bench.stop_pending);
  const tokenUsed = useRef<string | null>(null);
  const keyboard = useRef<string | null>(null);
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
  const stepRef = useRef(step);
  stepRef.current = step;

  useEffect(() => {
    if (!bench?.armed || !connected || !isPilot) setWheelsRaised(false);
  }, [bench?.armed, connected, isPilot]);

  useEffect(() => {
    if (bench?.armed && window.matchMedia?.("(max-width: 800px)")?.matches)
      stage.current?.scrollIntoView?.({ block: "start", behavior: "instant" });
  }, [bench?.armed]);

  useEffect(() => {
    const safetyStop = () => {
      keyboard.current = null;
      if (latest.current.isPilot && (latest.current.bench?.armed || latest.current.bench?.busy)) void latest.current.stop();
    };
    const hidden = () => { if (document.hidden) safetyStop(); };
    const down = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.code === "Space" && !target?.closest("input[type=checkbox]")) { event.preventDefault(); void latest.current.stop(); return; }
      if (target?.closest("input,select,textarea,[role=slider]")) return;
      if (target?.closest("a") || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
      const direction = KEYS[event.key];
      if (!direction || !latest.current.canStep) return;
      event.preventDefault();
      keyboard.current = event.key;
      stepRef.current(direction);
    };
    const up = (event: KeyboardEvent) => {
      if (event.key === keyboard.current) {
        keyboard.current = null;
        if (latest.current.isPilot) void latest.current.act("halt");
      }
    };
    window.addEventListener("blur", safetyStop);
    window.addEventListener("pagehide", safetyStop);
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("blur", safetyStop);
      window.removeEventListener("pagehide", safetyStop);
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, []);

  const previous = useRef({ connected, isPilot });
  useEffect(() => {
    if (previous.current.connected && previous.current.isPilot && !connected) void stop();
    previous.current = { connected, isPilot };
  }, [connected, isPilot, stop]);

  const bridgeReady = bench?.status === "ready";
  const busy = !!(bench?.busy || bench?.stop_pending);
  return <>
    <div className="nebula-backdrop" aria-hidden="true"><HalftoneNebula preset="bullseye" params={SKY} interactive={false} maxDpr={1} maxFps={30} /><div className="nebula-shade" /></div>
    <div className="app-shell bench-shell" data-armed={!!bench?.armed}>
      <header className="topbar">
        <a className="brand" href="/bench"><img className="event-badge brand-badge" src="/assets/ub-hacking-2026.png" alt="UB Hacking 2026 badge" /><span>BULLSEYE<span className="brand-sub">ROVER CONTROL DEMO</span></span></a>
        <div className="bench-header-right"><span className="micro">REAL HARDWARE · BENCH</span><Button className="stop-button" onClick={() => void stop()} aria-label="STOP rover"><Square size={12} fill="currentColor" /> STOP</Button></div>
      </header>
      <main className="bench-main">
        <div className="bench-title"><div><p className="eyebrow">MANUAL OPERATIONS / 01</p><h1>Take the controls<span>.</span></h1><p>One click. One timed step. A real connection to Bullseye.</p></div><span className={`bench-pill ${canStep ? "ready" : ""}`}><CircleDot size={14} />{bench?.direction ? `${bench.direction.toUpperCase()} STEP` : bench?.stop_pending ? "STOP REQUESTED" : bench?.armed ? "STEPS ENABLED" : "STEPS DISABLED"}</span></div>
        <div className="bench-notice"><ShieldCheck size={20} /><p><strong>Wheels raised for this demo.</strong> Secure all four wheels clear of the surface and keep physical power OFF within reach. Forward was bench-tested; reverse and turns still need individual checks. No obstacle avoidance is active.</p></div>
        {error && <div className="bench-error" role="alert">{error}<Button variant="ghost" onClick={clearError}>Dismiss</Button></div>}
        <div className="bench-grid" ref={stage}>
          <div className="bench-left">
            <CameraView state={state} connected={connected} onStop={() => void stop()} />
            <section className="panel bench-status" aria-label="Connection diagnostics">
              <div><Radio size={17} /><span>Browser → laptop</span><strong>{connected ? latency.rttMs != null ? `${latency.rttMs} ms` : "Connected" : "Disconnected"}</strong><small>Round-trip to this server only</small></div>
              <div><Cable size={17} /><span>ESP32 → UNO</span><strong>{bridgeReady ? "UNO replied" : bench?.status === "connecting" ? "Connecting…" : "Not connected"}</strong><small>{bench?.heartbeats || 0} ESP32 heartbeats echoed</small></div>
              <div><ShieldCheck size={17} /><span>Last STOP reply</span><strong>{bench?.stop_pending ? "Waiting…" : state?.last_stop_acknowledged === true ? "Acknowledged" : "Unverified"}</strong><small>Software reply; inspect wheels</small></div>
              <div><Wifi size={17} /><span>Ultrasonic snapshot</span><strong>{state?.distance_cm != null ? `${state.distance_cm} cm` : "Unavailable"}</strong><small>At connection · uncalibrated</small></div>
            </section>
          </div>
          <div className="bench-right">
            <section className="panel bench-pilot" aria-label="Pilot and bridge setup">
              <div className="panel-heading"><div className="heading-icon"><Radio size={16} /><h2>Pilot seat</h2></div><span className="micro">{isPilot ? "YOU HAVE CONTROL" : state?.pilot_id ? "VIEWER" : "AVAILABLE"}</span></div>
              <div className="bench-panel-body">
                <div className="bench-pilot-row"><div><strong>{isPilot ? "You’re the pilot" : state?.pilot_id ? "Another pilot is connected" : "Ready when you are"}</strong><p>One pilot. STOP is available to everyone.</p></div><Button variant="outline" disabled={!connected || (!isPilot && !!state?.pilot_id)} onClick={() => void act(isPilot ? "release" : "claim")}>{isPilot ? "Release" : "Claim controls"}</Button></div>
                <Button className="bench-connect" variant="outline" disabled={!connected || !isPilot || busy || !!bench?.armed} onClick={() => void act("bridge_connect")}><Unplug size={16} />{bench?.busy && !bench.direction ? "Checking bridge…" : bridgeReady ? "Reconnect bridge" : "Connect rover bridge"}</Button>
                <p className="bench-help">Join the ELEGOO Wi-Fi first. Close the phone app and CLI tests. Battery on, USB disconnected, shield switch on Cam.</p>
                <label className="bench-checkbox"><input type="checkbox" checked={wheelsRaised} disabled={!isPilot || !connected || !!bench?.armed} onChange={(event) => setWheelsRaised(event.target.checked)} /><span>All four wheels are securely raised. I can reach the power switch.</span></label>
                <Button className="primary-button bench-enable" disabled={!connected || !isPilot || !bridgeReady || busy || !wheelsRaised || !!bench?.armed} onClick={() => void act("bench_arm", { wheels_raised: true })}>{bench?.armed ? "Single steps enabled" : "Enable single steps"}</Button>
                {bench?.error && <p className="bench-inline-error" role="status">{bench.error}</p>}
              </div>
            </section>
            <section className="panel bench-controls" aria-label="Timed direction controls">
              <div className="panel-heading"><div className="heading-icon"><CircleDot size={16} /><h2>Step controls</h2></div><span className="micro">NO AUTO-REPEAT</span></div>
              <div className="bench-panel-body">
                <div className="bench-pad">
                  <Button className="bench-up" disabled={!canStep} aria-label="Step forward" onClick={() => step("forward")}><ArrowUp /><span>Forward</span></Button>
                  <Button className="bench-turn" disabled={!canStep} aria-label="Step left" onClick={() => step("left")}><ArrowLeft /><span>Left</span></Button>
                  <Button className="stop-button bench-center" aria-label="STOP rover from direction pad" onClick={() => void stop()}><Square fill="currentColor" />STOP</Button>
                  <Button className="bench-turn" disabled={!canStep} aria-label="Step right" onClick={() => step("right")}><ArrowRight /><span>Right</span></Button>
                  <Button className="bench-down" disabled={!canStep} aria-label="Step backward" onClick={() => step("backward")}><ArrowDown /><span>Reverse</span></Button>
                </div>
                <p className="bench-help bench-keyboard">Click for one step. Or tap arrows / WASD; keyboard release requests STOP. Space stops and disables steps.</p>
                <label className="bench-speed" htmlFor="bench-speed">Base speed <strong>{speed}<small> / 255 PWM</small></strong></label>
                <input id="bench-speed" className="bench-range" type="range" min={40} max={100} step={5} value={speed} disabled={busy} onChange={(event) => setSpeed(Number(event.target.value))} />
                <label className="bench-duration" htmlFor="bench-duration">Step duration<select id="bench-duration" value={duration} disabled={busy} onChange={(event) => setDuration(Number(event.target.value))}><option value={200}>0.2 seconds</option><option value={500}>0.5 seconds</option><option value={1000}>1 second</option></select></label>
                <p className="bench-help">STOP, focus loss, or pilot loss disables steps. Factory timing can vary; a reply does not independently verify the wheels stopped.</p>
              </div>
            </section>
          </div>
        </div>
        <section className="panel bench-log"><div className="panel-heading"><div className="heading-icon"><Terminal size={16} /><h2>Activity log</h2></div><span className="micro">LATEST {state?.events.length || 0} EVENTS</span></div><div className="bench-log-body">{state?.events.slice(0, 12).map((event) => <div className={`bench-log-row ${event.level}`} key={event.id}><time>{new Date(event.at).toLocaleTimeString()}</time><p>{event.message}</p></div>) || <p>Waiting for the bench server. Launch mission-control/run-rover-demo.sh.</p>}</div></section>
        <footer className="bench-footer">BULLSEYE / UB HACKING 2026<span>MANUAL BENCH DEMO · AUTONOMOUS MISSIONS DISABLED</span></footer>
      </main>
    </div>
  </>;
}
