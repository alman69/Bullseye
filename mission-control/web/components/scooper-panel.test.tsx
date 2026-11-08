import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ScooperPanel } from "./scooper-panel";
import type { ScooperState } from "@/lib/protocol";

const ready: ScooperState = {
  available: true, status: "ready", address: "http://192.168.4.50", error: null, state: "holding",
  sequence: null, step: null, angles: { left: 90, right: 90 }, busy: null,
};
afterEach(cleanup);

it("sends squeeze, release and straight-down when the link is ready", () => {
  const onAction = vi.fn();
  render(<ScooperPanel scooper={ready} canUse onAction={onAction} />);
  expect(screen.getByText("Straight down", { selector: "strong" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Squeeze" }));
  fireEvent.click(screen.getByRole("button", { name: "Release" }));
  fireEvent.click(screen.getByRole("button", { name: "Straight down" }));
  expect(onAction.mock.calls.map(([kind]) => kind)).toEqual(["scooper_squeeze", "scooper_release", "scooper_home"]);
});

it("only offers Connect until the Wi-Fi link is ready", () => {
  const onAction = vi.fn();
  render(<ScooperPanel scooper={{ ...ready, status: "error", state: null, angles: {}, error: "Scooper not reachable at http://192.168.4.50 (ConnectTimeout). Is it powered and joined to the rover's ELEGOO Wi-Fi?" }} canUse onAction={onAction} />);
  expect(screen.getByRole("button", { name: "Squeeze" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("status").textContent).toContain("not reachable");
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  expect(onAction).toHaveBeenCalledWith("scooper_connect");
});

it("locks the buttons for viewers and while a squeeze is running", () => {
  const { rerender } = render(<ScooperPanel scooper={ready} canUse={false} onAction={vi.fn()} />);
  for (const name of ["Squeeze", "Release", "Straight down", "Reconnect"])
    expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
  rerender(<ScooperPanel scooper={{ ...ready, state: "sequence", sequence: "squeeze", step: "in", angles: { left: 140, right: 140 } }} canUse onAction={vi.fn()} />);
  expect(screen.getByText("Squeezing · in")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Squeeze" }).hasAttribute("disabled")).toBe(true);
});

it("shows the scooper's Wi-Fi address once connected", () => {
  render(<ScooperPanel scooper={ready} canUse onAction={vi.fn()} />);
  expect(screen.getByText("WI-FI · 192.168.4.50")).toBeTruthy();
});
