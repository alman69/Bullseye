import { useCallback, useEffect, useRef, useState } from "react";
import { useVisionFeed } from "@/hooks/use-vision-feed";
import {
  MASK_TOOL,
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
  const outline = useRef<[number, number][]>([]);
  const opts = useRef(options);
  useEffect(() => {
    opts.current = options;
  });
  const [maskTick, setMaskTick] = useState(0);

  const draw = useCallback(() => {
    const el = canvas.current;
    const frozen = opts.current.frozen?.frame;
    const f = frozen ? { ...frozen, mask_boxes: frame.current?.mask_boxes ?? frozen.mask_boxes,
      mask_polygons: frame.current?.mask_polygons ?? frozen.mask_polygons } : frame.current;
    const ctx = el?.getContext("2d");
    if (!el || !f || !ctx) return;
    if (el.width !== f.w || el.height !== f.h) {
      el.width = f.w;
      el.height = f.h;
    }
    drawVision(ctx, f, opts.current, opts.current.samplingClass === MASK_TOOL ? null : drag.current);
    if (outline.current.length) {
      ctx.beginPath();
      outline.current.forEach(([x, y], i) => i ? ctx.lineTo(x * f.w, y * f.h) : ctx.moveTo(x * f.w, y * f.h));
      ctx.closePath(); ctx.fillStyle = "rgba(255,225,77,0.22)"; ctx.fill();
      ctx.strokeStyle = "#ffe14d"; ctx.lineWidth = 3; ctx.stroke();
    }

  }, []);

  useVisionFeed((f) => {
    frame.current = f;
    requestAnimationFrame(draw);
  });
  useEffect(() => {
    requestAnimationFrame(draw);
  }, [options.show, options.floor, options.samplingClass, options.frozen, draw]);
  useEffect(() => { if (options.frozen) canvas.current?.scrollIntoView?.({ block: "center", behavior: "smooth" }); }, [options.frozen]);
  useEffect(() => { drag.current = null; outline.current = []; requestAnimationFrame(draw); }, [options.samplingClass, options.frozen, draw]);
  useEffect(() => {
    if (options.mask === "off") return;
    const timer = setInterval(() => setMaskTick((t) => t + 1), 600);
    return () => clearInterval(timer);
  }, [options.mask]);

  const point = (event: React.PointerEvent<HTMLCanvasElement>): [number, number] | null => {
    const f = options.frozen?.frame ?? frame.current;
    const el = canvas.current;
    if (!f || !el) return null;
    const bounds = el.getBoundingClientRect();
    const rect = contentRect(bounds.width, bounds.height, f.w, f.h);
    if (event.clientX - bounds.left < rect.x || event.clientX - bounds.left > rect.x + rect.width ||
        event.clientY - bounds.top < rect.y || event.clientY - bounds.top > rect.y + rect.height) return null;
    return toImagePoint(event.clientX - bounds.left, event.clientY - bounds.top, rect);
  };
  const sampling = !!options.samplingClass;
  return (
    <>
      {options.frozen && <img className="vision-frozen" src={options.frozen.image} alt="Frozen calibration frame" />}
      {!options.frozen && options.mask !== "off" && (
        <img
          className="vision-mask"
          alt={`${options.mask} mask preview`}
          src={`/api/vision/mask.jpg?kind=${options.mask}&t=${maskTick}`}
        />
      )}
      <canvas
        ref={canvas}
        className={`vision-overlay ${sampling ? "sampling" : ""}`}
        aria-label={sampling ? options.samplingClass === MASK_TOOL ? "Trace rover outline on the frozen video" : `Click or drag to sample ${options.samplingClass}` : "Vision overlay"}
        onPointerDown={(event) => {
          if (!sampling) return;
          const p = point(event);
          if (!p) return;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          drag.current = { start: p, end: p };
          outline.current = options.samplingClass === MASK_TOOL ? [p] : [];
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          const p = point(event);
          if (p) {
            drag.current.end = p;
            const prev = outline.current.at(-1);
            if (options.samplingClass === MASK_TOOL && prev && Math.hypot(p[0] - prev[0], p[1] - prev[1]) > 0.006) {
              outline.current.push(p);
              if (outline.current.length > 120) outline.current = outline.current.filter((_, i) => i % 2 === 0);
            }
          }
          requestAnimationFrame(draw);
        }}
        onPointerCancel={() => { drag.current = null; outline.current = []; requestAnimationFrame(draw); }}
        onLostPointerCapture={() => { drag.current = null; outline.current = []; requestAnimationFrame(draw); }}
        onPointerUp={() => {
          const d = drag.current;
          drag.current = null;
          requestAnimationFrame(draw);
          if (!d) return;
          if (opts.current.samplingClass === MASK_TOOL) {
            const points = outline.current; outline.current = [];
            if (points.length >= 3) opts.current.onMaskPolygon?.(points);
            return;
          }
          const box = normalizedBox(d.start, d.end);
          if (Math.hypot(d.end[0] - d.start[0], d.end[1] - d.start[1]) < 0.008) {
            const f = opts.current.frozen?.frame ?? frame.current;
            if (!f) return;
            const rx = 5 / f.w, ry = 5 / f.h;
            opts.current.onSampleBox?.([Math.max(0, d.end[0] - rx), Math.max(0, d.end[1] - ry),
              Math.min(1, d.end[0] + rx), Math.min(1, d.end[1] + ry)]);
          } else if (box[2] - box[0] > 0.004 && box[3] - box[1] > 0.004) opts.current.onSampleBox?.(box);
        }}
      />
    </>
  );
}

/** Outside the image so no controls obscure the claws being traced. */
export function VisionFreezeControls(options: VisionOverlayOptions) {
  if (!options.frozen) return null;
  return <div className="vision-freeze-controls"><span>FROZEN · {options.samplingClass === MASK_TOOL ? "trace rover outline" : options.samplingClass ? `sample ${options.samplingClass}` : "calibration image"}</span>
    <button type="button" onClick={options.onResumeLive}>Back to live video</button></div>;
}
