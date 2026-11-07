import { StrictMode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HalftoneNebula from "./halftone-nebula";

function fakeGl() {
  return {
    VERTEX_SHADER: 1,
    FRAGMENT_SHADER: 2,
    COMPILE_STATUS: 3,
    LINK_STATUS: 4,
    FRAMEBUFFER: 5,
    TRIANGLES: 6,
    createShader: vi.fn(() => ({})),
    shaderSource: vi.fn(),
    compileShader: vi.fn(),
    getShaderParameter: vi.fn(() => true),
    getShaderInfoLog: vi.fn(),
    deleteShader: vi.fn(),
    createProgram: vi.fn(() => ({})),
    attachShader: vi.fn(),
    linkProgram: vi.fn(),
    getProgramParameter: vi.fn(() => true),
    getProgramInfoLog: vi.fn(),
    deleteProgram: vi.fn(),
    getUniformLocation: vi.fn(() => ({})),
    createVertexArray: vi.fn(() => ({})),
    deleteVertexArray: vi.fn(),
    useProgram: vi.fn(),
    bindVertexArray: vi.fn(),
    bindFramebuffer: vi.fn(),
    viewport: vi.fn(),
    uniform3fv: vi.fn(),
    uniform1i: vi.fn(),
    uniform1f: vi.fn(),
    uniform2f: vi.fn(),
    uniform4fv: vi.fn(),
    uniform4f: vi.fn(),
    drawArrays: vi.fn(),
  };
}

let gl: ReturnType<typeof fakeGl>;
let frames: FrameRequestCallback[];
let reduced = false;

beforeEach(() => {
  gl = fakeGl();
  frames = [];
  reduced = false;
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: reduced,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn((callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    }),
  );
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    gl as unknown as WebGL2RenderingContext,
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("nebula lifecycle", () => {
  it("keeps overlay content usable when WebGL is unavailable", () => {
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue(null);
    const { container } = render(
      <HalftoneNebula>
        <button>Launch mission</button>
      </HalftoneNebula>,
    );
    expect(screen.getByRole("button", { name: "Launch mission" })).toBeTruthy();
    expect(container.querySelector("canvas")).toBeNull();
    expect(container.querySelector('[style*="radial-gradient"]')).toBeTruthy();
    expect(requestAnimationFrame).not.toHaveBeenCalled();
  });

  it("renders a static sky for reduced motion without scheduling animation", () => {
    reduced = true;
    render(<HalftoneNebula interactive={false} />);
    expect(gl.drawArrays.mock.calls.length).toBeGreaterThan(0);
    expect(requestAnimationFrame).not.toHaveBeenCalled();
  });

  it("caps animation frames and pauses/resumes with page visibility", () => {
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    render(<HalftoneNebula interactive={false} maxDpr={1} maxFps={30} />);
    const initial = gl.drawArrays.mock.calls.length;
    act(() => frames[0](0));
    act(() => frames[1](10));
    act(() => frames[2](34));
    expect(gl.drawArrays.mock.calls.length).toBe(initial + 2);
    hidden.mockReturnValue(true);
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(cancelAnimationFrame).toHaveBeenCalled();
    const scheduled = vi.mocked(requestAnimationFrame).mock.calls.length;
    hidden.mockReturnValue(false);
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(vi.mocked(requestAnimationFrame).mock.calls.length).toBe(
      scheduled + 1,
    );
  });

  it("recreates the renderer after context restoration and frees resources on unmount", () => {
    const { container, unmount } = render(<HalftoneNebula />);
    const canvas = container.querySelector("canvas")!;
    const loss = new Event("webglcontextlost", { cancelable: true });
    act(() => canvas.dispatchEvent(loss));
    expect(loss.defaultPrevented).toBe(true);
    act(() => canvas.dispatchEvent(new Event("webglcontextrestored")));
    expect(gl.createProgram).toHaveBeenCalledTimes(2);
    unmount();
    expect(gl.deleteProgram).toHaveBeenCalledTimes(2);
    expect(gl.deleteVertexArray).toHaveBeenCalledTimes(2);
  });

  it("releases the surviving shader when the other shader cannot compile", () => {
    gl.getShaderParameter.mockReturnValueOnce(true).mockReturnValueOnce(false);
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { container } = render(<HalftoneNebula />);
    expect(gl.deleteShader).toHaveBeenCalledTimes(2);
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("does not leak renderer objects across StrictMode remounts", () => {
    const { unmount } = render(
      <StrictMode>
        <HalftoneNebula />
      </StrictMode>,
    );
    unmount();
    expect(gl.createProgram.mock.calls.length).toBe(
      gl.deleteProgram.mock.calls.length,
    );
    expect(gl.createVertexArray.mock.calls.length).toBe(
      gl.deleteVertexArray.mock.calls.length,
    );
  });
});
