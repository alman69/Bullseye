import type { VisionSummary } from "@/lib/vision";

export type SampleColor = "red" | "green" | "blue" | "yellow";
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
  bench?: {
    enabled: boolean;
    status: string;
    error: string | null;
    armed: boolean;
    arm_token: string | null;
    busy: boolean;
    direction: string | null;
    stop_pending: boolean;
    heartbeats: number;
    max_duration_ms: number;
    max_speed: number;
    drive_mode?: "step" | "hold" | null;
    hold_id?: string | null;
    distance_status?: "live" | "invalid" | "stale" | "unavailable";
    distance_age_ms?: number | null;
    scooper?: ScooperState;
    auto?: { state: "idle" | "running" | "arrived" | "collected" | "deposited" | "sample_reached" | "home_arrived" | "done" | "failed" | "stopped"; message: string; expression?: "happy" | "auto" | null };
  };
  events: { id: string; at: string; message: string; level: string }[];
  vision?: VisionSummary | null;
};
// Combined display/scooper over BLE; legacy standalone HTTP remains optional.
export type ScooperState = {
  available: boolean;
  status: "disconnected" | "connecting" | "ready" | "error";
  address: string;
  error: string | null;
  state: "relaxed" | "holding" | "moving" | "sequence" | null;
  sequence: string | null;
  step: string | null;
  angles: Record<string, number>;
  busy: string | null;
  transport?: "ble" | "http";
  display_updates?: number;
  ble_connected?: boolean;
  servo_ready?: boolean | null;
  heartbeats_sent?: number;
  heartbeats_acknowledged?: number;
  heartbeat_age_ms?: number | null;
  max_heartbeat_gap_ms?: number;
  device_boot_id?: number | null;
  device_boot_changes?: number;
};
export type CommandPayload = Record<string, unknown>;
export type LinkLatency = {
  status: "offline" | "measuring" | "ready" | "timeout";
  rttMs: number | null;
};
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
  yellow: "#f2d15c",
};
