import { describe, expect, it } from "vitest";
import { contentRect, normalizedBox, toImagePoint } from "@/lib/vision";

describe("vision overlay geometry", () => {
  it("letterboxes a 4:3 frame inside a 16:9 stage like object-fit: contain", () => {
    const rect = contentRect(1600, 900, 800, 600);
    expect(rect.height).toBe(900);
    expect(rect.width).toBe(1200);
    expect(rect.x).toBe(200);
    expect(rect.y).toBe(0);
  });

  it("maps pointer positions to normalized image coordinates and clamps the letterbox", () => {
    const rect = contentRect(1600, 900, 800, 600);
    expect(toImagePoint(800, 450, rect)).toEqual([0.5, 0.5]);
    expect(toImagePoint(200, 0, rect)).toEqual([0, 0]);
    expect(toImagePoint(50, 950, rect)).toEqual([0, 1]);
  });

  it("orders drag corners into a box", () => {
    expect(normalizedBox([0.6, 0.7], [0.2, 0.1])).toEqual([0.2, 0.1, 0.6, 0.7]);
  });
});
