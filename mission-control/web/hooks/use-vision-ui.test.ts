import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useVisionUi } from "./use-vision-ui";

const frozen = { id: "7", image: "data:image/png;base64,test", frame: { w: 800, h: 600 } };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("frozen calibration", () => {
  it("samples the immutable frame, reuses it for another material, and resumes", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => frozen });
    vi.stubGlobal("fetch", fetcher);
    const send = vi.fn().mockResolvedValue(true);
    const { result } = renderHook(() => useVisionUi(send));
    await act(async () => { await result.current.setSamplingClass("red"); });
    act(() => result.current.overlay.onSampleBox?.([0.1, 0.2, 0.3, 0.4]));
    expect(send).toHaveBeenCalledWith("vision_palette_sample", { cls: "red", box: [0.1, 0.2, 0.3, 0.4], frame_id: "7" });
    await act(async () => { await result.current.setSamplingClass("__rover_mask__"); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.overlay.onSampleBox).toBeUndefined();
    act(() => result.current.overlay.onMaskPolygon?.([[0, 0.8], [0.2, 0.8], [0.2, 1]]));
    expect(send).toHaveBeenLastCalledWith("vision_mask_polygon", { points: [[0, 0.8], [0.2, 0.8], [0.2, 1]] });
    act(() => result.current.resumeLive());
    expect(result.current.frozen).toBeNull();
    expect(result.current.overlay.onMaskPolygon).toBeUndefined();
  });

  it("does not sample when the camera is stale", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: "Camera frame is stale." }) }));
    const { result } = renderHook(() => useVisionUi(vi.fn()));
    await act(async () => { await result.current.setSamplingClass("red"); });
    expect(result.current.freezeError).toBe("Camera frame is stale.");
    expect(result.current.overlay.onSampleBox).toBeUndefined();
  });

  it("does not restore a frozen view after calibration is closed during a request", async () => {
    let resolve!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise((done) => { resolve = done; })));
    const { result } = renderHook(() => useVisionUi(vi.fn()));
    let pending: Promise<void>;
    act(() => { pending = result.current.setSamplingClass("blue"); });
    act(() => result.current.setCalibrating(false));
    await act(async () => { resolve({ ok: true, json: async () => frozen }); await pending; });
    expect(result.current.frozen).toBeNull();
    expect(result.current.samplingClass).toBeNull();
  });
});
