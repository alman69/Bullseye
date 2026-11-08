import { useCallback, useEffect, useRef, useState } from "react";
import { useVisionFeed } from "@/hooks/use-vision-feed";
import {
  contentRect,
  normalizedBox,
  toImagePoint,
  type VisionFrame,
  type VisionOverlayOptions,
} from "@/lib/vision";
import { drawVision } from "@/lib/vision-draw";


/** Draws what vision sees on top of the camera video, and turns drags into color samples. */
export function VisionOverlay(options: VisionOverlayOptions) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const frame = useRef<VisionFrame | null>(null);
  const drag = useRef<{ start: [number, number]; end: [number, number] } | null>(null);
  const opts = useRef(options);
  useEffect(() => {
    opts.current = options;
  });
  const [maskTick, setMaskTick] = useState(0);

  const draw = useCallback(() => {
    const el = canvas.current;
    const f = frame.current;
    const ctx = el?.getContext("2d");
    if (!el || !f || !ctx) return;
    if (el.width !== f.w || el.height !== f.h) {
      el.width = f.w;
      el.height = f.h;
    }
    drawVision(ctx, f, opts.current, drag.current);
  }, []);

  useVisionFeed((f) => {
    frame.current = f;
    requestAnimationFrame(draw);
  });
  useEffect(() => {
    requestAnimationFrame(draw);
  }, [options.show, options.floor, options.samplingClass, draw]);
  useEffect(() => {
    if (options.mask === "off") return;
    const timer = setInterval(() => setMaskTick((t) => t + 1), 600);
    return () => clearInterval(timer);
  }, [options.mask]);

  const point = (event: React.PointerEvent<HTMLCanvasElement>): [number, number] | null => {
    const f = frame.current;
    const el = canvas.current;
    if (!f || !el) return null;
    const bounds = el.getBoundingClientRect();
    const rect = contentRect(bounds.width, bounds.height, f.w, f.h);
    return toImagePoint(event.clientX - bounds.left, event.clientY - bounds.top, rect);
  };
  const sampling = !!options.samplingClass;
  return (
    <>
      {options.mask !== "off" && (
        <img
          className="vision-mask"
          alt={`${options.mask} mask preview`}
          src={`/api/vision/mask.jpg?kind=${options.mask}&t=${maskTick}`}
        />
      )}
      <canvas
        ref={canvas}
        className={`vision-overlay ${sampling ? "sampling" : ""}`}
        aria-label={sampling ? `Drag on the video to sample ${options.samplingClass}` : "Vision overlay"}
        onPointerDown={(event) => {
          if (!sampling) return;
          const p = point(event);
          if (!p) return;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          drag.current = { start: p, end: p };
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          const p = point(event);
          if (p) drag.current.end = p;
          requestAnimationFrame(draw);
        }}
        onPointerUp={() => {
          const d = drag.current;
          drag.current = null;
          requestAnimationFrame(draw);
          if (!d) return;
          const box = normalizedBox(d.start, d.end);
          if (box[2] - box[0] > 0.004 && box[3] - box[1] > 0.004) opts.current.onSampleBox?.(box);
        }}
      />
    </>
  );
}
