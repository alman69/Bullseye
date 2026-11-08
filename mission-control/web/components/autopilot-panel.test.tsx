import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AutopilotPanel } from "@/components/autopilot-panel";
import type { MissionState } from "@/lib/protocol";

afterEach(cleanup);

const state = {
  bench: { arm_token: "tok", distance_status: "live", auto: { state: "idle", message: "" } },
  distance_cm: 42,
  vision: { available: true, error: null },
} as unknown as MissionState;
const props = { canStep: true, canAct: true, onStop: vi.fn() };
const missionState = (mode: NonNullable<NonNullable<MissionState["bench"]>["auto"]>["state"]) => ({
  ...state,
  bench: { ...state.bench, armed: false, arm_token: null, auto: { state: mode, message: "", target: "green", run_id: "run-1" } },
}) as MissionState;

it("starts the ruby/emerald search and explains the 360 scan, 10 cm stop and manual handover", async () => {
  const act = vi.fn().mockResolvedValue(true);
  render(<AutopilotPanel {...props} state={state} act={act} />);
  expect(screen.queryByRole("button", { name: /blue|obstacle/i })).toBeNull();
  expect(screen.queryByRole("button", { name: "yellow" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Return to base/ })).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("hands you the controls");
  expect(screen.getByRole("list", { name: "Mission steps" }).textContent).toContain("Scan 360°");
  expect(screen.getByRole("list", { name: "Mission steps" }).textContent).toContain("10 cm");
  fireEvent.click(screen.getByRole("button", { name: "emerald" }));
  expect(act).toHaveBeenCalledWith("vision_target", { color: "green" });
  fireEvent.click(screen.getByRole("button", { name: "Search for the emerald" }));
  expect(act).toHaveBeenCalledWith("auto_mission", { color: "green", avoid: true, arm_token: "tok" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Search for the emerald" }).hasAttribute("disabled")).toBe(false));
});

it("stays disabled until the rover is connected while keeping STOP available", () => {
  const onStop = vi.fn();
  render(<AutopilotPanel {...props} state={state} canStep={false} canAct={false} act={vi.fn()} onStop={onStop} />);
  expect(screen.getByRole("button", { name: "Search for the ruby" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "STOP sample mission" }));
  expect(onStop).toHaveBeenCalledTimes(1);
});

it("hands over to manual driving at 10 cm, then confirms home delivery", async () => {
  const act = vi.fn().mockResolvedValue(true);
  const { rerender } = render(<AutopilotPanel {...props} state={missionState("manual_pickup")} act={act} />);
  expect(screen.getByRole("status").textContent).toContain("Squeeze to grab it");
  expect(screen.queryByRole("button", { name: /Search for/ })).toBeNull();
  expect(screen.getByRole("region", { name: "Sample mission" }).textContent).toContain("emerald");
  const delivered = screen.getByRole("button", { name: "Delivered to home base" });
  fireEvent.click(delivered);
  expect(act).toHaveBeenCalledWith("auto_home_confirm", { run_id: "run-1" });
  await waitFor(() => expect(delivered.hasAttribute("disabled")).toBe(false));
  rerender(<AutopilotPanel {...props} state={missionState("home_arrived")} act={act} />);
  expect(screen.getByRole("status").textContent).toContain("Mission complete");
  expect(act.mock.calls.some(([type]) => type === "auto_return")).toBe(false);
});

it("blocks delivery for viewers and prevents repeated pending submissions", async () => {
  let finish!: (accepted: boolean) => void;
  const act = vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; }));
  const { rerender } = render(<AutopilotPanel {...props} state={missionState("manual_pickup")} canAct={false} act={act} />);
  const delivered = screen.getByRole("button", { name: "Delivered to home base" });
  expect(delivered.hasAttribute("disabled")).toBe(true);
  rerender(<AutopilotPanel {...props} state={missionState("manual_pickup")} act={act} />);
  fireEvent.click(delivered);
  fireEvent.click(delivered);
  expect(act).toHaveBeenCalledTimes(1);
  expect(delivered.hasAttribute("disabled")).toBe(true);
  finish(true);
  await waitFor(() => expect(delivered.hasAttribute("disabled")).toBe(false));
});

it("shows actual mission target and scan updates without hiding failures or stale range", () => {
  const active = missionState("scanning");
  active.bench!.auto!.message = "Scanning 180° / 360°";
  const { rerender } = render(<AutopilotPanel {...props} state={active} canStep={false} canAct={false} act={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toContain("for the emerald");
  expect(screen.getByText("Scanning 180° / 360°")).toBeTruthy();
  expect(screen.queryByRole("group", { name: "Sample" })).toBeNull();
  const failed = missionState("failed");
  failed.bench!.auto!.message = "Ultrasonic reading unavailable. Stopped.";
  failed.bench!.distance_status = "stale";
  rerender(<AutopilotPanel {...props} state={failed} canStep={false} act={vi.fn()} />);
  expect(screen.getByRole("alert").textContent).toContain("Ultrasonic reading unavailable");
  expect(screen.getByText("Stale")).toBeTruthy();
  expect(screen.queryByText("42 cm")).toBeNull();
});

it("shows the newly selected target after completing a different sample mission", () => {
  render(<AutopilotPanel {...props} state={missionState("home_arrived")} act={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "ruby" }));
  const telemetry = screen.getByLabelText("Mission telemetry");
  expect(within(telemetry).getByText("ruby")).toBeTruthy();
  expect(within(telemetry).queryByText("emerald")).toBeNull();
});
