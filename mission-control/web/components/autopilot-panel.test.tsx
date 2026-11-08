import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AutopilotPanel } from "@/components/autopilot-panel";
import type { MissionState } from "@/lib/protocol";

afterEach(cleanup);

const state = {
  bench: { arm_token: "tok", auto: { state: "idle", message: "" } },
  vision: { available: true, error: null },
} as unknown as MissionState;

it("sends go-to and return-to-base with the current arm token", () => {
  const act = vi.fn().mockResolvedValue(true);
  render(<AutopilotPanel state={state} canStep act={act} />);
  fireEvent.click(screen.getByRole("button", { name: "blue" }));
  fireEvent.click(screen.getByRole("button", { name: /Avoid obstacles: off/ }));
  fireEvent.click(screen.getByRole("button", { name: /Go to blue/ }));
  expect(act).toHaveBeenCalledWith("auto_goto", { color: "blue", avoid: true, arm_token: "tok" });
  fireEvent.click(screen.getByRole("button", { name: /Return to base/ }));
  expect(act).toHaveBeenCalledWith("auto_return", { avoid: true, arm_token: "tok" });
});

it("stays disabled until controls are enabled", () => {
  render(<AutopilotPanel state={state} canStep={false} act={vi.fn()} />);
  expect((screen.getByRole("button", { name: /Go to red/ }) as HTMLButtonElement).disabled).toBe(true);
});
