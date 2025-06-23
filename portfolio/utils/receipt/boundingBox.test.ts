import {
  createBoundaryLineFromTheilSen,
  createBoundaryLineFromPoints,
  computeReceiptBoxFromBoundaries,
} from "./boundingBox";
import type { Point } from "../../types/api";

describe("boundary line helpers", () => {
  test("createBoundaryLineFromTheilSen returns inverted form", () => {
    const line = createBoundaryLineFromTheilSen({ slope: 2, intercept: 0.1 });
    expect(line.isVertical).toBe(false);
    expect(line.isInverted).toBe(true);
    expect(line.slope).toBeCloseTo(2);
    expect(line.intercept).toBeCloseTo(0.1);
  });

  test("computeReceiptBoxFromBoundaries handles inverted lines", () => {
    const top = createBoundaryLineFromTheilSen({ slope: 1, intercept: 0 });
    const bottom = createBoundaryLineFromTheilSen({
      slope: 1,
      intercept: 0.5,
    });
    const left = createBoundaryLineFromPoints({ x: 0, y: 0 }, { x: 0, y: 1 });
    const right = createBoundaryLineFromPoints({ x: 1, y: 0 }, { x: 1, y: 1 });
    const centroid: Point = { x: 0.5, y: 0.5 };

    const box = computeReceiptBoxFromBoundaries(
      top,
      bottom,
      left,
      right,
      centroid
    );

    expect(box).toHaveLength(4);
    expect(box[0].x).toBeCloseTo(0);
    expect(box[0].y).toBeCloseTo(0);
    expect(box[1].x).toBeCloseTo(1);
    expect(box[1].y).toBeCloseTo(1);
    expect(box[2].x).toBeCloseTo(1);
    expect(box[2].y).toBeCloseTo(0.5);
    expect(box[3].x).toBeCloseTo(0);
    expect(box[3].y).toBeCloseTo(-0.5);
  });
});
