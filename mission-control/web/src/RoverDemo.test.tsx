import { act as flushReact, cleanup, fireEvent, render, screen } from "@testing-library/react";
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
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); act.mockResolvedValue(true); state = structuredClone(initial); useMissionMock.mockImplementation(() => hook()); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("uses separate bench endpoints and sends one default step per fresh token", () => {
  const { rerender } = render(<RoverDemo />);
  expect(useMissionMock).toHaveBeenCalledWith("/ws/bench", "/api/bench/stop");
  fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
  fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
  expect(act).toHaveBeenCalledTimes(1);
  expect(act).toHaveBeenCalledWith("bench_pulse", { direction: "forward", speed: 40, duration_ms: 200, arm_token: "first" });
  state.bench!.arm_token = "next";
  rerender(<RoverDemo />);
  fireEvent.change(screen.getByLabelText("Button step duration"), { target: { value: "1000" } });
  fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
  expect(act).toHaveBeenLastCalledWith("bench_pulse", { direction: "forward", speed: 40, duration_ms: 1000, arm_token: "next" });
});

it("provides a simple enable/disable control without a wheels checkbox or automatic enabling", () => {
  state.bench!.armed = false;
  state.bench!.arm_token = null;
  render(<RoverDemo />);
  const enable = screen.getByRole("button", { name: "Enable controls" });
  expect(enable.hasAttribute("disabled")).toBe(false);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByText(/wheels raised/i)).toBeNull();
  expect(act).not.toHaveBeenCalled();
  fireEvent.click(enable);
  expect(act).toHaveBeenCalledWith("bench_arm");
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

it("renews a held key until release instead of sending button steps or native key repeats", async () => {
  render(<RoverDemo />);
  await flushReact(async () => { fireEvent.keyDown(screen.getByRole("button", { name: "Step forward" }), { key: "w", code: "KeyW" }); });
  fireEvent.keyDown(window, { key: "w", code: "KeyW", repeat: true });
  await flushReact(async () => { await vi.advanceTimersByTimeAsync(450); });
  expect(act.mock.calls.filter((call) => call[0] === "hold_start")).toHaveLength(1);
  expect(act.mock.calls.filter((call) => call[0] === "hold_update").length).toBeGreaterThanOrEqual(3);
  expect(act).not.toHaveBeenCalledWith("bench_pulse", expect.anything());
  const first = act.mock.calls[0][1]!;
  fireEvent.keyUp(window, { key: "w", code: "KeyW" });
  expect(act).toHaveBeenLastCalledWith("hold_end", { hold_id: first.hold_id, arm_token: "first" });
  const count = act.mock.calls.length;
  await flushReact(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(act).toHaveBeenCalledTimes(count);
  fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight" });
  expect(act).toHaveBeenCalledTimes(count);
  fireEvent.keyDown(screen.getByRole("slider"), { key: " ", code: "Space" });
  expect(stop).toHaveBeenCalledTimes(1);
});

it("uses the newest held direction and falls back to the remaining key", async () => {
  render(<RoverDemo />);
  await flushReact(async () => { fireEvent.keyDown(window, { key: "W", code: "KeyW" }); });
  await flushReact(async () => { fireEvent.keyDown(window, { key: "a", code: "KeyA" }); });
  expect(act).toHaveBeenLastCalledWith("hold_update", expect.objectContaining({ direction: "left" }));
  await flushReact(async () => { fireEvent.keyUp(window, { key: "a", code: "KeyA" }); });
  expect(act).toHaveBeenLastCalledWith("hold_update", expect.objectContaining({ direction: "forward" }));
  fireEvent.keyUp(window, { key: "w", code: "KeyW" });
  expect(act.mock.calls.at(-1)![0]).toBe("hold_end");
});

it("does not queue renewals behind an unacknowledged start but still sends release", async () => {
  let finish!: (accepted: boolean) => void;
  act.mockImplementationOnce(() => new Promise<boolean>((resolve) => { finish = resolve; }));
  render(<RoverDemo />);
  fireEvent.keyDown(window, { key: "w", code: "KeyW" });
  await flushReact(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(act).toHaveBeenCalledTimes(1);
  fireEvent.keyUp(window, { key: "w", code: "KeyW" });
  expect(act.mock.calls.at(-1)![0]).toBe("hold_end");
  await flushReact(async () => { finish(true); await vi.advanceTimersByTimeAsync(500); });
  expect(act).toHaveBeenCalledTimes(2);
});

it("does not start a queued key after it was released during a button step", async () => {
  state.bench!.busy = true;
  const { rerender } = render(<RoverDemo />);
  fireEvent.keyDown(window, { key: "w", code: "KeyW" });
  fireEvent.keyUp(window, { key: "w", code: "KeyW" });
  state.bench!.busy = false;
  state.bench!.arm_token = "next";
  rerender(<RoverDemo />);
  await flushReact(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(act).not.toHaveBeenCalled();
});

it("disables through the red toggle even while moving and displays live/stale/invalid distance", () => {
  state.bench!.busy = true;
  state.bench!.distance_status = "live";
  state.bench!.distance_age_ms = 200;
  const { rerender } = render(<RoverDemo />);
  const disable = screen.getByRole("button", { name: "Disable controls" });
  expect(disable.hasAttribute("disabled")).toBe(false);
  fireEvent.click(disable);
  expect(stop).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Live · 0.2s ago/)).toBeTruthy();
  state.bench!.distance_status = "stale";
  state.bench!.distance_age_ms = 3200;
  rerender(<RoverDemo />);
  expect(screen.getByText(/Stale · 3.2s ago/)).toBeTruthy();
  state.distance_cm = null;
  state.bench!.distance_status = "invalid";
  rerender(<RoverDemo />);
  expect(screen.getByText("No echo")).toBeTruthy();
});

it("disables steps and requests stop when the browser connection drops", () => {
  const { rerender } = render(<RoverDemo />);
  useMissionMock.mockImplementation(() => hook({ connected: false, isPilot: false }));
  rerender(<RoverDemo />);
  expect(stop).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Step forward" }).hasAttribute("disabled")).toBe(true);
  expect(act).not.toHaveBeenCalled();
});

it("clears a physically held key on disconnect and never resumes it after reconnect", async () => {
  const { rerender } = render(<RoverDemo />);
  await flushReact(async () => { fireEvent.keyDown(window, { key: "w", code: "KeyW" }); });
  expect(act.mock.calls[0][0]).toBe("hold_start");
  useMissionMock.mockImplementation(() => hook({ connected: false, isPilot: false }));
  rerender(<RoverDemo />);
  state.bench!.arm_token = "reconnected";
  useMissionMock.mockImplementation(() => hook());
  rerender(<RoverDemo />);
  await flushReact(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(act).toHaveBeenCalledTimes(1);
  fireEvent.keyUp(window, { key: "w", code: "KeyW" });
  await flushReact(async () => { fireEvent.keyDown(window, { key: "w", code: "KeyW" }); });
  expect(act).toHaveBeenLastCalledWith("hold_start", expect.objectContaining({ arm_token: "reconnected" }));
});

it("omits scooper controls even when the backend reports a ready scooper", () => {
  state.bench!.armed = false;
  state.bench!.scooper = { available: true, status: "ready", address: "http://192.168.4.50", error: null, state: "holding", sequence: null, step: null, angles: { left: 90, right: 90 }, busy: null };
  render(<RoverDemo />);
  expect(screen.queryByRole("button", { name: "Squeeze" })).toBeNull();
  expect(screen.queryByRole("region", { name: /scooper/i })).toBeNull();
  expect(act).not.toHaveBeenCalled();
});
