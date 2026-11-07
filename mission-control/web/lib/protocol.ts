export type SampleColor = "red" | "green" | "blue";
export type Phase =
  | "idle"
  | "searching"
  | "approaching"
  | "awaiting_confirmation"
  | "collecting"
  | "collected"
  | "returning"
  | "completed"
  | "stopped";
export type Review = {
  id: string;
  mission_id: string;
  captured_at: string;
  requested_color: SampleColor;
  image_url: string | null;
  simulated: boolean;
  image_source: string;
};
export type MissionState = {
  mode: "simulation" | "hardware";
  phase: Phase;
  target: SampleColor;
  mission_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  review: Review | null;
  collection_error: string | null;
  capabilities: {
    mission: boolean;
    manual: boolean;
    collection: boolean;
    return: boolean;
  };
  rover: string;
  pilot_id: string | null;
  led_color: SampleColor | null;
  camera: {
    source: string;
    status: string;
    frame_age_ms: number | null;
    error: string | null;
    sequence: number;
  };
  motion: { linear: number; angular: number };
  distance_cm: number | null;
  last_stop_acknowledged: boolean | null;
  events: { id: string; at: string; message: string; level: string }[];
};
export type CommandPayload = Record<string, unknown>;
export type SendCommand = (
  type: string,
  payload?: CommandPayload,
) => Promise<void>;
export const PHASE_LABELS: Record<Phase, string> = {
  idle: "Ready for launch",
  searching: "Searching for sample",
  approaching: "Approaching sample",
  awaiting_confirmation: "Awaiting your confirmation",
  collecting: "Collecting sample",
  collected: "Sample collected",
  returning: "Assisted return",
  completed: "Mission complete",
  stopped: "Stop latched",
};
export const SAMPLE_HEX: Record<SampleColor, string> = {
  red: "#f06b7c",
  green: "#65c79b",
  blue: "#79aaf4",
};
