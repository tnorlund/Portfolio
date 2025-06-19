import {
  findHullExtentsRelativeToCentroid,
  computeReceiptBoxFromHull,
  findBoundaryLinesWithSkew,
  computeFinalReceiptTilt,
  findHullExtremesAlongAngle,
  estimateReceiptPolygonFromLines,
} from "./receipt";
import type { Line, Point } from "../types/api";

describe("receipt utilities", () => {
  const hull: Point[] = [
    { x: 0, y: 0 },
    { x: 2, y: 0 },
    { x: 2, y: 1 },
    { x: 0, y: 1 },
  ];
  const centroid: Point = { x: 1, y: 0.5 };
  const lines: Array<Line & { angle_degrees: number }> = [
    {
      top_left: { x: 0, y: 0.9 },
      top_right: { x: 1, y: 0.9 },
      bottom_left: { x: 0, y: 0.8 },
      bottom_right: { x: 1, y: 0.8 },
      angle_degrees: 0,
    } as unknown as Line & { angle_degrees: number },
    {
      top_left: { x: 1, y: 0.7 },
      top_right: { x: 2, y: 0.7 },
      bottom_left: { x: 1, y: 0.6 },
      bottom_right: { x: 2, y: 0.6 },
      angle_degrees: 0,
    } as unknown as Line & { angle_degrees: number },
    {
      top_left: { x: 2, y: 0.5 },
      top_right: { x: 3, y: 0.5 },
      bottom_left: { x: 2, y: 0.4 },
      bottom_right: { x: 3, y: 0.4 },
      angle_degrees: 0,
    } as unknown as Line & { angle_degrees: number },
    {
      top_left: { x: 3, y: 0.3 },
      top_right: { x: 4, y: 0.3 },
      bottom_left: { x: 3, y: 0.2 },
      bottom_right: { x: 4, y: 0.2 },
      angle_degrees: 0,
    } as unknown as Line & { angle_degrees: number },
  ];

  test("findHullExtentsRelativeToCentroid computes extents", () => {
    const ext = findHullExtentsRelativeToCentroid(hull, centroid);
    expect(ext.minX).toBeCloseTo(-1);
    expect(ext.maxX).toBeCloseTo(1);
    expect(ext.minY).toBeCloseTo(-0.5);
    expect(ext.maxY).toBeCloseTo(0.5);
  });

  test("computeReceiptBoxFromHull returns four points", () => {
    const box = computeReceiptBoxFromHull(hull, centroid, 0);
    expect(box).toHaveLength(4);
  });

  test("estimateReceiptPolygonFromLines computes polygon", () => {
    const poly = estimateReceiptPolygonFromLines(lines);
    expect(poly).not.toBeNull();
    if (poly) {
      expect(poly.top_left).toBeDefined();
      expect(poly.bottom_right).toBeDefined();
    }
  });

  test("findBoundaryLinesWithSkew returns edge points", () => {
    const result = findBoundaryLinesWithSkew(lines, hull, centroid, 0);
    expect(Array.isArray(result.leftEdgePoints)).toBe(true);
    expect(Array.isArray(result.rightEdgePoints)).toBe(true);
    expect(typeof result.leftBoundaryAngle).toBe("number");
    expect(typeof result.rightBoundaryAngle).toBe("number");
  });

  test("computeFinalReceiptTilt returns expected angle", () => {
    const angle = computeFinalReceiptTilt(lines as any, hull, centroid, 0);
    expect(angle).toBeCloseTo(0); // With consistent angle calculation, horizontal lines give 0°
  });

  test("findHullExtremesAlongAngle finds extremes", () => {
    const { leftPoint, rightPoint } = findHullExtremesAlongAngle(
      hull,
      centroid,
      0
    );
    expect(leftPoint.x).toBeCloseTo(0);
    expect(rightPoint.x).toBeCloseTo(2);
  });
});
