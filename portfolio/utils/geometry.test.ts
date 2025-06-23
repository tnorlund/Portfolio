import {
  computeHullCentroid,
  theilSen,
  computeHullEdge,
  computeEdge,
  computeReceiptBoxFromLineEdges,
  convexHull,
  computeReceiptBoxFromSkewedExtents,
  findLineEdgesAtSecondaryExtremes,
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

  describe("convexHull", () => {
    test("handles empty array", () => {
      const result = convexHull([]);
      expect(result).toEqual([]);
    });

    test("handles single point", () => {
      const points = [{ x: 1, y: 1 }];
      const result = convexHull(points);
      expect(result).toEqual([{ x: 1, y: 1 }]);
    });

    test("handles two points", () => {
      const points = [
        { x: 0, y: 0 },
        { x: 1, y: 1 },
      ];
      const result = convexHull(points);
      expect(result).toEqual([
        { x: 0, y: 0 },
        { x: 1, y: 1 },
      ]);
    });

    test("computes hull for triangle", () => {
      const points = [
        { x: 0, y: 0 },
        { x: 2, y: 0 },
        { x: 1, y: 2 },
      ];
      const result = convexHull(points);
      expect(result).toHaveLength(3);
      expect(result).toContainEqual({ x: 0, y: 0 });
      expect(result).toContainEqual({ x: 2, y: 0 });
      expect(result).toContainEqual({ x: 1, y: 2 });
    });

    test("removes interior points", () => {
      const points = [
        { x: 0, y: 0 },
        { x: 2, y: 0 },
        { x: 2, y: 2 },
        { x: 0, y: 2 },
        { x: 1, y: 1 }, // interior point
      ];
      const result = convexHull(points);
      expect(result).toHaveLength(4);
      expect(result).not.toContainEqual({ x: 1, y: 1 });
    });

    test("handles duplicate points", () => {
      const points = [
        { x: 0, y: 0 },
        { x: 0, y: 0 }, // duplicate
        { x: 1, y: 0 },
        { x: 1, y: 1 },
      ];
      const result = convexHull(points);
      expect(result).toHaveLength(3);
    });
  });

  describe("computeReceiptBoxFromSkewedExtents", () => {
    test("returns null for empty hull", () => {
      const result = computeReceiptBoxFromSkewedExtents([], 0, 0, 0);
      expect(result).toBeNull();
    });

    test("computes box for unit square", () => {
      const hull = [
        { x: 0, y: 0 },
        { x: 1, y: 0 },
        { x: 1, y: 1 },
        { x: 0, y: 1 },
      ];
      const result = computeReceiptBoxFromSkewedExtents(hull, 0.5, 0.5, 0);
      expect(result).toHaveLength(4);
      expect(result).not.toBeNull();
    });

    test("handles rotation", () => {
      const hull = [
        { x: 0, y: 0 },
        { x: 1, y: 0 },
        { x: 1, y: 1 },
        { x: 0, y: 1 },
      ];
      const result = computeReceiptBoxFromSkewedExtents(hull, 0.5, 0.5, 45);
      expect(result).toHaveLength(4);
      expect(result).not.toBeNull();
    });
  });

  describe("findLineEdgesAtSecondaryExtremes", () => {
    const mockLines = [
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
    const mockHull = [
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      { x: 2, y: 1 },
      { x: 0, y: 1 },
    ];
    const mockCentroid = { x: 1, y: 0.5 };

    test("returns edge points", () => {
      const result = findLineEdgesAtSecondaryExtremes(
        mockLines as any,
        mockHull,
        mockCentroid,
        0
      );
      expect(result).toHaveProperty("topEdge");
      expect(result).toHaveProperty("bottomEdge");
      expect(Array.isArray(result.topEdge)).toBe(true);
      expect(Array.isArray(result.bottomEdge)).toBe(true);
    });

    test("handles empty lines", () => {
      const result = findLineEdgesAtSecondaryExtremes(
        [],
        mockHull,
        mockCentroid,
        0
      );
      expect(result.topEdge).toHaveLength(2); // uses 2 hull points
      expect(result.bottomEdge).toHaveLength(2); // uses 2 hull points
    });
  });

  describe("Edge cases and error handling", () => {
    test("computeHullCentroid handles empty hull", () => {
      const result = computeHullCentroid([]);
      expect(result).toEqual({ x: 0, y: 0 });
    });

    test("computeHullCentroid handles single point", () => {
      const result = computeHullCentroid([{ x: 5, y: 3 }]);
      expect(result).toEqual({ x: 5, y: 3 });
    });

    test("computeHullCentroid handles two points", () => {
      const result = computeHullCentroid([
        { x: 0, y: 0 },
        { x: 2, y: 2 },
      ]);
      expect(result).toEqual({ x: 1, y: 1 });
    });

    test("theilSen handles single point", () => {
      const result = theilSen([{ x: 1, y: 2 }]);
      expect(result.slope).toBe(0);
      expect(result.intercept).toBe(2);
    });

    test("theilSen handles horizontal line", () => {
      const points = [
        { x: 0, y: 1 },
        { x: 1, y: 1 },
        { x: 2, y: 1 },
      ];
      const result = theilSen(points);
      expect(result.slope).toBe(0);
      expect(result.intercept).toBe(1);
    });

    test("computeHullEdge returns null for insufficient points", () => {
      const hull = [
        { x: 0, y: 0 },
        { x: 1, y: 1 },
      ];
      const result = computeHullEdge(hull, 4, "left");
      expect(result).toBeNull();
    });

    test("computeEdge returns null for insufficient lines", () => {
      const lines = [
        {
          top_left: { x: 0, y: 0 },
          top_right: { x: 1, y: 0 },
          bottom_left: { x: 0, y: 1 },
          bottom_right: { x: 1, y: 1 },
        },
      ];
      const result = computeEdge(lines as any, "left", 6);
      expect(result).toBeNull();
    });

    test("computeReceiptBoxFromLineEdges handles empty lines", () => {
      const result = computeReceiptBoxFromLineEdges([], [], { x: 0, y: 0 }, 0);
      expect(result).toEqual([]);
    });
  });
});
