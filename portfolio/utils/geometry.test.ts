import {
  computeHullCentroid,
  theilSen,
  computeHullEdge,
  computeEdge,
  computeReceiptBoxFromLineEdges,
  Point,
} from "./geometry";

describe("geometry utilities", () => {
  test("computeHullCentroid of unit square", () => {
    const hull: Point[] = [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
      { x: 0, y: 1 },
    ];
    const c = computeHullCentroid(hull);
    expect(c.x).toBeCloseTo(0.5);
    expect(c.y).toBeCloseTo(0.5);
  });

  test("theilSen on diagonal", () => {
    const pts: Point[] = [
      { x: 0, y: 0 },
      { x: 1, y: 1 },
      { x: 2, y: 2 },
    ];
    const ts = theilSen(pts);
    expect(ts.slope).toBeCloseTo(1);
    expect(ts.intercept).toBeCloseTo(0);
  });

  test("computeHullEdge right side", () => {
    const hull: Point[] = [
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      { x: 2, y: 1 },
      { x: 0, y: 1 },
    ];
    const edge = computeHullEdge(hull, 4, "right");
    expect(edge).not.toBeNull();
    if (edge) {
      expect(edge.top.x).toBeCloseTo(2);
      expect(edge.bottom.x).toBeCloseTo(2);
    }
  });

  test("computeEdge aggregates lines", () => {
    const lines = [
      {
        top_left: { x: 0, y: 0.9 },
        top_right: { x: 1, y: 0.9 },
        bottom_left: { x: 0, y: 0.8 },
        bottom_right: { x: 1, y: 0.8 },
      },
      {
        top_left: { x: 1, y: 0.7 },
        top_right: { x: 2, y: 0.7 },
        bottom_left: { x: 1, y: 0.6 },
        bottom_right: { x: 2, y: 0.6 },
      },
      {
        top_left: { x: 2, y: 0.5 },
        top_right: { x: 3, y: 0.5 },
        bottom_left: { x: 2, y: 0.4 },
        bottom_right: { x: 3, y: 0.4 },
      },
      {
        top_left: { x: 3, y: 0.3 },
        top_right: { x: 4, y: 0.3 },
        bottom_left: { x: 3, y: 0.2 },
        bottom_right: { x: 4, y: 0.2 },
      },
    ];
    const edge = computeEdge(lines as any, "right");
    expect(edge).not.toBeNull();
  });

  test("computeReceiptBoxFromLineEdges returns four points", () => {
    const lines = [
      {
        top_left: { x: 0, y: 1 },
        top_right: { x: 1, y: 1 },
        bottom_left: { x: 0, y: 0 },
        bottom_right: { x: 1, y: 0 },
      },
      {
        top_left: { x: 1, y: 1 },
        top_right: { x: 2, y: 1 },
        bottom_left: { x: 1, y: 0 },
        bottom_right: { x: 2, y: 0 },
      },
    ];
    const hull: Point[] = [
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      { x: 2, y: 1 },
      { x: 0, y: 1 },
    ];
    const centroid: Point = { x: 1.5, y: 0.5 };
    const box = computeReceiptBoxFromLineEdges(lines as any, hull, centroid, 0);
    expect(box).toHaveLength(4);
  });
});
