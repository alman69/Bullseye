import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AutopilotPanel } from "@/components/autopilot-panel";
import type { MissionState } from "@/lib/protocol";

afterEach(cleanup);

const state = {
  bench: { arm_token: "tok", auto: { state: "idle", message: "" } },
  vision: { available: true, error: null },
} as unknown as MissionState;

it("starts a red or green sample mission and keeps a separate recovery return", () => {
  const act = vi.fn().mockResolvedValue(true);
  render(<AutopilotPanel state={state} canStep act={act} />);
  expect(screen.queryByRole("button", { name: "blue" })).toBeNull();
  expect(screen.queryByRole("button", { name: "yellow" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "green" }));
  fireEvent.click(screen.getByRole("button", { name: "Start sample mission" }));
  expect(act).toHaveBeenCalledWith("auto_mission", { color: "green", avoid: true, arm_token: "tok" });
  fireEvent.click(screen.getByRole("button", { name: /Return to base/ }));
  expect(act).toHaveBeenCalledWith("auto_return", { avoid: true, arm_token: "tok" });
});

it("stays disabled until controls are enabled", () => {
  render(<AutopilotPanel state={state} canStep={false} act={vi.fn()} />);
  expect((screen.getByRole("button", { name: "Start sample mission" }) as HTMLButtonElement).disabled).toBe(true);
});

it("shows sample arrival and home completion without a pickup confirmation", () => {
  const reached = { ...state, bench: { arm_token: "tok", auto: { state: "sample_reached", message: "", expression: "happy" } } } as unknown as MissionState;
  const { rerender } = render(<AutopilotPanel state={reached} canStep act={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toContain("Sample reached");
  expect(screen.getByRole("status").textContent).toContain("returning home");
  expect(screen.queryByText(/manual pickup/)).toBeNull();
  const home = { ...state, bench: { arm_token: "tok", auto: { state: "home_arrived", message: "" } } } as unknown as MissionState;
  rerender(<AutopilotPanel state={home} canStep act={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toContain("Home reached");
  expect(screen.getByRole("status").textContent).toContain("mission complete");
});
