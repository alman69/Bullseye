import { useRef, useState } from "react";
import { Check, Flag, Navigation, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { MissionState } from "@/lib/protocol";
import { CLASS_HEX, displayName } from "@/lib/vision";

const ORES = ["red", "green"] as const;
const STAGES = ["Scan 360°", "Approach · 15 cm", "Drive + grab", "Bring home"];
const SEARCHING = ["running", "scanning", "relocating", "approaching"];

/** Autonomous search and approach, then the pilot drives up, grabs the sample and brings it home. */
export function AutopilotPanel({ state, canStep, canAct, act, onStop }: {
  state: MissionState | null;
  canStep: boolean;
  canAct: boolean;
  act: (type: string, payload?: Record<string, unknown>) => Promise<boolean>;
  onStop: () => void;
}) {
  const [selectedColor, setColor] = useState<(typeof ORES)[number] | null>(null);
  const color = selectedColor ?? (state?.vision?.target === "green" ? "green" : "red");
  const [pending, setPending] = useState(false);
  const requestPending = useRef(false);
  const bench = state?.bench;
  const auto = bench?.auto;
  const mode = auto?.state ?? "idle";
  const token = bench?.arm_token;
  const searching = SEARCHING.includes(mode);
  const pickup = mode === "manual_pickup";
  const complete = mode === "home_arrived" || mode === "done";
  const inMission = searching || pickup;
  const target = inMission ? auto?.target ?? color : color;
  const name = displayName(target);
  const ready = canStep && !!token && !!state?.vision?.available && !inMission && !pending;
  const stage = complete ? 4 : pickup ? 2 : mode === "approaching" ? 1 : searching ? 0 : -1;
  const title = pickup ? `Stopped 15 cm from the ${name}. You have control.`
    : complete ? "Sample home. Mission complete."
    : mode === "approaching" ? `Found the ${name}. Approaching to 15 cm.`
    : mode === "relocating" ? "Exploring toward open space."
    : searching ? `Scanning all around for the ${name}.`
    : mode === "failed" ? "Search paused. Check the rover."
    : mode === "stopped" ? "Stopped."
    : "Choose a sample. Start the search.";
  const instruction = pickup ? "Autonomy is off. Drive up with WASD / arrow keys, Squeeze to grab it, drive it back to home base, then press Delivered."
    : complete ? "Delivery confirmed. Choose a sample to start another search."
    : mode === "approaching" ? "The rover centers the sample and uses the ultrasonic sensor and camera to stop about 15 cm away."
    : mode === "relocating" ? "Not in the 360° scan. The rover drives toward the most open direction; at a wall it checks left and right by ultrasonic and takes the more open side. No second 360."
    : searching ? "One full 360° turn, checking the camera after each step. STOP is always available."
    : mode === "failed" || mode === "stopped" ? "Check the camera, sensor and surroundings, then start a new search."
    : "One 360° scan → explore toward open space (checking sides at walls) until found. It stops 15 cm away and hands you the controls.";
  const distance = state?.distance_cm;
  const distanceLive = bench?.distance_status === "live" && distance != null;
  const range = distanceLive ? `${distance >= 150 ? "150+" : distance} cm`
    : bench?.distance_status === "invalid" ? "No echo"
    : bench?.distance_status === "stale" ? "Stale"
    : "Waiting";
  const run = async (type: string, payload?: Record<string, unknown>) => {
    if (requestPending.current) return;
    requestPending.current = true;
    setPending(true);
    try { await act(type, payload); }
    finally { requestPending.current = false; setPending(false); }
  };

  return <section className="panel autopilot-panel" aria-label="Sample mission">
    <div className="panel-heading">
      <div className="heading-icon"><Navigation size={16} /><h2>Sample mission</h2></div>
      <span className={`micro ${mode === "failed" ? "warn-text" : "muted"}`}>{searching ? "ROVER SEARCHING" : pickup ? "YOUR TURN · MANUAL CONTROL" : complete ? "COMPLETE" : mode === "stopped" || mode === "failed" ? "STOPPED" : "READY WHEN YOU ARE"}</span>
    </div>
    <div className="vision-body">
      <div className="autopilot-readouts" aria-label="Mission telemetry">
        <div><span>Target</span><strong style={{ color: CLASS_HEX[target] }}>{name}</strong></div>
        <div className={distanceLive ? "range-live" : "range-unavailable"}><span>Ultrasonic range</span><strong>{range}</strong></div>
        <div><span>Stop distance</span><strong>15 <small>cm</small></strong></div>
      </div>
      <div className={`autopilot-guidance ${pickup ? "needs-action" : complete ? "complete" : ""}`} role="status" aria-atomic="true">
        <h3>{title}</h3>
        <p>{instruction}</p>
      </div>
      <ol className="autopilot-stages" aria-label="Mission steps">
        {STAGES.map((label, index) => <li key={label} className={index < stage ? "done" : index === stage ? "current" : ""} aria-current={index === stage ? "step" : undefined}>
          <span>{index < stage ? <Check size={13} aria-hidden="true" /> : index + 1}</span>{label}
        </li>)}
      </ol>
      {!inMission && <div className="vision-targets autopilot-colors" role="group" aria-label="Sample">
        {ORES.map((c) => <button key={c} type="button" className={`vision-chip ${color === c ? "selected" : ""}`}
          style={{ "--chip": CLASS_HEX[c] } as React.CSSProperties} aria-pressed={color === c} disabled={!canAct || pending} onClick={() => { setColor(c); void act("vision_target", { color: c }); }}>{displayName(c)}</button>)}
      </div>}
      <div className="autopilot-actions">
        {pickup ? <Button className="autopilot-start" disabled={!canAct || pending || !auto?.run_id} onClick={() => void run("auto_home_confirm", { run_id: auto?.run_id })}><Flag size={16} /> Delivered to home base</Button>
          : <Button className="autopilot-start" disabled={!ready} onClick={() => void run("auto_mission", { color, avoid: true, arm_token: token })}><Navigation size={16} />{searching ? "Search in progress…" : `Search for the ${displayName(color)}`}</Button>}
        <Button className="stop-button" aria-label="STOP sample mission" onClick={onStop}><Square size={14} fill="currentColor" /> STOP</Button>
      </div>
      {!canAct && <p className="vision-note">{bench?.busy ? "Rover is working. STOP is available at any time." : "Waiting for the pilot seat…"}</p>}
      {canAct && !inMission && !canStep && <p className="vision-note">Connecting to the rover…</p>}
      {canAct && !inMission && canStep && !state?.vision?.available && <p className="vision-warning">Start the camera and vision feed before searching.</p>}
      {auto?.message && <div className="autopilot-detail"><span className="micro">LATEST ROVER UPDATE</span><p role={mode === "failed" ? "alert" : undefined} className={mode === "failed" ? "vision-warning" : "vision-message"}>{auto.message}</p></div>}
    </div>
  </section>;
}
