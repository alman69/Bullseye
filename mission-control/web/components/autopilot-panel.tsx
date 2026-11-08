import { useRef, useState } from "react";
import { Check, Flag, Hand, Navigation, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { MissionState } from "@/lib/protocol";
import { CLASS_HEX } from "@/lib/vision";

const ORES = ["red", "green"] as const;
const STAGES = ["Scan 360°", "Approach · 10 cm", "Pick up", "Bring home"];

/** Autonomous search and approach, followed by an explicit manual pickup and return. */
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
  const searching = ["running", "scanning", "relocating", "approaching", "arrived"].includes(mode);
  const pickup = mode === "awaiting_pickup" || mode === "sample_reached";
  const returning = mode === "manual_return";
  const complete = mode === "home_arrived" || mode === "done";
  const inMission = searching || pickup || returning;
  const target = inMission ? auto?.target ?? color : color;
  const ready = canStep && !!token && !!state?.vision?.available && !inMission && !pending;
  const stage = complete ? 4 : returning ? 3 : pickup ? 2 : ["approaching", "arrived"].includes(mode) ? 1 : searching ? 0 : -1;
  const title = pickup ? "Rover stopped. Your turn to collect."
    : returning ? "Carry the material to home base."
    : complete ? "Material home. Mission complete."
    : mode === "approaching" || mode === "arrived" ? `Found ${target}. Approaching 10 cm.`
    : mode === "relocating" ? "Moving to an open space."
    : searching ? `Scanning all around for ${target}.`
    : mode === "failed" ? "Search paused. Check the rover."
    : mode === "stopped" ? "Stopped. Controls disabled."
    : "Choose a color. Start the search.";
  const instruction = pickup ? "Manually grab the material, then confirm below. Rover movement is disabled for pickup."
    : returning ? "Bring the material back yourself, then confirm it is home. To drive the rover back too, enable controls and use the direction pad."
    : complete ? "Your pickup and home delivery are confirmed. Choose a color to start another search."
    : mode === "approaching" || mode === "arrived" ? "The rover aligns with the color and uses fresh ultrasonic readings to stop about 10 cm away."
    : mode === "relocating" ? "No target in the full scan. The rover checks ultrasonic clearance, moves to a nearby opening, then scans again."
    : searching ? "The rover turns in steps through a full 360° if needed. Watch the camera; STOP is always available."
    : mode === "failed" || mode === "stopped" ? "Check the camera, sensor and surroundings. Enable controls when ready to start a new search."
    : "Scan all around → move to a nearby open space if needed → repeat. Once found, stop at 10 cm for your manual pickup.";
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
      <span className={`micro ${mode === "failed" ? "warn-text" : "muted"}`}>{searching ? "ROVER SEARCHING" : pickup || returning ? "YOUR ACTION NEEDED" : complete ? "COMPLETE" : mode === "stopped" || mode === "failed" ? "STOPPED" : "READY WHEN YOU ARE"}</span>
    </div>
    <div className="vision-body">
      <div className="autopilot-readouts" aria-label="Mission telemetry">
        <div><span>Target color</span><strong style={{ color: CLASS_HEX[target] }}>{target}</strong></div>
        <div className={distanceLive ? "range-live" : "range-unavailable"}><span>Ultrasonic range</span><strong>{range}</strong></div>
        <div><span>Stop distance</span><strong>10 <small>cm</small></strong></div>
      </div>
      <div className={`autopilot-guidance ${pickup || returning ? "needs-action" : complete ? "complete" : ""}`} role="status" aria-atomic="true">
        <h3>{title}</h3>
        <p>{instruction}</p>
      </div>
      <ol className="autopilot-stages" aria-label="Mission steps">
        {STAGES.map((label, index) => <li key={label} className={index < stage ? "done" : index === stage ? "current" : ""} aria-current={index === stage ? "step" : undefined}>
          <span>{index < stage ? <Check size={13} aria-hidden="true" /> : index + 1}</span>{label}
        </li>)}
      </ol>
      {!inMission && <div className="vision-targets autopilot-colors" role="group" aria-label="Sample color">
        {ORES.map((c) => <button key={c} type="button" className={`vision-chip ${color === c ? "selected" : ""}`}
          style={{ "--chip": CLASS_HEX[c] } as React.CSSProperties} aria-pressed={color === c} disabled={!canAct || pending} onClick={() => { setColor(c); void act("vision_target", { color: c }); }}>{c}</button>)}
      </div>}
      <div className="autopilot-actions">
        {pickup ? <Button className="autopilot-start" disabled={!canAct || pending || !auto?.run_id} onClick={() => void run("auto_pickup_confirm", { run_id: auto?.run_id })}><Hand size={16} /> I picked up the material</Button>
          : returning ? <Button className="autopilot-start" disabled={!canAct || pending || !auto?.run_id} onClick={() => void run("auto_home_confirm", { run_id: auto?.run_id })}><Flag size={16} /> Material is at home base</Button>
          : <Button className="autopilot-start" disabled={!ready} onClick={() => void run("auto_mission", { color, avoid: true, arm_token: token })}><Navigation size={16} />{searching ? "Search in progress…" : "Start sample search"}</Button>}
        <Button className="stop-button" aria-label="STOP sample mission" onClick={onStop}><Square size={14} fill="currentColor" /> STOP</Button>
      </div>
      {!canAct && <p className="vision-note">{bench?.busy ? "Rover is working. STOP is available at any time." : "Connect and claim pilot controls to continue."}</p>}
      {canAct && !inMission && !canStep && <p className="vision-note">Connect the rover bridge and enable controls to start.</p>}
      {canAct && !inMission && canStep && !state?.vision?.available && <p className="vision-warning">Start the camera and vision feed before searching.</p>}
      {auto?.message && <div className="autopilot-detail"><span className="micro">LATEST ROVER UPDATE</span><p role={mode === "failed" ? "alert" : undefined} className={mode === "failed" ? "vision-warning" : "vision-message"}>{auto.message}</p></div>}
    </div>
  </section>;
}
