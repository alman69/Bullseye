// Vision types and helpers shared by the overlay, the vision panel and calibration.
// The server runs the detector (software/vision) on the camera relay's frames and
// streams one VisionFrame per processed frame on /ws/vision.

export type VisionFrame = {
  type: "vision";
  seq: number;
  w: number;
  h: number;
  target: string;
  mode: "hsv" | "classifier";
  dry_run: true;
  detection: {
    detected: boolean;
    direction: string;
    horizontal_error: number | null;
    distance_cm_est: number | null;
    area_ratio: number | null;
    candidates: number;
  };
  box: [number, number, number, number] | null; // normalized x, y, w, h
  center: [number, number] | null;
  others: [number, number, number, number][];
  deadband: number;
  mask_boxes?: [number, number, number, number][];
  free: {
    heights: number[];
    blocked: boolean[];
    corridor: number[];
    roi_top: number;
    blocked_height: number;
    floor_visible: boolean;
  } | null;
  nav: { state: string; label: string | null; action: { kind: string; reason: string } | null };
  lines: { stop: number | null; zone: number | null; horizon: number | null };
  wb: number[] | null;
  processing_ms: number;
  fps: number;
};

export type VisionSummary = {
  available: boolean;
  error: string | null;
  dry_run?: boolean;
  target?: string;
  mode?: "hsv" | "classifier";
  classes?: string[];
  model_fitted?: boolean;
  camera?: { controls: Record<string, number>; url_set: boolean; ok: boolean | null; message: string; at: string | null };
  rover_mask?: { boxes: [number, number, number, number][] };
  detected?: boolean;
  direction?: string | null;
  horizontal_error?: number | null;
  distance_cm_est?: number | null;
  nav_state?: string | null;
  nav_label?: string | null;
  nav_reason?: string | null;
  fps?: number;
  processing_ms?: number | null;
  message?: string | null;
  sample_counts?: Record<string, number>;
  confusion?: Record<string, Record<string, number>> | null;
  ground?: {
    calibrated: boolean;
    stop_line_y: number | null;
    horizon_y: number | null;
    captures: { distance_cm: number; bottom_y: number; width: number; error: number }[];
  };
  fov?: { captures: { center?: number; side?: number }; hfov_deg: number | null };
  steps?: { forward: Record<string, number>; turn: Record<string, number> };
  tuning?: Record<string, number>;
  hsv?: Record<string, { h: number[]; s: number[]; v: number[] }>;
};

export type MaskView = "off" | "target" | "floor";

/** What the camera overlay shows, chosen in the vision panel. */
export type VisionOverlayOptions = {
  show: boolean;
  floor: boolean;
  mask: MaskView;
  samplingClass: string | null; // when set, dragging on the video adds samples of this class
  onSampleBox?: (box: [number, number, number, number]) => void;
};

export const VISION_TARGETS = ["red", "green", "blue", "yellow", "black"] as const;
/** What the rover can drive to: the minerals and the black home flag. Yellow is the obstacle color. */
export const NAV_TARGETS = ["red", "green", "blue", "black"] as const;

/** Special "sampling class": dragging adds a rover-mask box instead of color samples. */
export const MASK_TOOL = "__rover_mask__";

export const CAMERA_TUNING: { key: string; label: string; hint: string }[] = [
  { key: "saturation", label: "Color saturation", hint: "Under the floodlight, +2 brings washed-out colors back." },
  { key: "ae_level", label: "Exposure", hint: "Lower (−1 / −2) if bright objects look white." },
  { key: "brightness", label: "Brightness", hint: "Usually 0." },
  { key: "contrast", label: "Contrast", hint: "Usually 0 or +1." },
];

export const SAMPLE_CLASSES: { id: string; label: string; hex: string; key: string }[] = [
  { id: "red", label: "Red", hex: "#f06b7c", key: "1" },
  { id: "green", label: "Green", hex: "#65c79b", key: "2" },
  { id: "blue", label: "Blue", hex: "#79aaf4", key: "3" },
  { id: "yellow", label: "Yellow", hex: "#f2d15c", key: "4" },
  { id: "floor", label: "Floor", hex: "#e9e6de", key: "5" },
  { id: "cardboard", label: "Cardboard", hex: "#c0925e", key: "6" },
  { id: "obstacle", label: "Obstacle", hex: "#ff9a3d", key: "7" },
];
export const FLAG_HEX = "#8b93a1";

export const CLASS_HEX: Record<string, string> = {
  ...Object.fromEntries(SAMPLE_CLASSES.map((c) => [c.id, c.hex])),
  black: FLAG_HEX,
};

export const TUNING: { key: string; label: string; min: number; max: number; step: number; hint: string }[] = [
  { key: "floor.s_max", label: "Floor: max saturation", min: 0, max: 255, step: 1, hint: "Raise if floor shows as blocked; lower if cardboard counts as floor." },
  { key: "floor.v_min", label: "Floor: min brightness", min: 0, max: 200, step: 1, hint: "Lower if floor shadows show as blocked." },
  { key: "detector.min_area_px", label: "Min block size (px)", min: 10, max: 1000, step: 10, hint: "Smaller finds farther blocks; the ground size check rejects specks." },
  { key: "classifier.max_distance", label: "Classifier strictness", min: 1, max: 12, step: 0.5, hint: "Lower = stricter (more unknown pixels)." },
  { key: "navigation.aim_deadband", label: "Centered within", min: 0.02, max: 0.4, step: 0.01, hint: "Fraction of half the image counted as CENTER." },
  { key: "navigation.arrive_distance_cm", label: "Stop distance (cm)", min: 2, max: 60, step: 1, hint: "Moves the stop line once the ground is calibrated." },
  { key: "navigation.approach_zone_y", label: "Approach zone height", min: 0, max: 0.3, step: 0.01, hint: "Band above the stop line with tiny steps." },
  { key: "black.v_max", label: "Black flag: max brightness", min: 10, max: 150, step: 5, hint: "Raise if the flag isn't detected; lower if dark corners count as the flag." },
  { key: "navigation.search_turn_ms", label: "Search turn (ms)", min: 100, max: 1000, step: 100, hint: "Measured: 1000 ms ≈ 49° at PWM 60." },
  { key: "navigation.avoid_turn_ms", label: "Avoid turn (ms)", min: 100, max: 1000, step: 100, hint: "Turn used to go around obstacles." },
];

/** Where an image of size w x h lands inside an element drawn with object-fit: contain. */
export function contentRect(elementWidth: number, elementHeight: number, w: number, h: number) {
  const scale = Math.min(elementWidth / w, elementHeight / h);
  const width = w * scale;
  const height = h * scale;
  return { x: (elementWidth - width) / 2, y: (elementHeight - height) / 2, width, height };
}

/** Pointer position (relative to the element) -> normalized image coordinates, clamped to 0..1. */
export function toImagePoint(
  px: number,
  py: number,
  rect: { x: number; y: number; width: number; height: number },
): [number, number] {
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  return [clamp((px - rect.x) / rect.width), clamp((py - rect.y) / rect.height)];
}

export function normalizedBox(a: [number, number], b: [number, number]): [number, number, number, number] {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}
