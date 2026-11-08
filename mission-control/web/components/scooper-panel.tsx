import { Bluetooth, Grip, Hand, MoveDown, Wifi } from "lucide-react";
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
  if (!scooper) return "Restart Mission Control to load scooper support";
  if (!scooper.available) return "Scooper link unavailable";
  if (scooper.status === "connecting") return scooper.transport === "ble" ? "Connecting display + scooper over BLE…" : "Connecting over Wi-Fi…";
  if (scooper.status !== "ready") return scooper.status === "error" ? "Connection failed" : "Not connected";
  if (scooper.servo_ready === false) return "BLE connected · scooper PWM unavailable";
  if (scooper.state === "sequence") return `${scooper.sequence === "release" ? "Releasing" : "Squeezing"} · ${scooper.step ?? ""}`;
  if (scooper.state === "relaxed") return "Relaxed (servos limp)";
  const left = scooper.angles.left ?? DOWN;
  return left === DOWN ? "Straight down" : left > DOWN ? `Squeezed in ${left - DOWN}°` : `Out ${DOWN - left}°`;
}

export function ScooperPanel({ scooper, canUse, onAction }: Props) {
  const ble = scooper?.transport === "ble" || !scooper?.address.startsWith("http");
  const ready = scooper?.status === "ready";
  const busy = !!scooper?.busy || scooper?.status === "connecting";
  const moving = scooper?.state === "sequence";
  const canMove = canUse && ready && scooper?.servo_ready !== false && !busy && !moving;
  const left = stickEnd(-1, scooper?.angles.left ?? DOWN);
  const right = stickEnd(1, scooper?.angles.right ?? DOWN);
  return <section className="panel bench-scooper" aria-label="Ore scooper">
    <div className="panel-heading"><div className="heading-icon"><Grip size={16} /><h2>Scooper</h2></div><span className="micro">{ble ? "DISPLAY + SCOOPER · BLE" : ready ? `WI-FI · ${scooper?.address.replace(/^https?:\/\//, "")}` : "ESP32-S3 · WI-FI"}</span></div>
    <div className="bench-panel-body">
      <div className="bench-scooper-readout">
        <svg viewBox="-60 -8 120 60" className="bench-scooper-diagram" aria-hidden="true">
          <line x1="-48" y1="0" x2="48" y2="0" className="bench-scooper-chassis" />
          <line x1={-PIVOT_X} y1="0" x2={left.x} y2={left.y} className="bench-scooper-stick" />
          <line x1={PIVOT_X} y1="0" x2={right.x} y2={right.y} className="bench-scooper-stick" />
          <circle cx={-PIVOT_X} r="3.5" className="bench-scooper-hub" />
          <circle cx={PIVOT_X} r="3.5" className="bench-scooper-hub" />
        </svg>
        <div><strong aria-live="polite">{describe(scooper)}</strong><small>{ready && scooper?.angles.left != null ? `Commanded left ${scooper.angles.left}° · right ${scooper.angles.right}° (90° = down)` : "Sticks start straight down at power-up"}</small></div>
      </div>
      <Button className="primary-button bench-scooper-squeeze" disabled={!canMove} onClick={() => onAction("scooper_squeeze")}><Hand size={16} />Squeeze</Button>
      <div className="bench-scooper-row">
        <Button disabled={!canMove} onClick={() => onAction("scooper_release")}>Release</Button>
        <Button disabled={!canMove} onClick={() => onAction("scooper_home")}><MoveDown size={15} />Straight down</Button>
        <Button variant="outline" disabled={!canUse || busy || !scooper?.available} onClick={() => onAction("scooper_connect")}>{ble ? <Bluetooth size={15} /> : <Wifi size={15} />}{ready ? "Reconnect" : "Connect"}</Button>
      </div>
      {scooper?.error && <p className="bench-inline-error" role="status">{scooper.error}</p>}
      {!canUse && <p className="bench-help">Claim controls in the Pilot seat panel to connect. Rover driving can stay disabled.</p>}
      {ble && <p className="bench-help">One BLE connection powers the display updates and scooper controls. Stop the separate send-display.sh sender before connecting here. {ready ? `BLE connected. ${scooper?.display_updates ?? 0} display updates acknowledged.` : "Claim controls, then Connect."}</p>}
      {ble && scooper?.heartbeats_sent != null && <p className="bench-help">Control heartbeats: {scooper.heartbeats_acknowledged ?? 0} applied / {scooper.heartbeats_sent} sent · last send {scooper.heartbeat_age_ms == null ? "pending" : `${scooper.heartbeat_age_ms} ms ago`} · largest gap {scooper.max_heartbeat_gap_ms ?? 0} ms.</p>}
      {ble && scooper?.device_boot_id != null && <p className="bench-help">S3 boot changes observed: {scooper.device_boot_changes ?? 0} (includes manual resets). Reconnect without resetting to distinguish a BLE drop from a board restart.</p>}
      <p className="bench-help">Squeeze: out 30°, then in 80° past straight down to lift the block. STOP holds the last commanded angles. {ble ? "Rover Wi-Fi is unchanged." : "Legacy standalone scooper uses the rover Wi-Fi."}</p>
      <p className="bench-help">The diagram shows firmware-commanded angles. These servos provide no measured position feedback; observe the mechanism to verify movement.</p>
    </div>
  </section>;
}
