import { CLASS_HEX, MASK_TOOL, normalizedBox, type VisionFrame, type VisionOverlayOptions } from "@/lib/vision";

const ARROWS: Record<string, [number, number]> = {
  forward: [0, -1],
  backward: [0, 1],
  turn_left: [-1, 0],
  turn_right: [1, 0],
};

/** Draw one vision result in frame pixel coordinates (the canvas is sized to the frame). */
export function drawVision(
  ctx: CanvasRenderingContext2D,
  f: VisionFrame,
  o: VisionOverlayOptions,
  drag: { start: [number, number]; end: [number, number] } | null,
) {
  const { w, h } = f;
  ctx.clearRect(0, 0, w, h);
  const font = Math.max(12, Math.round(w / 48));
  ctx.font = `600 ${font}px Geist Variable, sans-serif`;
  ctx.lineJoin = "round";
  const label = (text: string, x: number, y: number, color: string) => {
    ctx.lineWidth = 4;
    ctx.strokeStyle = "rgba(0,0,0,0.75)";
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  };
  if (o.show) {
    // Center line and CENTER band.
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255,255,255,0.65)";
    ctx.beginPath();
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(w / 2, h);
    ctx.stroke();
    ctx.setLineDash([6, 6]);
    ctx.strokeStyle = "rgba(200,200,200,0.45)";
    for (const x of [w / 2 - (f.deadband * w) / 2, w / 2 + (f.deadband * w) / 2]) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // Floor strips: how far up the white floor reaches in each direction.
    if (o.floor && f.free) {
      const n = f.free.heights.length;
      const top = f.free.roi_top * h;
      f.free.heights.forEach((height, i) => {
        const x0 = (i * w) / n;
        const y = h - height * (h - top);
        ctx.fillStyle = f.free!.blocked[i] ? "rgba(235,70,70,0.28)" : "rgba(80,210,120,0.22)";
        ctx.fillRect(x0 + 2, y, w / n - 4, h - y);
        if (f.free!.corridor.includes(i)) {
          ctx.strokeStyle = "rgba(255,255,255,0.35)";
          ctx.strokeRect(x0 + 1, top, w / n - 2, h - top - 1);
        }
      });
      const thr = h - f.free.blocked_height * (h - top);
      ctx.setLineDash([3, 5]);
      ctx.strokeStyle = "rgba(235,70,70,0.6)";
      ctx.beginPath();
      ctx.moveTo(0, thr);
      ctx.lineTo(w, thr);
      ctx.stroke();
      ctx.setLineDash([]);
      if (!f.free.floor_visible) label("NO FLOOR VISIBLE", 12, top - 8, "#ff8080");
    }

    // Rover mask: parts of the rover itself (gripper) that vision ignores.
    for (const [x0, y0, x1, y1] of f.mask_boxes ?? []) {
      ctx.fillStyle = "rgba(20,20,30,0.45)";
      ctx.fillRect(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h);
      ctx.strokeStyle = "rgba(255,255,255,0.55)";
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1.5;
      ctx.strokeRect(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h);
      ctx.setLineDash([]);
      label("ignored", x0 * w + 6, y0 * h + font, "rgba(255,255,255,0.8)");
    }

    for (const polygon of f.mask_polygons ?? []) {
      ctx.beginPath();
      polygon.forEach(([x, y], i) => i ? ctx.lineTo(x * w, y * h) : ctx.moveTo(x * w, y * h));
      ctx.closePath(); ctx.fillStyle = "rgba(20,20,30,0.55)"; ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.8)"; ctx.lineWidth = 1.5; ctx.stroke();
    }
    for (const [name, material] of Object.entries(f.materials ?? {})) {
      if (name === f.target) continue;
      ctx.strokeStyle = CLASS_HEX[name] ?? "#fff"; ctx.lineWidth = 2;
      for (const [x, y, bw, bh] of material.boxes) {
        ctx.strokeRect(x * w, y * h, bw * w, bh * h);
        label(name === "cardboard" ? "WALL" : name === "home" ? "HOME?" : name.toUpperCase(),
          x * w + 3, Math.max(font, y * h - 5), CLASS_HEX[name] ?? "#fff");
      }
    }

    // Ground lines from the stop-line calibration.
    const lines: [number | null, string, string, number][] = [
      [f.lines.horizon, "horizon", "rgba(180,180,180,0.8)", 1],
      [f.lines.zone, "approach zone", "rgba(200,140,255,0.85)", 1],
      [f.lines.stop, "STOP LINE", "#ff4fd8", 3],
    ];
    for (const [y, text, color, width] of lines) {
      if (y == null || y <= 0 || y >= 1) continue;
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.moveTo(0, y * h);
      ctx.lineTo(w, y * h);
      ctx.stroke();
      label(text, w - ctx.measureText(text).width - 10, y * h - 6, color);
    }

    // Other regions of the target color (thin gray), then the chosen target.
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(210,210,210,0.8)";
    for (const [x, y, bw, bh] of f.others) ctx.strokeRect(x * w, y * h, bw * w, bh * h);
    const color = CLASS_HEX[f.target] ?? "#ffffff";
    if (f.box && f.center) {
      const [x, y, bw, bh] = f.box;
      ctx.lineWidth = 3;
      ctx.strokeStyle = color;
      ctx.strokeRect(x * w, y * h, bw * w, bh * h);
      const [cx, cy] = [f.center[0] * w, f.center[1] * h];
      ctx.strokeStyle = "#ffe14d";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(w / 2, cy);
      ctx.lineTo(cx, cy);
      ctx.stroke();
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, Math.PI * 2);
      ctx.fill();
      const dist = f.detection.distance_cm_est;
      label(`${f.target.toUpperCase()}${dist != null ? ` ~${Math.round(dist)} cm` : ""}`, x * w, Math.max(font, y * h - 8), color);
    }

    // Planner DRY RUN: what it would do next.
    if (f.nav.label) {
      const text = `DRY RUN · ${f.nav.state} → ${f.nav.label}`;
      const tw = ctx.measureText(text).width;
      const by = h - font * 2.2;
      ctx.fillStyle = "rgba(6,16,36,0.72)";
      ctx.fillRect(w / 2 - tw / 2 - 12, by - font * 1.15, tw + 24, font * 1.7);
      label(text, w / 2 - tw / 2, by, "#ffe14d");
      const arrow = f.nav.action ? ARROWS[f.nav.action.kind] : undefined;
      if (arrow) {
        const [ax, ay] = [w / 2, by - font * 3];
        ctx.strokeStyle = "#ffe14d";
        ctx.fillStyle = "#ffe14d";
        ctx.lineWidth = 5;
        const [ex, ey] = [ax + arrow[0] * 55, ay + arrow[1] * 40];
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(ex, ey);
        ctx.stroke();
        const angle = Math.atan2(ey - ay, ex - ax);
        ctx.beginPath();
        ctx.moveTo(ex + Math.cos(angle) * 10, ey + Math.sin(angle) * 10);
        ctx.lineTo(ex + Math.cos(angle + 2.5) * 14, ey + Math.sin(angle + 2.5) * 14);
        ctx.lineTo(ex + Math.cos(angle - 2.5) * 14, ey + Math.sin(angle - 2.5) * 14);
        ctx.fill();
      }
    }
  }
  if (drag && o.samplingClass) {
    const box = normalizedBox(drag.start, drag.end);
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 2;
    ctx.strokeStyle = o.samplingClass === MASK_TOOL ? "#ffffff" : CLASS_HEX[o.samplingClass] ?? "#ffffff";
    ctx.strokeRect(box[0] * w, box[1] * h, (box[2] - box[0]) * w, (box[3] - box[1]) * h);
    ctx.setLineDash([]);
  }
}
