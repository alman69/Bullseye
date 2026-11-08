import { useState } from "react";
import { Aperture, Crosshair, Footprints, Ruler, ScanEye, Settings2, SquareDashed } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { VisionUi } from "@/hooks/use-vision-ui";
import { CAMERA_TUNING, CLASS_HEX, MASK_TOOL, SAMPLE_CLASSES, TUNING, VISION_TARGETS, type VisionSummary } from "@/lib/vision";

type Act = (type: string, payload?: Record<string, unknown>) => Promise<boolean>;
type Tab = "camera" | "mask" | "hsv" | "colors" | "stop" | "fov" | "steps" | "tuning";

// In the order to do them: camera settings change colors, so set them (and the mask) before sampling.
const TABS: { id: Tab; label: string; icon: typeof Crosshair }[] = [
  { id: "camera", label: "1 Camera", icon: Aperture },
  { id: "mask", label: "2 Rover mask", icon: SquareDashed },
  { id: "hsv", label: "3 HSV colors", icon: ScanEye },
  { id: "colors", label: "Classifier", icon: ScanEye },
  { id: "stop", label: "Stop line", icon: Ruler },
  { id: "fov", label: "Field of view", icon: Crosshair },
  { id: "steps", label: "Steps", icon: Footprints },
  { id: "tuning", label: "Tuning", icon: Settings2 },
];

/** Browser calibration for the vision service. Every change is saved to software/vision on the server. */
export function VisionCalibration({
  vision,
  ui,
  canAct,
  act,
}: {
  vision: VisionSummary | null | undefined;
  ui: VisionUi;
  canAct: boolean;
  act: Act;
}) {
  const [tab, setTab] = useState<Tab>("camera");
  if (!vision?.available) return null;
  return (
    <section className="panel vision-calibration" aria-label="Vision calibration">
      <div className="panel-heading">
        <div className="heading-icon"><Settings2 size={16} /><h2>Vision calibration</h2></div>
        <span className="micro muted">{canAct ? "SAVED TO software/vision" : "CLAIM PILOT CONTROLS TO EDIT"}</span>
      </div>
      <div className="vision-tabs" role="tablist" aria-label="Calibration steps">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "active" : ""}
            onClick={() => {
              setTab(id);
              if (id !== "colors" && id !== "mask") ui.setSamplingClass(null);
            }}
          >
            <Icon size={13} /> {label}
          </button>
        ))}
      </div>
      <div className="vision-tab-body" role="tabpanel">
        {tab === "camera" && <CameraTab key={JSON.stringify(vision.camera?.controls)} vision={vision} canAct={canAct} act={act} />}
        {tab === "mask" && <MaskTab vision={vision} ui={ui} canAct={canAct} act={act} />}
        {tab === "hsv" && <HsvTab vision={vision} ui={ui} canAct={canAct} act={act} />}
        {tab === "colors" && <ColorsTab vision={vision} ui={ui} canAct={canAct} act={act} />}
        {tab === "stop" && <StopLineTab vision={vision} canAct={canAct} act={act} />}
        {tab === "fov" && <FovTab vision={vision} canAct={canAct} act={act} />}
        {tab === "steps" && <StepsTab key={JSON.stringify(vision.steps)} vision={vision} canAct={canAct} act={act} />}
        {tab === "tuning" && <TuningTab key={JSON.stringify(vision.tuning)} vision={vision} canAct={canAct} act={act} />}
      </div>
      {vision.message && <p className="vision-message">{vision.message}</p>}
    </section>
  );
}

function CameraTab({ vision, canAct, act }: { vision: VisionSummary; canAct: boolean; act: Act }) {
  const controls = vision.camera?.controls ?? {};
  const [draft, setDraft] = useState<Record<string, number>>(controls);
  return (
    <div className="vision-tuning">
      <p className="vision-help">
        Set these first: they change the colors the camera sends. Under the floodlight start with <b>saturation +2</b> and{" "}
        <b>exposure −1</b>, then check the colors look strong in the video. They live in the camera’s memory only and are
        re-sent automatically when the camera restarts.
      </p>
      {CAMERA_TUNING.map((t) => (
        <label key={t.key} className="vision-slider">
          <span>{t.label} <b>{draft[t.key] ?? 0}</b></span>
          <input
            type="range"
            min={-2}
            max={2}
            step={1}
            value={draft[t.key] ?? 0}
            disabled={!canAct}
            onChange={(e) => setDraft({ ...draft, [t.key]: Number(e.target.value) })}
            onPointerUp={() => draft[t.key] !== controls[t.key] && void act("vision_camera", { key: t.key, value: draft[t.key] ?? 0 })}
            onKeyUp={() => draft[t.key] !== controls[t.key] && void act("vision_camera", { key: t.key, value: draft[t.key] ?? 0 })}
          />
          <small>{t.hint}</small>
        </label>
      ))}
      <p className={`vision-note ${vision.camera?.ok === false ? "warn-text" : ""}`}>
        {vision.camera?.message}{vision.camera?.at ? ` (${vision.camera.at})` : ""}
      </p>
    </div>
  );
}

function MaskTab({ vision, ui, canAct, act }: { vision: VisionSummary; ui: VisionUi; canAct: boolean; act: Act }) {
  const boxes = vision.rover_mask?.boxes ?? [];
  const drawing = ui.samplingClass === MASK_TOOL;
  return (
    <div>
      <p className="vision-help">
        Draw boxes over <b>parts of the rover that are always in view</b> (the popsicle-stick gripper and its tape). Vision
        ignores them for colors, and the floor check treats them as floor, so the claws are never “obstacles” or
        “blue”. Leave the gap between the claws unmasked: that’s where the mineral ends up.
      </p>
      <div className="vision-actions">
        <Button size="sm" variant={drawing ? "secondary" : "default"} disabled={!canAct} aria-pressed={drawing}
          onClick={() => ui.setSamplingClass(drawing ? null : MASK_TOOL)}>
          {drawing ? "Stop drawing" : "Draw mask boxes"}
        </Button>
        <Button size="sm" variant="outline" disabled={!canAct || !boxes.length} onClick={() => void act("vision_mask_undo")}>Undo last box</Button>
        <Button size="sm" variant="outline" disabled={!canAct || !boxes.length} onClick={() => void act("vision_mask_clear")}>Clear mask</Button>
      </div>
      <p className="vision-note">
        {drawing ? "Drag on the video over each claw. " : ""}
        {boxes.length ? `${boxes.length} box(es) masked (shown shaded on the video).` : "No mask yet."}
      </p>
    </div>
  );
}

const HSV_SLIDERS: { key: "h" | "s" | "v"; label: string; max: number }[] = [
  { key: "h", label: "Hue", max: 179 },
  { key: "s", label: "Saturation", max: 255 },
  { key: "v", label: "Brightness", max: 255 },
];

function HsvTab({ vision, ui, canAct, act }: { vision: VisionSummary; ui: VisionUi; canAct: boolean; act: Act }) {
  const [color, setColor] = useState<string>(vision.target && vision.hsv?.[vision.target] ? vision.target : "red");
  const saved = vision.hsv?.[color] ?? { h: [0, 179], s: [0, 255], v: [0, 255] };
  const [draft, setDraft] = useState<{ h: number[]; s: number[]; v: number[] } | null>(null);
  const range = draft ?? saved;
  const commit = (next: { h: number[]; s: number[]; v: number[] }) => {
    setDraft(null);
    void act("vision_hsv", { color, h: next.h, s: next.s, v: next.v });
  };
  return (
    <div className="vision-tuning">
      <p className="vision-help">
        The original fixed-range detector. Pick a color, select the same <b>target chip</b> in the Vision panel, turn on{" "}
        <b>Mask: target</b>, and move the sliders until only that object lights up. Hue low above hue high wraps around
        (red). Changes save when you release a slider.
      </p>
      {vision.mode !== "hsv" && (
        <p className="vision-warning" role="alert">
          The detector is in classifier mode, so these ranges aren’t used yet.{" "}
          <Button size="sm" disabled={!canAct} onClick={() => void act("vision_mode", { mode: "hsv" })}>Use HSV detector</Button>
        </p>
      )}
      <div className="vision-targets" role="group" aria-label="HSV color">
        {VISION_TARGETS.map((c) => (
          <button key={c} type="button" className={`vision-chip ${color === c ? "selected" : ""}`}
            style={{ "--chip": CLASS_HEX[c] } as React.CSSProperties} aria-pressed={color === c}
            onClick={() => { setColor(c); setDraft(null); if (canAct) void act("vision_target", { color: c }); }}>
            {c}
          </button>
        ))}
      </div>
      {HSV_SLIDERS.map(({ key, label, max }) =>
        [0, 1].map((i) => (
          <label key={key + i} className="vision-slider">
            <span>{label} {i === 0 ? "low" : "high"} <b>{range[key][i]}</b></span>
            <input
              type="range" min={0} max={max} step={1} value={range[key][i]} disabled={!canAct}
              aria-label={`${label} ${i === 0 ? "low" : "high"}`}
              onChange={(e) => {
                const next = { h: [...range.h], s: [...range.s], v: [...range.v] };
                next[key][i] = Number(e.target.value);
                setDraft(next);
              }}
              onPointerUp={() => draft && commit(draft)}
              onKeyUp={() => draft && commit(draft)}
            />
          </label>
        )),
      )}
      {ui.mask === "off" && <p className="vision-note">Tip: set the Vision panel’s mask to “target” to see exactly what matches.</p>}
    </div>
  );
}

function ColorsTab({ vision, ui, canAct, act }: { vision: VisionSummary; ui: VisionUi; canAct: boolean; act: Act }) {
  const counts = vision.sample_counts ?? {};
  const totalSamples = Object.values(counts).reduce((a, b) => a + b, 0);
  const confusion = vision.confusion;
  const columns = confusion ? Object.keys(Object.values(confusion)[0] ?? {}) : [];
  return (
    <div className="vision-colors">
      <p className="vision-help">
        Pick a class, then <b>drag a tight box</b> on the video inside that object. Do 3–5 boxes per class: near and far,
        lit and shadowed. For floor include shadows and the darker bottom corners. Then fit.
      </p>
      {totalSamples > 0 && !vision.model_fitted && (
        <p className="vision-warning" role="alert">
          Samples are collected but <b>not fitted yet</b>: the detector is still using the default HSV colors. Click{" "}
          <b>Fit color model</b>.
        </p>
      )}
      <div className="vision-classes" role="group" aria-label="Sample class">
        {SAMPLE_CLASSES.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`vision-chip ${ui.samplingClass === c.id ? "selected" : ""}`}
            style={{ "--chip": c.hex } as React.CSSProperties}
            aria-pressed={ui.samplingClass === c.id}
            disabled={!canAct}
            onClick={() => ui.setSamplingClass(ui.samplingClass === c.id ? null : c.id)}
          >
            {c.label} <small>{counts[c.id] ?? 0}</small>
          </button>
        ))}
      </div>
      <p className="vision-note">
        {ui.samplingClass
          ? `Sampling ${ui.samplingClass}: drag on the video. Click the chip again to stop.`
          : "Not sampling. Choose a class to start."}
      </p>
      <div className="vision-actions">
        <Button size="sm" disabled={!canAct} onClick={() => void act("vision_fit")}>Fit color model</Button>
        <Button size="sm" variant="outline" disabled={!canAct} onClick={() => void act("vision_undo")}>Undo last box</Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!canAct || !ui.samplingClass}
          onClick={() => void act("vision_clear_samples", { cls: ui.samplingClass })}
        >
          Clear {ui.samplingClass ?? "class"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!canAct || totalSamples === 0}
          onClick={() => {
            if (window.confirm("Delete ALL color samples? Do this after changing camera settings, then sample every class again.")) {
              void act("vision_clear_samples", {});
            }
          }}
        >
          Clear all samples
        </Button>
        <label className="vision-select">
          Detector
          <select
            value={vision.mode === "classifier" ? "classifier" : "hsv"}
            disabled={!canAct}
            onChange={(e) => void act("vision_mode", { mode: e.target.value })}
          >
            <option value="classifier">Classifier</option>
            <option value="hsv">HSV (fallback)</option>
          </select>
        </label>
      </div>
      {confusion && (
        <div className="vision-confusion" role="table" aria-label="Confusion table">
          <p className="vision-help">
            Each row: how that class’s samples were classified. Aim for ≥ 95% on the diagonal and &lt; 2% between a
            mineral and floor/cardboard/obstacle.
          </p>
          <div className="confusion-grid" style={{ gridTemplateColumns: `90px repeat(${columns.length}, 1fr)` }}>
            <span />
            {columns.map((c) => <span key={c} className="confusion-head">{c.slice(0, 6)}</span>)}
            {Object.entries(confusion).map(([row, values]) => (
              <Row key={row} row={row} values={values} columns={columns} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ row, values, columns }: { row: string; values: Record<string, number>; columns: string[] }) {
  return (
    <>
      <span className="confusion-row" style={{ color: CLASS_HEX[row] }}>{row}</span>
      {columns.map((c) => {
        const v = values[c] ?? 0;
        const diagonal = c === row;
        const bad = diagonal ? v < 0.95 : v >= 0.02;
        return (
          <span key={c} className={`confusion-cell ${diagonal ? "diag" : ""} ${bad ? "bad" : ""}`} style={{ opacity: 0.35 + 0.65 * Math.min(1, v * (diagonal ? 1 : 10)) }}>
            {(v * 100).toFixed(v >= 0.995 || v === 0 ? 0 : 1)}%
          </span>
        );
      })}
    </>
  );
}

function StopLineTab({ vision, canAct, act }: { vision: VisionSummary; canAct: boolean; act: Act }) {
  const [distance, setDistance] = useState(20);
  const ground = vision.ground;
  return (
    <div>
      <p className="vision-help">
        Put a <b>{vision.target}</b> block straight ahead, measure from the front bumper, and capture it at each distance
        (10, 15, 20, 30 cm works well). Then fit: the magenta STOP LINE appears on the video.
      </p>
      <div className="vision-actions">
        {[10, 15, 20, 30].map((d) => (
          <Button key={d} size="sm" variant={distance === d ? "secondary" : "outline"} onClick={() => setDistance(d)}>{d} cm</Button>
        ))}
        <label className="vision-select">
          Other
          <input type="number" min={3} max={300} value={distance} onChange={(e) => setDistance(Number(e.target.value))} />
        </label>
        <Button size="sm" disabled={!canAct || !vision.detected} onClick={() => void act("vision_ground_capture", { distance_cm: distance })}>
          Capture at {distance} cm
        </Button>
      </div>
      {!vision.detected && <p className="vision-note">The {vision.target} block must be detected to capture.</p>}
      <ul className="vision-captures">
        {(ground?.captures ?? []).map((c) => (
          <li key={c.distance_cm}>{c.distance_cm} cm → bottom edge at {(c.bottom_y * 100).toFixed(1)}% · width {(c.width * 100).toFixed(1)}%</li>
        ))}
      </ul>
      <div className="vision-actions">
        <Button size="sm" disabled={!canAct || (ground?.captures.length ?? 0) < 2} onClick={() => void act("vision_ground_fit")}>Fit stop line</Button>
        <Button size="sm" variant="outline" disabled={!canAct} onClick={() => void act("vision_ground_clear")}>Clear captures</Button>
        <Button size="sm" variant="outline" disabled={!canAct || !ground?.calibrated} onClick={() => void act("vision_ground_reset")}>Remove calibration</Button>
      </div>
      <p className="vision-note">
        {ground?.calibrated
          ? `Calibrated: stop line at ${((ground.stop_line_y ?? 0) * 100).toFixed(1)}% of the image height. Consider lowering “Min block size” in Tuning to about 80.`
          : "Not calibrated yet: arrival falls back to the block’s size in the image."}
      </p>
    </div>
  );
}

function FovTab({ vision, canAct, act }: { vision: VisionSummary; canAct: boolean; act: Act }) {
  const [distance, setDistance] = useState(30);
  const [offset, setOffset] = useState(10);
  const captures = vision.fov?.captures ?? {};
  return (
    <div>
      <p className="vision-help">
        Block at {distance} cm: capture it centered, then move it {offset} cm to the side (same distance) and capture again.
      </p>
      <div className="vision-actions">
        <Button size="sm" disabled={!canAct || !vision.detected} onClick={() => void act("vision_fov_capture", { role: "center" })}>
          Capture centered {captures.center != null ? "✓" : ""}
        </Button>
        <Button size="sm" disabled={!canAct || !vision.detected} onClick={() => void act("vision_fov_capture", { role: "side" })}>
          Capture side {captures.side != null ? "✓" : ""}
        </Button>
        <label className="vision-select">Distance cm<input type="number" min={5} value={distance} onChange={(e) => setDistance(Number(e.target.value))} /></label>
        <label className="vision-select">Offset cm<input type="number" min={1} value={offset} onChange={(e) => setOffset(Number(e.target.value))} /></label>
        <Button size="sm" disabled={!canAct} onClick={() => void act("vision_fov_fit", { distance_cm: distance, offset_cm: offset })}>Compute</Button>
      </div>
      <p className="vision-note">
        {vision.fov?.hfov_deg ? `Camera field of view: ${vision.fov.hfov_deg}°` : "Not measured yet: turns use fixed durations."}
      </p>
    </div>
  );
}

const DURATIONS = ["200", "500", "1000"];

function StepsTab({ vision, canAct, act }: { vision: VisionSummary; canAct: boolean; act: Act }) {
  const toText = (t: Record<string, number> = {}) => Object.fromEntries(Object.entries(t).map(([k, v]) => [k, String(v)]));
  const [forward, setForward] = useState<Record<string, string>>(() => toText(vision.steps?.forward));
  const [turn, setTurn] = useState<Record<string, string>>(() => toText(vision.steps?.turn));
  const parse = (t: Record<string, string>) =>
    Object.fromEntries(DURATIONS.filter((d) => t[d]?.trim()).map((d) => [d, Number(t[d])]));
  return (
    <div>
      <p className="vision-help">
        On the <b>/bench</b> page at PWM 60, run each timed button 5 times and enter the average: forward in cm, turns in degrees.
        The planner then sizes each turn and forward step from these.
      </p>
      <table className="vision-steps">
        <thead><tr><th /> {DURATIONS.map((d) => <th key={d}>{Number(d) / 1000} s</th>)}</tr></thead>
        <tbody>
          <tr>
            <th>Forward (cm)</th>
            {DURATIONS.map((d) => (
              <td key={d}><input aria-label={`forward ${d} ms cm`} inputMode="decimal" value={forward[d] ?? ""} onChange={(e) => setForward({ ...forward, [d]: e.target.value })} /></td>
            ))}
          </tr>
          <tr>
            <th>Turn (°)</th>
            {DURATIONS.map((d) => (
              <td key={d}><input aria-label={`turn ${d} ms degrees`} inputMode="decimal" value={turn[d] ?? ""} onChange={(e) => setTurn({ ...turn, [d]: e.target.value })} /></td>
            ))}
          </tr>
        </tbody>
      </table>
      <div className="vision-actions">
        <Button size="sm" disabled={!canAct} onClick={() => void act("vision_steps", { forward: parse(forward), turn: parse(turn) })}>Save step table</Button>
      </div>
    </div>
  );
}

function TuningTab({ vision, canAct, act }: { vision: VisionSummary; canAct: boolean; act: Act }) {
  const values = vision.tuning ?? {};
  const [draft, setDraft] = useState<Record<string, number>>(values);
  return (
    <div className="vision-tuning">
      {TUNING.map((t) => (
        <label key={t.key} className="vision-slider">
          <span>{t.label} <b>{draft[t.key] ?? "—"}</b></span>
          <input
            type="range"
            min={t.min}
            max={t.max}
            step={t.step}
            value={draft[t.key] ?? t.min}
            disabled={!canAct}
            onChange={(e) => setDraft({ ...draft, [t.key]: Number(e.target.value) })}
            onPointerUp={() => draft[t.key] !== values[t.key] && void act("vision_tune", { key: t.key, value: draft[t.key] })}
            onKeyUp={() => draft[t.key] !== values[t.key] && void act("vision_tune", { key: t.key, value: draft[t.key] })}
          />
          <small>{t.hint}</small>
        </label>
      ))}
    </div>
  );
}
