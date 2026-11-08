import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VisionFrame, VisionOverlayOptions } from "@/lib/vision";
import { MASK_TOOL } from "@/lib/vision";
import { VisionFreezeControls, VisionOverlay } from "./vision-overlay";

const { frame } = vi.hoisted(() => ({ frame: {
  type: "vision", seq: 1, w: 800, h: 600, target: "red", mode: "palette",
  detection: { detected: false, direction: "NONE", horizontal_error: null, distance_cm_est: null, area_ratio: null, candidates: 0 }, box: null, center: null, others: [], deadband: 0.12,
  free: null, lines: { stop: null, zone: null, horizon: null },
  wb: null, processing_ms: 1, fps: 10,
} }));
vi.mock("@/hooks/use-vision-feed", () => ({ useVisionFeed: (cb: (f: VisionFrame) => void) => cb(frame as VisionFrame) }));
beforeEach(() => {
  vi.stubGlobal("PointerEvent", MouseEvent);
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => { cb(); return 1; });
  const context = new Proxy({}, { get: (_, key) => key === "measureText" ? () => ({ width: 10 }) : () => {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600, toJSON() {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function options(overrides: Partial<VisionOverlayOptions> = {}): VisionOverlayOptions {
  return { show: true, floor: false, mask: "off", samplingClass: "red",
    frozen: { id: "1", image: "data:image/png;base64,test", frame: frame as VisionFrame }, ...overrides };
}

describe("video sampling gestures", () => {
  it("turns a click into a small pixel patch", () => {
    const sample = vi.fn(); render(<VisionOverlay {...options({ onSampleBox: sample })} />);
    const canvas = screen.getByLabelText("Click or drag to sample red");
    fireEvent.pointerDown(canvas, { clientX: 400, clientY: 300 });
    fireEvent.pointerUp(canvas, { clientX: 400, clientY: 300 });
    expect(sample).toHaveBeenCalledWith([395/800, 295/600, 405/800, 305/600]);
  });
  it("saves a traced polygon and cancels interrupted gestures", () => {
    const save = vi.fn(); render(<VisionOverlay {...options({ samplingClass: MASK_TOOL, onMaskPolygon: save })} />);
    const canvas = screen.getByLabelText("Trace rover outline on the frozen video");
    fireEvent.pointerDown(canvas, { clientX: 100, clientY: 400 });
    fireEvent.pointerMove(canvas, { clientX: 200, clientY: 400 });
    fireEvent.pointerMove(canvas, { clientX: 200, clientY: 500 });
    fireEvent.pointerMove(canvas, { clientX: 100, clientY: 500 });
    fireEvent.pointerUp(canvas);
    expect(save).toHaveBeenCalledWith([[0.125, 2/3], [0.25, 2/3], [0.25, 5/6], [0.125, 5/6]]);
    fireEvent.pointerDown(canvas, { clientX: 100, clientY: 400 });
    fireEvent.pointerMove(canvas, { clientX: 200, clientY: 400 });
    fireEvent.pointerCancel(canvas);
    fireEvent.pointerUp(canvas);
    expect(save).toHaveBeenCalledTimes(1);
  });
  it("rejects clicks in the letterbox and offers a direct return to live", () => {
    const sample = vi.fn(), resume = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1200, height: 600 } as DOMRect);
    render(<><VisionOverlay {...options({ onSampleBox: sample })} /><VisionFreezeControls {...options({ onResumeLive: resume })} /></>);
    const canvas = screen.getByLabelText("Click or drag to sample red");
    fireEvent.pointerDown(canvas, { clientX: 50, clientY: 300 });
    fireEvent.pointerUp(canvas);
    expect(sample).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Back to live video" }));
    expect(resume).toHaveBeenCalled();
  });
});
