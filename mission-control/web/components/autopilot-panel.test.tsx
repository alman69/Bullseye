import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AutopilotPanel } from "@/components/autopilot-panel";
import type { MissionState } from "@/lib/protocol";

afterEach(cleanup);

const state = {
  bench: { arm_token: "tok", auto: { state: "idle", message: "" } },
  vision: { available: true, error: null },
} as unknown as MissionState;

it("sends go-to, sample collected and sample deposited with the current arm token", () => {
  const act = vi.fn().mockResolvedValue(true);
  render(<AutopilotPanel state={state} canStep act={act} />);
  expect(screen.queryByRole("button", { name: "blue" })).toBeNull(); // blue = obstacles, not a target
  fireEvent.click(screen.getByRole("button", { name: "green" }));
  expect(screen.getByRole("button", { name: /Avoid obstacles: on/ })).toBeTruthy(); // on by default
  expect(screen.queryByRole("button", { name: /Run mission|Return to base/ })).toBeNull(); // home is driven by hand
  fireEvent.click(screen.getByRole("button", { name: /Go to green/ }));
  expect(act).toHaveBeenCalledWith("auto_goto", { color: "green", avoid: true, arm_token: "tok" });
  fireEvent.click(screen.getByRole("button", { name: /Sample collected/ }));
  expect(act).toHaveBeenCalledWith("sample_collected", { arm_token: "tok" });
  fireEvent.click(screen.getByRole("button", { name: /Sample deposited/ }));
  expect(act).toHaveBeenCalledWith("sample_deposited", { arm_token: "tok" });
});

it("stays disabled until controls are enabled", () => {
  render(<AutopilotPanel state={state} canStep={false} act={vi.fn()} />);
  expect((screen.getByRole("button", { name: /Go to red/ }) as HTMLButtonElement).disabled).toBe(true);
});

it("shows the mission banners", () => {
  const collected = { ...state, bench: { arm_token: "tok", auto: { state: "collected", message: "" } } } as unknown as MissionState;
  render(<AutopilotPanel state={collected} canStep act={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toContain("Sample collected");
});
