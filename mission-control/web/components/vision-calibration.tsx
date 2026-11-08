import { useState } from "react";
import { Aperture, Crosshair, Footprints, Ruler, ScanEye, Settings2, SquareDashed } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { VisionUi } from "@/hooks/use-vision-ui";
import { CAMERA_TUNING, MASK_TOOL, SAMPLE_CLASSES, TUNING, type VisionSummary } from "@/lib/vision";

type Act = (type: string, payload?: Record<string, unknown>) => Promise<boolean>;
type Tab = "camera" | "mask" | "colors" | "stop" | "fov" | "steps" | "tuning";

// In the order to do them: camera settings change colors, so set them (and the mask) before sampling.
const TABS: { id: Tab; label: string; icon: typeof Crosshair }[] = [
  { id: "mask", label: "Rover mask", icon: SquareDashed },
  { id: "colors", label: "Colors", icon: ScanEye },
  { id: "camera", label: "Camera", icon: Aperture },
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
  const [tab, setTab] = useState<Tab>("colors");
  const [advanced, setAdvanced] = useState(false);
  if (!vision?.available) return null;
  return (
    <section className="panel vision-calibration" aria-label="Vision calibration">
      <div className="panel-heading">
        <div className="heading-icon"><Settings2 size={16} /><h2>Vision calibration</h2></div>
        <span className="micro muted">{canAct ? "CHANGES SAVE AUTOMATICALLY" : "CLAIM PILOT CONTROLS TO EDIT"}</span>
      </div>
      <div className="vision-tabs" role="tablist" aria-label="Calibration steps">
        {TABS.filter((t) => advanced || t.id === "colors" || t.id === "mask").map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "active" : ""}
            onClick={() => {
              setTab(id);
              void ui.setSamplingClass(null);
            }}
          >
            <Icon size={13} /> {label}
          </button>
        ))}
      </div>
      <div className="vision-actions vision-calibration-tools">
        <Button size="sm" variant="outline" aria-expanded={advanced} onClick={() => {
          setAdvanced(!advanced); setTab("colors"); void ui.setSamplingClass(null);
        }}>{advanced ? "Hide advanced settings" : "Advanced settings"}</Button>
        {ui.frozen && <Button size="sm" onClick={ui.resumeLive}>Resume live</Button>}
        <span className="vision-note">{ui.freezing ? "Freezing camera…" : ui.frozen ? "FROZEN · samples use this exact image" : "LIVE · choose a color or trace the rover to freeze"}</span>
      </div>
      {ui.freezeError && <p className="vision-warning" role="alert">{ui.freezeError}</p>}
      <div className="vision-tab-body" role="tabpanel">
        {tab === "camera" && <CameraTab key={JSON.stringify(vision.camera?.controls)} vision={vision} canAct={canAct} act={act} />}
        {tab === "mask" && <MaskTab vision={vision} ui={ui} canAct={canAct} act={act} />}
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
  const count = (vision.rover_mask?.boxes.length ?? 0) + (vision.rover_mask?.polygons?.length ?? 0);
  const drawing = ui.samplingClass === MASK_TOOL;
  return <div className="vision-colors">
    <p className="vision-help">Choose <b>Trace rover outline</b>, then drag around each claw or visible rover part on the frozen image. Release to close and save the outline. Keep the space between the claws clear.</p>
    <div className="vision-actions">
      <Button size="sm" disabled={!canAct || ui.freezing} aria-pressed={drawing}
        onClick={() => void ui.setSamplingClass(drawing ? null : MASK_TOOL)}>{drawing ? "Stop tracing" : "Trace rover outline"}</Button>
      <Button size="sm" variant="outline" disabled={!canAct || !count} onClick={() => void act("vision_mask_undo")}>Undo last mask</Button>
      <Button size="sm" variant="outline" disabled={!canAct || !count} onClick={() => void act("vision_mask_clear")}>Clear mask</Button>
    </div>
    <p className="vision-note">{count ? `${count} masked area(s). Existing mask boxes are preserved.` : "No rover mask yet."} Outlines are shaded on the image and ignored for every color, including black.</p>
  </div>;
}

function ColorsTab({ vision, ui, canAct, act }: { vision: VisionSummary; ui: VisionUi; canAct: boolean; act: Act }) {
  const counts = vision.palette?.counts ?? {};
  const selected = SAMPLE_CLASSES.find((c) => c.id === ui.samplingClass);
  return <div className="vision-colors">
    <p className="vision-help"><b>Pick a color → click inside it.</b> The camera freezes so you can sample precisely. A tight drag-box works too. Calibration applies and saves immediately. Resume live to check it; add a second sample in different lighting if needed.</p>
    {vision.mode !== "palette" && <p className="vision-warning" role="alert">Legacy detection is active. Sample a color or use built-in colors to switch to the simple palette.</p>}
    <div className="vision-classes" role="group" aria-label="Sample class">
      {SAMPLE_CLASSES.map((c) => <button key={c.id} type="button"
        className={`vision-chip ${ui.samplingClass === c.id ? "selected" : ""}`}
        style={{ "--chip": c.hex } as React.CSSProperties} aria-pressed={ui.samplingClass === c.id}
        disabled={!canAct || ui.freezing} onClick={() => void ui.setSamplingClass(ui.samplingClass === c.id ? null : c.id)}>
        {c.label} <small>{counts[c.id] ? `${counts[c.id]} saved` : "built-in"}</small>
      </button>)}
    </div>
    <p className="vision-help"><b>Floor looking green?</b> Choose Floor (white / tinted), then click an empty patch of that floor. Add both white and green-tinted patches; each example saves automatically. Sample the floor itself, away from ores.</p>
    <p className="vision-note">{selected ? `Sampling ${selected.label}: click a solid patch away from edges and glare.` : "Start with built-in colors. Sample only materials that need adjustment."}</p>
    <div className="vision-actions">
      <Button size="sm" variant="outline" disabled={!canAct || !vision.palette?.can_undo} onClick={() => void act("vision_palette_undo")}>Undo calibration</Button>
      <Button size="sm" variant="outline" disabled={!canAct || !selected} onClick={() => void act("vision_palette_reset", { cls: selected?.id })}>Reset {selected?.label ?? "selected color"}</Button>
      <Button size="sm" variant="outline" disabled={!canAct} onClick={() => void act("vision_palette_reset", {})}>Use built-in colors</Button>
    </div>
    <p className="vision-note">Red and green mark samples. Blue marks obstacles; yellow is another material you can calibrate. Brown marks cardboard walls. Black marks the home flag and stays restricted to near-black pixels. Floor samples help reject tinted floor. Check an empty arena and then all objects before relying on the result.</p>
  </div>;
}

function StopLineTab({ vision, canAct, act }: { vision: VisionSummary; canAct: boolean; act: Act }) {
  const [distance, setDistance] = useState(20);
  const ground = vision.ground;
  return (
    <div>
      <p className="vision-help">
        Put a <b>{vision.target}</b> block straight ahead, measure from the gripper tips, and capture it at each distance
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
      {TUNING.filter((t) => vision.mode !== "palette" || (!t.key.startsWith("classifier.") && !t.key.startsWith("floor."))).map((t) => (
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
