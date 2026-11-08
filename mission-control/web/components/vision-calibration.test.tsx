import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VisionCalibration } from "@/components/vision-calibration";
import { VisionPanel } from "@/components/vision-panel";
import type { VisionUi } from "@/hooks/use-vision-ui";
import type { VisionSummary } from "@/lib/vision";

afterEach(cleanup);

const summary: VisionSummary = {
  available: true,
  error: null,
  target: "red",
  mode: "classifier",
  detected: true,
  direction: "LEFT",
  horizontal_error: -0.31,
  distance_cm_est: 42,
  nav_state: "APPROACH",
  nav_label: "TURN LEFT 200 ms",
  nav_reason: "Aim at target",
  fps: 9.8,
  processing_ms: 6.1,
  message: null,
  sample_counts: { red: 120, floor: 4000 },
  confusion: { red: { red: 0.99, floor: 0.01, unknown: 0 }, floor: { red: 0.03, floor: 0.97, unknown: 0 } },
  ground: { calibrated: false, stop_line_y: null, horizon_y: null, captures: [{ distance_cm: 10, bottom_y: 0.9, width: 0.1, error: 0 }] },
  fov: { captures: {}, hfov_deg: null },
  steps: { forward: { "200": 4 }, turn: {} },
  tuning: { "floor.s_max": 45 },
};

function ui(overrides: Partial<VisionUi> = {}): VisionUi {
  return {
    overlay: { show: true, floor: true, mask: "off", samplingClass: null },
    show: true,
    setShow: vi.fn(),
    floor: true,
    setFloor: vi.fn(),
    mask: "off",
    setMask: vi.fn(),
    samplingClass: null,
    setSamplingClass: vi.fn(),
    calibrating: true,
    setCalibrating: vi.fn(),
    ...overrides,
  };
}

describe("VisionCalibration", () => {
  it("starts sampling a class and fits the model", () => {
    const act = vi.fn().mockResolvedValue(true);
    const view = ui();
    render(<VisionCalibration vision={summary} ui={view} canAct act={act} />);
    fireEvent.click(screen.getByRole("button", { name: /Floor/ }));
    expect(view.setSamplingClass).toHaveBeenCalledWith("floor");
    fireEvent.click(screen.getByRole("button", { name: "Fit color model" }));
    expect(act).toHaveBeenCalledWith("vision_fit");
    expect(screen.getByRole("table", { name: "Confusion table" })).toBeTruthy();
  });

  it("captures stop-line distances and saves the step table", () => {
    const act = vi.fn().mockResolvedValue(true);
    render(<VisionCalibration vision={summary} ui={ui()} canAct act={act} />);
    fireEvent.click(screen.getByRole("tab", { name: /Stop line/ }));
    fireEvent.click(screen.getByRole("button", { name: "15 cm" }));
    fireEvent.click(screen.getByRole("button", { name: "Capture at 15 cm" }));
    expect(act).toHaveBeenCalledWith("vision_ground_capture", { distance_cm: 15 });
    fireEvent.click(screen.getByRole("tab", { name: /Steps/ }));
    fireEvent.change(screen.getByLabelText("turn 500 ms degrees"), { target: { value: "38" } });
    fireEvent.click(screen.getByRole("button", { name: "Save step table" }));
    expect(act).toHaveBeenCalledWith("vision_steps", { forward: { "200": 4 }, turn: { "500": 38 } });
  });

  it("disables editing for viewers", () => {
    render(<VisionCalibration vision={summary} ui={ui()} canAct={false} act={vi.fn()} />);
    expect((screen.getByRole("button", { name: "Fit color model" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("VisionPanel", () => {
  it("shows detection and the dry-run plan, and picks the vision target on /bench", () => {
    const act = vi.fn().mockResolvedValue(true);
    render(<VisionPanel vision={summary} ui={ui()} canAct act={act} targetControl />);
    expect(screen.getByText("RED DETECTED")).toBeTruthy();
    expect(screen.getByText(/APPROACH → TURN LEFT 200 ms/)).toBeTruthy();
    expect(screen.getByText(/~42 cm/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "yellow" }));
    expect(act).toHaveBeenCalledWith("vision_target", { color: "yellow" });
  });

  it("explains when vision is off or unavailable", () => {
    const { rerender } = render(<VisionPanel vision={null} ui={ui()} canAct act={vi.fn()} />);
    expect(screen.getByText(/not running/)).toBeTruthy();
    rerender(<VisionPanel vision={{ available: false, error: "Vision unavailable: no cv2" }} ui={ui()} canAct act={vi.fn()} />);
    expect(screen.getByText(/no cv2/)).toBeTruthy();
  });
});
