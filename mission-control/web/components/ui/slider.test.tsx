import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Slider } from "./slider";

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Speed({ visible }: { visible: boolean }) {
  const [value, setValue] = useState([35]);
  return (
    <div style={{ display: visible ? "block" : "none" }}>
      <Slider
        aria-label="Drive speed"
        value={value}
        min={10}
        max={70}
        step={5}
        onValueChange={(v) => setValue(Array.isArray(v) ? v : [v])}
      />
      <output>{value[0]}</output>
    </div>
  );
}

it("exposes a positioned, keyboard-operable speed control after a hidden panel becomes visible", () => {
  const { rerender, container } = render(<Speed visible={false} />);
  expect(screen.queryByRole("slider")).toBeNull();
  rerender(<Speed visible />);
  const slider = screen.getByRole("slider");
  expect(slider.getAttribute("aria-valuenow")).toBe("35");
  expect(
    (container.querySelector('[data-slot="slider-thumb"]') as HTMLElement).style
      .visibility,
  ).not.toBe("hidden");
  fireEvent.keyDown(slider, { key: "ArrowRight" });
  expect(slider.getAttribute("aria-valuenow")).toBe("40");
});
