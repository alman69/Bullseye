import { useEffect, useRef, useState } from "react";
import { Flag, Hand, Navigation } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { MissionState } from "@/lib/protocol";
import { CLASS_HEX } from "@/lib/vision";
import { playVictory, unlockAudio } from "@/lib/victory-sound";

const ORES = ["red", "green"] as const; // blue is the obstacle color, never a target

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
  // Space victory fanfare once per "Sample deposited".
  const lastState = useRef(auto?.state);
  useEffect(() => {
    if (auto?.state === "deposited" && lastState.current !== "deposited") playVictory();
    lastState.current = auto?.state;
  }, [auto?.state]);
  const send = (type: string, payload: Record<string, unknown>) => {
    unlockAudio(); // a click: lets the browser play the victory sound later
    void act(type, payload);
  };
  return (
    <section className="panel autopilot-panel" aria-label="Autopilot">
      <div className="panel-heading">
        <div className="heading-icon"><Navigation size={16} /><h2>Autopilot</h2></div>
        <span className={`micro ${auto?.state === "failed" ? "warn-text" : "muted"}`}>{(auto?.state ?? "idle").toUpperCase()}</span>
      </div>
      <div className="vision-body">
        <p className="vision-help">
          1) Pick the mineral and press <b>Go to</b>: the rover searches (exploring open floor if needed), drives to it and
          stops about 10 cm away. 2) <b>Take manual control</b>: drive up and grab it, then press <b>Sample collected</b>
          (LEDs green). 3) Drive it back to base and press <b>Sample deposited</b> (LEDs red). Blue = obstacles. STOP cancels
          at any time.
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
          <Button disabled={!ready} onClick={() => send("auto_goto", { color, avoid, arm_token: token })}>
            <Navigation size={14} /> Go to {color}
          </Button>
          <Button variant="outline" disabled={!canStep || !token} onClick={() => send("sample_collected", { arm_token: token })}>
            <Hand size={14} /> Sample collected
          </Button>
          <Button variant="outline" disabled={!canStep || !token} onClick={() => send("sample_deposited", { arm_token: token })}>
            <Flag size={14} /> Sample deposited
          </Button>
        </div>
        {!canStep && <p className="vision-note">Connect the rover bridge and enable controls first.</p>}
        {auto?.state === "collected" && <p className="mission-banner collected" role="status">🧪 Sample collected</p>}
        {auto?.state === "deposited" && <p className="mission-banner deposited" role="status">🚀 Sample deposited</p>}
        {auto?.message && <p className={auto.state === "failed" ? "vision-warning" : "vision-message"}>{auto.message}</p>}
      </div>
    </section>
  );
}
