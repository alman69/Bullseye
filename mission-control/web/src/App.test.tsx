import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { MissionState } from "@/lib/protocol";
import App from "./App";

const { send, useMissionMock, cameraMount } = vi.hoisted(() => ({
  send: vi.fn(),
  useMissionMock: vi.fn(),
  cameraMount: vi.fn(),
}));
vi.mock("@/hooks/use-mission", () => ({ useMission: useMissionMock }));
vi.mock("@/components/ui/halftone-nebula", () => ({ default: () => <div /> }));
vi.mock("@/components/ui/slider", () => ({
  Slider: () => <input aria-label="Drive speed" type="range" />,
}));
vi.mock("@/components/camera-view", async () => {
  const { useEffect } = await import("react");
  return {
    CameraView: () => {
      useEffect(() => {
        cameraMount();
      }, []);
      return <div data-testid="shared-camera">Camera connection</div>;
    },
  };
});

const state: MissionState = {
  mode: "simulation",
  phase: "idle",
  target: "red",
  mission_id: null,
  started_at: null,
  finished_at: null,
  review: null,
  collection_error: null,
  capabilities: { mission: true, manual: true, collection: true, return: true },
  rover: "simulated",
  pilot_id: "pilot",
  led_color: null,
  camera: {
    source: "simulation",
    status: "streaming",
    frame_age_ms: 25,
    error: null,
    sequence: 1,
  },
  motion: { linear: 0, angular: 0 },
  distance_cm: 120,
  last_stop_acknowledged: null,
  events: [],
};
const hookState = () => ({
  state,
  connected: true,
  sessionId: "pilot",
  isPilot: true,
  act: vi.fn().mockResolvedValue(true),
  send,
  stop: vi.fn(),
  error: null,
  clearError: vi.fn(),
  latency: { status: "ready", rttMs: 42 },
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  send.mockResolvedValue(undefined);
  useMissionMock.mockReturnValue(hookState());
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query) => ({
      matches: query === "(max-width: 800px)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.setPointerCapture = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("offers only red and green samples as mission objectives", () => {
  render(<App />);
  expect(screen.getByRole("button", { name: "Select red sample" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Select green sample" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Select blue sample" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Select yellow sample" })).toBeNull();
});

it("keeps one camera mounted and stops held motion when leaving the mobile Pilot tab", () => {
  render(<App />);
  const camera = screen.getByTestId("shared-camera");
  expect(
    screen
      .getByRole("button", { name: "Drive forward" })
      .hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.click(screen.getByRole("tab", { name: "Pilot" }));
  fireEvent.pointerDown(screen.getByRole("button", { name: "Drive forward" }), {
    pointerId: 1,
  });
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0.35, angular: 0 });
  fireEvent.click(screen.getByRole("tab", { name: "Mission" }));
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0, angular: 0 });
  expect(screen.getByTestId("shared-camera")).toBe(camera);
  expect(cameraMount).toHaveBeenCalledTimes(1);
});

it("stops manual input when opening Connections and labels pending hardware honestly", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("tab", { name: "Pilot" }));
  fireEvent.pointerDown(screen.getByRole("button", { name: "Drive forward" }), {
    pointerId: 1,
  });
  fireEvent.click(screen.getByRole("button", { name: "Open connections" }));
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0, angular: 0 });
  expect(screen.getByText("Awaiting integration")).toBeTruthy();
  expect(screen.getByText("Not tested")).toBeTruthy();
  expect(screen.getByText("42 ms round trip")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "STOP rover in connections" }),
  ).toBeTruthy();
});

it("stops a held drive immediately when the control link disappears", () => {
  const { rerender } = render(<App />);
  fireEvent.click(screen.getByRole("tab", { name: "Pilot" }));
  fireEvent.pointerDown(screen.getByRole("button", { name: "Drive forward" }), {
    pointerId: 1,
  });
  useMissionMock.mockReturnValue({
    ...hookState(),
    connected: false,
    sessionId: null,
    isPilot: false,
  });
  rerender(<App />);
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0, angular: 0 });
  expect(
    screen
      .getByRole("button", { name: "Drive forward" })
      .hasAttribute("disabled"),
  ).toBe(true);
});
