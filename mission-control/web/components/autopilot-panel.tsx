import { useState } from "react";
import { Flag, Navigation } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { MissionState } from "@/lib/protocol";
import { CLASS_HEX } from "@/lib/vision";

const ORES = ["red", "green", "blue"] as const; // yellow is the obstacle color, never a target

/** Demo autopilot on /bench: drive to a mineral, then turn 180 degrees and drive to the black flag. */
export function AutopilotPanel({
  state,
  canStep,
  act,
}: {
  state: MissionState | null;
  canStep: boolean;
  act: (type: string, payload?: Record<string, unknown>) => Promise<boolean>;
}) {
  const [color, setColor] = useState<(typeof ORES)[number]>("red");
  const [avoid, setAvoid] = useState(true); // on by default: go around yellow obstacles
  const bench = state?.bench;
  const auto = bench?.auto;
  const token = bench?.arm_token;
  const ready = canStep && !!token && !!state?.vision?.available;
  return (
    <section className="panel autopilot-panel" aria-label="Autopilot">
      <div className="panel-heading">
        <div className="heading-icon"><Navigation size={16} /><h2>Autopilot</h2></div>
        <span className={`micro ${auto?.state === "failed" ? "warn-text" : "muted"}`}>{(auto?.state ?? "idle").toUpperCase()}</span>
      </div>
      <div className="vision-body">
        <p className="vision-help">
          1) Pick the mineral and press <b>Go</b>: the rover drives to it and stops. 2) Pick it up by hand. 3) Press{" "}
          <b>Return to base</b>: it turns 180° and drives to the black flag. If it can't see the target it keeps exploring open floor until it finds it. STOP cancels at any time.
        </p>
        <div className="vision-targets" role="group" aria-label="Mineral">
          {ORES.map((c) => (
            <button key={c} type="button" className={`vision-chip ${color === c ? "selected" : ""}`}
              style={{ "--chip": CLASS_HEX[c] } as React.CSSProperties} aria-pressed={color === c} onClick={() => setColor(c)}>
              {c}
            </button>
          ))}
        </div>
        <Button size="sm" variant={avoid ? "secondary" : "outline"} aria-pressed={avoid} onClick={() => setAvoid(!avoid)}>
          Avoid obstacles: {avoid ? "on" : "off"}
        </Button>
        <div className="vision-actions">
          <Button disabled={!ready} onClick={() => void act("auto_goto", { color, avoid, arm_token: token })}>
            <Navigation size={14} /> Go to {color}
          </Button>
          <Button variant="outline" disabled={!ready} onClick={() => void act("auto_return", { avoid, arm_token: token })}>
            <Flag size={14} /> Return to base
          </Button>
        </div>
        {!canStep && <p className="vision-note">Connect the rover bridge and enable controls first.</p>}
        {auto?.message && <p className={auto.state === "failed" ? "vision-warning" : "vision-message"}>{auto.message}</p>}
      </div>
    </section>
  );
}
