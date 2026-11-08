import { useState } from "react";
import { Flag, Navigation } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { MissionState } from "@/lib/protocol";
import { CLASS_HEX } from "@/lib/vision";

const ORES = ["red", "green"] as const;

/** Stop at a sample, celebrate, then automatically return to the black home flag. */
export function AutopilotPanel({ state, canStep, act }: {
  state: MissionState | null;
  canStep: boolean;
  act: (type: string, payload?: Record<string, unknown>) => Promise<boolean>;
}) {
  const [color, setColor] = useState<(typeof ORES)[number]>("red");
  const bench = state?.bench;
  const auto = bench?.auto;
  const token = bench?.arm_token;
  const ready = canStep && !!token && !!state?.vision?.available;
  return <section className="panel autopilot-panel" aria-label="Autopilot">
    <div className="panel-heading">
      <div className="heading-icon"><Navigation size={16} /><h2>Autopilot</h2></div>
      <span className={`micro ${auto?.state === "failed" ? "warn-text" : "muted"}`}>{(auto?.state ?? "idle").toUpperCase().replaceAll("_", " ")}</span>
    </div>
    <div className="vision-body">
      <p className="vision-help">Choose a red or green sample. The rover searches, stops at it, turns its LED green and shows a happy animation, then automatically returns to the black home flag. Blue marks obstacles; brown marks walls. STOP cancels at any time.</p>
      <div className="vision-targets" role="group" aria-label="Sample color">
        {ORES.map((c) => <button key={c} type="button" className={`vision-chip ${color === c ? "selected" : ""}`}
          style={{ "--chip": CLASS_HEX[c] } as React.CSSProperties} aria-pressed={color === c} disabled={!canStep} onClick={() => setColor(c)}>{c}</button>)}
      </div>
      <div className="vision-actions">
        <Button disabled={!ready} onClick={() => void act("auto_mission", { color, avoid: true, arm_token: token })}><Navigation size={14} /> Start sample mission</Button>
        <Button variant="outline" disabled={!ready} onClick={() => void act("auto_return", { avoid: true, arm_token: token })}><Flag size={14} /> Return to base</Button>
      </div>
      <p className="vision-note">Return to base starts a separate home search when you need to recover the rover.</p>
      {!canStep && <p className="vision-note">Connect the rover bridge and enable controls first.</p>}
      {auto?.state === "sample_reached" && <p className="mission-banner collected" role="status">Sample reached · celebrating, then returning home</p>}
      {auto?.state === "home_arrived" && <p className="mission-banner deposited" role="status">Home reached · rover stopped · mission complete</p>}
      <p className="vision-note">Experimental autonomy: verify stop distances in the arena. Search is time-limited; camera arrival alone is an estimate when a mineral sits below the ultrasonic beam.</p>
      {auto?.message && <p className={auto.state === "failed" ? "vision-warning" : "vision-message"}>{auto.message}</p>}
    </div>
  </section>;
}
