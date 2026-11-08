import { Grip, Hand, MoveDown, Wifi } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ScooperState } from "@/lib/protocol";

// Logical stick angles from the firmware: 90 = straight down, lower = out, higher = in.
const DOWN = 90;
const PIVOT_X = 22;
const STICK = 42;

type Props = {
  scooper?: ScooperState;
  canUse: boolean; // Connected to this server and holding the pilot seat.
  onAction: (kind: "scooper_connect" | "scooper_squeeze" | "scooper_release" | "scooper_home") => void;
};

function stickEnd(side: -1 | 1, angle: number) {
  const inward = ((angle - DOWN) * Math.PI) / 180;
  return { x: side * (PIVOT_X - Math.sin(inward) * STICK), y: Math.cos(inward) * STICK };
}

function describe(scooper?: ScooperState) {
  if (!scooper) return "Waiting for the bench server";
  if (!scooper.available) return "Scooper link unavailable";
  if (scooper.status === "connecting") return "Connecting over Wi-Fi…";
  if (scooper.status !== "ready") return scooper.status === "error" ? "Connection failed" : "Not connected";
  if (scooper.state === "sequence") return `${scooper.sequence === "release" ? "Releasing" : "Squeezing"} · ${scooper.step ?? ""}`;
  if (scooper.state === "relaxed") return "Relaxed (servos limp)";
  const left = scooper.angles.left ?? DOWN;
  return left === DOWN ? "Straight down" : left > DOWN ? `Squeezed in ${left - DOWN}°` : `Out ${DOWN - left}°`;
}

export function ScooperPanel({ scooper, canUse, onAction }: Props) {
  const ready = scooper?.status === "ready";
  const busy = !!scooper?.busy || scooper?.status === "connecting";
  const moving = scooper?.state === "sequence";
  const canMove = canUse && ready && !busy && !moving;
  const left = stickEnd(-1, scooper?.angles.left ?? DOWN);
  const right = stickEnd(1, scooper?.angles.right ?? DOWN);
  return <section className="panel bench-scooper" aria-label="Ore scooper">
    <div className="panel-heading"><div className="heading-icon"><Grip size={16} /><h2>Scooper</h2></div><span className="micro">{ready ? `WI-FI · ${scooper?.address.replace(/^https?:\/\//, "")}` : "ESP32-S3 · WI-FI"}</span></div>
    <div className="bench-panel-body">
      <div className="bench-scooper-readout">
        <svg viewBox="-60 -8 120 60" className="bench-scooper-diagram" aria-hidden="true">
          <line x1="-48" y1="0" x2="48" y2="0" className="bench-scooper-chassis" />
          <line x1={-PIVOT_X} y1="0" x2={left.x} y2={left.y} className="bench-scooper-stick" />
          <line x1={PIVOT_X} y1="0" x2={right.x} y2={right.y} className="bench-scooper-stick" />
          <circle cx={-PIVOT_X} r="3.5" className="bench-scooper-hub" />
          <circle cx={PIVOT_X} r="3.5" className="bench-scooper-hub" />
        </svg>
        <div><strong aria-live="polite">{describe(scooper)}</strong><small>{ready && scooper?.angles.left != null ? `Left ${scooper.angles.left}° · Right ${scooper.angles.right}° (90° = down)` : "Sticks start straight down at power-up"}</small></div>
      </div>
      <Button className="primary-button bench-scooper-squeeze" disabled={!canMove} onClick={() => onAction("scooper_squeeze")}><Hand size={16} />Squeeze</Button>
      <div className="bench-scooper-row">
        <Button disabled={!canMove} onClick={() => onAction("scooper_release")}>Release</Button>
        <Button disabled={!canMove} onClick={() => onAction("scooper_home")}><MoveDown size={15} />Straight down</Button>
        <Button variant="outline" disabled={!canUse || busy || scooper?.available === false} onClick={() => onAction("scooper_connect")}><Wifi size={15} />{ready ? "Reconnect" : "Connect"}</Button>
      </div>
      {scooper?.error && <p className="bench-inline-error" role="status">{scooper.error}</p>}
      <p className="bench-help">Squeeze: out 30°, then in 80° past straight down to lift the block. STOP freezes the sticks. The scooper joins the rover's ELEGOO Wi-Fi on its own (separate from the rover controls) and needs only the pilot seat.</p>
    </div>
  </section>;
}
