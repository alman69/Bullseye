import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ManualControls } from "./manual-controls";

vi.mock("@/components/ui/slider", () => ({
  Slider: () => <input aria-label="Drive speed" type="range" />,
}));
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  HTMLElement.prototype.setPointerCapture = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("renews a held pointer and sends zero immediately on release", () => {
  const send = vi.fn().mockResolvedValue(undefined);
  render(<ManualControls enabled send={send} onStop={vi.fn()} />);
  fireEvent.pointerDown(screen.getByRole("button", { name: "Drive forward" }), {
    pointerId: 1,
  });
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0.35, angular: 0 });
  vi.advanceTimersByTime(200);
  expect(send).toHaveBeenCalledTimes(3);
  fireEvent.pointerUp(window);
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0, angular: 0 });
  const count = send.mock.calls.length;
  vi.advanceTimersByTime(500);
  expect(send).toHaveBeenCalledTimes(count);
});

it("stops a held drive when input is disabled by a tab, drawer, or lost link", () => {
  const send = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(
    <ManualControls enabled send={send} onStop={vi.fn()} />,
  );
  fireEvent.keyDown(document.body, { key: "w" });
  rerender(<ManualControls enabled={false} send={send} onStop={vi.fn()} />);
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0, angular: 0 });
  const count = send.mock.calls.length;
  vi.advanceTimersByTime(500);
  expect(send).toHaveBeenCalledTimes(count);
});

it("supports keys from buttons, stops on key-up or blur, and ignores stale repeat events", () => {
  const send = vi.fn().mockResolvedValue(undefined);
  render(<ManualControls enabled send={send} onStop={vi.fn()} />);
  const button = screen.getByRole("button", { name: "Drive forward" });
  fireEvent.keyDown(button, { key: "w" });
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0.35, angular: 0 });
  fireEvent.keyUp(button, { key: "w" });
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0, angular: 0 });
  fireEvent.keyDown(document.body, { key: "d" });
  fireEvent.blur(window);
  expect(send).toHaveBeenLastCalledWith("drive", { linear: 0, angular: 0 });
  const count = send.mock.calls.length;
  fireEvent.keyDown(document.body, { key: "d", repeat: true });
  fireEvent.keyDown(screen.getByLabelText("Drive speed"), { key: "ArrowUp" });
  expect(send).toHaveBeenCalledTimes(count);
});
