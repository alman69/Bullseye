import { Camera, Eye, ScanSearch, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { VisionUi } from "@/hooks/use-vision-ui";
import { CLASS_HEX, SAMPLE_CLASSES, VISION_TARGETS, type VisionSummary } from "@/lib/vision";

/** Live vision status (detection, planner dry run) and overlay toggles. */
export function VisionPanel({
  vision,
  ui,
  canAct,
  act,
  targetControl = false,
}: {
  vision: VisionSummary | null | undefined;
  ui: VisionUi;
  canAct: boolean;
  act: (type: string, payload?: Record<string, unknown>) => Promise<boolean>;
  targetControl?: boolean; // /bench has no mission target, so it picks the vision target directly
}) {
  if (!vision) {
    return (
      <section className="panel vision-panel" aria-label="Vision">
        <div className="panel-heading">
          <div className="heading-icon"><ScanSearch size={16} /><h2>Vision</h2></div>
          <span className="micro muted">OFF</span>
        </div>
        <p className="vision-note">Vision is not running on this server (BULLSEYE_VISION=0).</p>
      </section>
    );
  }
  if (!vision.available) {
    return (
      <section className="panel vision-panel" aria-label="Vision">
        <div className="panel-heading">
          <div className="heading-icon"><ScanSearch size={16} /><h2>Vision</h2></div>
          <span className="micro warn-text">UNAVAILABLE</span>
        </div>
        <p className="vision-note">{vision.error}</p>
      </section>
    );
  }
  const target = vision.target ?? "red";
  const distance = vision.distance_cm_est != null ? `~${Math.round(vision.distance_cm_est)} cm` : null;
  return (
    <section className="panel vision-panel" aria-label="Vision">
      <div className="panel-heading">
        <div className="heading-icon"><ScanSearch size={16} /><h2>Vision</h2></div>
        <span className="micro muted">
          {vision.mode === "palette" ? "ARENA COLORS" : vision.mode === "classifier" ? "CLASSIFIER" : "HSV"} · {vision.fps?.toFixed(1) ?? "0"} FPS
          {vision.processing_ms != null ? ` · ${vision.processing_ms} ms` : ""}
        </span>
      </div>
      <div className="vision-body">
        {targetControl && (
          <div className="vision-targets" role="group" aria-label="Vision target">
            {VISION_TARGETS.map((color) => (
              <button
                key={color}
                type="button"
                className={`vision-chip ${target === color ? "selected" : ""}`}
                style={{ "--chip": CLASS_HEX[color] } as React.CSSProperties}
                aria-pressed={target === color}
                disabled={!canAct}
                onClick={() => void act("vision_target", { color })}
              >
                {color}
              </button>
            ))}
          </div>
        )}
        <div className={`vision-status ${vision.detected ? "found" : "lost"}`} aria-live="polite">
          <span className="vision-swatch" style={{ background: CLASS_HEX[target] }} />
          <strong>{target.toUpperCase()} {vision.detected ? "DETECTED" : "NOT IN VIEW"}</strong>
          {vision.detected && (
            <small>
              {vision.direction}
              {vision.horizontal_error != null ? ` · err ${vision.horizontal_error >= 0 ? "+" : ""}${vision.horizontal_error.toFixed(2)}` : ""}
              {distance ? ` · ${distance}` : ""}
            </small>
          )}
        </div>
        {vision.materials && <div className="vision-materials" aria-label="Visible materials">
          {SAMPLE_CLASSES.filter((c) => c.id !== "floor").map((c) => <span key={c.id}
            className={`vision-chip ${vision.materials?.[c.id]?.detected ? "selected" : ""}`}
            style={{ "--chip": c.hex } as React.CSSProperties}>
            {c.label} <small>{vision.materials?.[c.id]?.detected ? `${vision.materials[c.id].count} visible` : "not in view"}</small>
          </span>)}
        </div>}
        <details className="vision-plan"><summary>Planner preview · no rover movement</summary>
          <span className="micro">PLANNER · DRY RUN (NEVER MOVES THE ROVER)</span>
          <strong>{vision.nav_state ?? "—"} → {vision.nav_label ?? "…"}</strong>
          {vision.nav_reason && <small>{vision.nav_reason}</small>}
        </details>
        <div className="vision-toggles" role="group" aria-label="Overlay options">
          <Button variant={ui.show ? "secondary" : "outline"} size="sm" aria-pressed={ui.show} onClick={() => ui.setShow(!ui.show)}>
            <Eye size={13} /> Overlay
          </Button>
          <Button variant={ui.floor ? "secondary" : "outline"} size="sm" aria-pressed={ui.floor} onClick={() => ui.setFloor(!ui.floor)}>
            Floor strips
          </Button>
          <Button
            variant={ui.mask !== "off" ? "secondary" : "outline"}
            size="sm"
            aria-label="Cycle mask preview"
            onClick={() => ui.setMask(ui.mask === "off" ? "target" : ui.mask === "target" ? "floor" : "off")}
          >
            Mask: {ui.mask}
          </Button>
        </div>
        <div className="vision-actions">
          <Button variant="outline" size="sm" disabled={!canAct} onClick={() => void act("vision_snapshot")}>
            <Camera size={13} /> Snapshot
          </Button>
          <Button variant="outline" size="sm" disabled={!canAct} onClick={() => void act("vision_reset_planner")}>
            Reset planner
          </Button>
          <Button
            size="sm"
            variant={ui.calibrating ? "secondary" : "default"}
            aria-expanded={ui.calibrating}
            onClick={() => ui.setCalibrating(!ui.calibrating)}
          >
            <SlidersHorizontal size={13} /> {ui.calibrating ? "Close calibration" : "Calibrate"}
          </Button>
        </div>
        {!canAct && <p className="vision-note">Claim pilot controls to calibrate or take snapshots.</p>}
        {vision.message && <p className="vision-message">{vision.message}</p>}
      </div>
    </section>
  );
}
