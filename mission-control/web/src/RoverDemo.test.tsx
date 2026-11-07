import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RoverDemo from "./RoverDemo";
import type { MissionState } from "@/lib/protocol";

const { useMissionMock, act, stop } = vi.hoisted(() => ({ useMissionMock: vi.fn(), act: vi.fn(async (_type: string, _payload?: Record<string, unknown>) => true), stop: vi.fn(async () => {}) }));
vi.mock("@/hooks/use-mission", () => ({ useMission: useMissionMock }));
vi.mock("@/components/ui/halftone-nebula", () => ({ default: () => null }));
vi.mock("@/components/camera-view", () => ({ CameraView: () => <div data-testid="camera">Shared live camera</div> }));

const initial: MissionState = {
  mode: "hardware", phase: "idle", target: "red", mission_id: null, started_at: null, finished_at: null,
  review: null, collection_error: null, capabilities: { mission: false, manual: false, collection: false, return: false },
  rover: "connected", pilot_id: "pilot", led_color: null,
  camera: { source: "live", status: "streaming", frame_age_ms: 100, error: null, sequence: 3 },
  motion: { linear: 0, angular: 0 }, distance_cm: 11, last_stop_acknowledged: true, events: [],
  bench: { enabled: true, status: "ready", error: null, armed: true, arm_token: "first", busy: false, direction: null, stop_pending: false, heartbeats: 4, max_duration_ms: 1000, max_speed: 100 },
};
let state: MissionState;
const hook = (overrides = {}) => ({ state, connected: true, isPilot: true, act, stop, error: null, clearError: vi.fn(), latency: { status: "ready", rttMs: 12 }, ...overrides });
beforeEach(() => { vi.clearAllMocks(); state = structuredClone(initial); useMissionMock.mockImplementation(() => hook()); });
afterEach(cleanup);

it("uses separate bench endpoints and sends one default step per fresh token", () => {
  const { rerender } = render(<RoverDemo />);
  expect(useMissionMock).toHaveBeenCalledWith("/ws/bench", "/api/bench/stop");
  fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
  fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
  expect(act).toHaveBeenCalledTimes(1);
  expect(act).toHaveBeenCalledWith("bench_pulse", { direction: "forward", speed: 40, duration_ms: 200, arm_token: "first" });
  state.bench!.arm_token = "next";
  rerender(<RoverDemo />);
  fireEvent.change(screen.getByLabelText("Step duration"), { target: { value: "1000" } });
  fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
  expect(act).toHaveBeenLastCalledWith("bench_pulse", { direction: "forward", speed: 40, duration_ms: 1000, arm_token: "next" });
});

it("requires the wheels checkbox before enabling and never enables automatically", () => {
  state.bench!.armed = false;
  state.bench!.arm_token = null;
  render(<RoverDemo />);
  const enable = screen.getByRole("button", { name: "Enable single steps" });
  expect(enable.hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("checkbox"));
  expect(enable.hasAttribute("disabled")).toBe(false);
  expect(act).not.toHaveBeenCalled();
  fireEvent.click(enable);
  expect(act).toHaveBeenCalledWith("bench_arm", { wheels_raised: true });
});

it("leaves STOP available to viewers and disables all direction commands", () => {
  useMissionMock.mockImplementation(() => hook({ isPilot: false }));
  render(<RoverDemo />);
  expect(screen.getByRole("button", { name: "Step forward" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "STOP rover" }));
  fireEvent.click(screen.getByRole("button", { name: "STOP rover from direction pad" }));
  expect(stop).toHaveBeenCalledTimes(2);
  expect(act).not.toHaveBeenCalled();
});

it("stops on blur without resuming when focus returns", () => {
  render(<RoverDemo />);
  fireEvent.blur(window);
  fireEvent.focus(window);
  expect(stop).toHaveBeenCalledTimes(1);
  expect(act).not.toHaveBeenCalled();
});

it("ignores key repeat and inputs, and requests halt on keyboard release", () => {
  render(<RoverDemo />);
  fireEvent.keyDown(screen.getByRole("button", { name: "Step forward" }), { key: "ArrowUp" });
  fireEvent.keyDown(window, { key: "ArrowUp", repeat: true });
  fireEvent.keyUp(window, { key: "ArrowUp" });
  expect(act.mock.calls.map((call) => call[0])).toEqual(["bench_pulse", "halt"]);
  fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight" });
  expect(act).toHaveBeenCalledTimes(2);
  fireEvent.keyDown(screen.getByRole("slider"), { key: " ", code: "Space" });
  expect(stop).toHaveBeenCalledTimes(1);
});

it("disables steps and requests stop when the browser connection drops", () => {
  const { rerender } = render(<RoverDemo />);
  useMissionMock.mockImplementation(() => hook({ connected: false, isPilot: false }));
  rerender(<RoverDemo />);
  expect(stop).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Step forward" }).hasAttribute("disabled")).toBe(true);
  expect(act).not.toHaveBeenCalled();
});
