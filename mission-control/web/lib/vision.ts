// Vision types and helpers shared by the overlay, the vision panel and calibration.
// The server runs the detector (software/vision) on the camera relay's frames and
// streams one VisionFrame per processed frame on /ws/vision.

export type VisionFrame = {
  type: "vision";
  seq: number;
  w: number;
  h: number;
  target: string;
  mode: "hsv" | "classifier" | "palette";
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
  materials?: Record<string, MaterialDetection>;
  mask_polygons?: [number, number][][];
  mask_boxes?: [number, number, number, number][];
  free: {
    heights: number[];
    blocked: boolean[];
    corridor: number[];
    roi_top: number;
    blocked_height: number;
    floor_visible: boolean;
  } | null;
  lines: { stop: number | null; zone: number | null; horizon: number | null };
  wb: number[] | null;
  processing_ms: number;
  fps: number;
};

export type VisionSummary = {
  available: boolean;
  error: string | null;
  target?: string;
  mode?: "hsv" | "classifier" | "palette";
  classes?: string[];
  model_fitted?: boolean;
  camera?: { controls: Record<string, number>; url_set: boolean; ok: boolean | null; message: string; at: string | null };
  rover_mask?: { boxes: [number, number, number, number][]; polygons?: [number, number][][] };
  palette?: { counts: Record<string, number>; can_undo: boolean };
  materials?: Record<string, MaterialDetection>;
  detected?: boolean;
  direction?: string | null;
  horizontal_error?: number | null;
  distance_cm_est?: number | null;
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
};

export type MaterialDetection = { detected: boolean; count: number; boxes: [number, number, number, number][] };
export type FrozenVision = { id: string; image: string; frame: VisionFrame };

export type MaskView = "off" | "target" | "floor";

/** What the camera overlay shows, chosen in the vision panel. */
export type VisionOverlayOptions = {
  show: boolean;
  floor: boolean;
  mask: MaskView;
  samplingClass: string | null; // when set, dragging on the video adds samples of this class
  frozen?: FrozenVision | null;
  onResumeLive?: () => void;
  onMaskPolygon?: (points: [number, number][]) => void;
  onSampleBox?: (box: [number, number, number, number]) => void;
};

export const VISION_TARGETS = ["red", "green", "home"] as const;

/** What the operator sees for each internal color id (the server and config keep red/green/blue). */
export const DISPLAY_NAMES: Record<string, string> = { red: "ruby", green: "emerald", blue: "obstacle", cardboard: "wall", home: "home" };
export const displayName = (id: string) => DISPLAY_NAMES[id] ?? id;

/** Special "sampling class": dragging adds a rover-mask box instead of color samples. */
export const MASK_TOOL = "__rover_mask__";

export const CAMERA_TUNING: { key: string; label: string; hint: string }[] = [
  { key: "saturation", label: "Color saturation", hint: "Under the floodlight, +2 brings washed-out colors back." },
  { key: "ae_level", label: "Exposure", hint: "Lower (−1 / −2) if bright objects look white." },
  { key: "brightness", label: "Brightness", hint: "Usually 0." },
  { key: "contrast", label: "Contrast", hint: "Usually 0 or +1." },
];

export const SAMPLE_CLASSES: { id: string; label: string; hex: string; key: string }[] = [
  { id: "red", label: "Ruby (red)", hex: "#f06b7c", key: "1" },
  { id: "green", label: "Emerald (green)", hex: "#65c79b", key: "2" },
  { id: "blue", label: "Obstacle (blue)", hex: "#79aaf4", key: "3" },
  { id: "yellow", label: "Yellow material", hex: "#f2d15c", key: "4" },
  { id: "floor", label: "Floor (white / tinted)", hex: "#e9e6de", key: "5" },
  { id: "cardboard", label: "Brown wall", hex: "#c0925e", key: "6" },
  { id: "home", label: "Black home base", hex: "#88909d", key: "7" },
];

export const CLASS_HEX: Record<string, string> = Object.fromEntries(
  SAMPLE_CLASSES.map((c) => [c.id, c.hex]),
);

export const TUNING: { key: string; label: string; min: number; max: number; step: number; hint: string }[] = [
  { key: "floor.s_max", label: "Floor: max saturation", min: 0, max: 120, step: 1, hint: "Raise if floor shows as blocked; lower if cardboard counts as floor." },
  { key: "floor.v_min", label: "Floor: min brightness", min: 0, max: 200, step: 1, hint: "Lower if floor shadows show as blocked." },
  { key: "detector.min_area_px", label: "Min block size (px)", min: 10, max: 1000, step: 10, hint: "Smaller finds farther blocks; the ground size check rejects specks." },
  { key: "classifier.max_distance", label: "Classifier strictness", min: 1, max: 12, step: 0.5, hint: "Lower = stricter (more unknown pixels)." },
  { key: "navigation.aim_deadband", label: "Centered within", min: 0.02, max: 0.4, step: 0.01, hint: "Fraction of half the image counted as CENTER." },
  { key: "navigation.arrive_distance_cm", label: "Stop distance (cm)", min: 2, max: 60, step: 1, hint: "Moves the stop line once the ground is calibrated." },
  { key: "navigation.approach_zone_y", label: "Approach zone height", min: 0, max: 0.3, step: 0.01, hint: "Band above the stop line with tiny steps." },
  { key: "navigation.arrive_bottom_y", label: "Arrive at image bottom", min: 0.5, max: 1, step: 0.01, hint: "Sample's bottom edge this low in the image counts as 15 cm away (paper below the sonar beam)." },
  { key: "navigation.stop_buffer_cm", label: "Stop early by (cm)", min: 0, max: 10, step: 0.5, hint: "Extra leeway on top of the 15 cm stop. 0 = stop about 15 cm away; 3 = about 18 cm." },
  { key: "navigation.wall_distance_cm", label: "Wall margin (cm)", min: 10, max: 60, step: 1, hint: "Driving toward open space stops this far from a wall." },
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
