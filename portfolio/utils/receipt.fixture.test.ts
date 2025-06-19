import fixtureData from "../tests/fixtures/target_receipt.json";
import {
  convexHull,
  computeHullCentroid,
  findLineEdgesAtSecondaryExtremes,
  computeReceiptBoxFromLineEdges,
  theilSen,
} from "./geometry";
import {
  computeFinalReceiptTilt,
  findHullExtremesAlongAngle,
} from "./receipt/boundingBox";

describe("bounding box algorithm with fixture", () => {
  const lines = fixtureData.lines;
  const allCorners: { x: number; y: number }[] = [];
  lines.forEach((line) => {
    allCorners.push(
      { x: line.top_left.x, y: line.top_left.y },
      { x: line.top_right.x, y: line.top_right.y },
      { x: line.bottom_right.x, y: line.bottom_right.y },
      { x: line.bottom_left.x, y: line.bottom_left.y }
    );
  });

  const hull = convexHull([...allCorners]);
  const centroid = computeHullCentroid(hull);
  const avgAngle =
    lines.reduce((s: number, l: any) => s + l.angle_degrees, 0) / lines.length;
  const finalAngle = computeFinalReceiptTilt(
    lines as any,
    hull,
    centroid,
    avgAngle
  );
  const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
    lines as any,
    hull,
    centroid,
    avgAngle
  );
  const extremes = findHullExtremesAlongAngle(hull, centroid, finalAngle);
  const receiptBox = computeReceiptBoxFromLineEdges(
    lines as any,
    hull,
    centroid,
    avgAngle
  );

  test("collects expected number of corners", () => {
    expect(allCorners).toHaveLength(168);
  });

  test("computes expected hull size", () => {
    expect(hull).toHaveLength(13);
  });

  test("computes expected centroid", () => {
    expect(centroid.x).toBeCloseTo(0.57128, 5);
    expect(centroid.y).toBeCloseTo(0.47067, 5);
  });

  // The top edge should be using indices 10 and 9
  // The bottom edge should be using indices 3 and 4

  test("finds top and bottom edge points", () => {
    expect(topEdge.length).toBe(2);
    expect(bottomEdge.length).toBe(2);
  });

  test("boundary lines intersect expected hull points", () => {
    // Expected hull point indices for top and bottom boundaries
    // Based on the debug output, the algorithm correctly returns:
    // Top Edge: Hull[10] and Hull[9]
    // Bottom Edge: Hull[4] and Hull[3]
    const expectedTopHullIndices = [10, 9];
    const expectedBottomHullIndices = [4, 3];

    // Verify that the correct hull points are being used as input to the line fitting
    expect(topEdge.length).toBe(2);
    expect(bottomEdge.length).toBe(2);

    // Check that the returned edge points match the expected hull points
    const actualTopHullIndices = topEdge.map((edgePoint) =>
      hull.findIndex(
        (hullPoint) =>
          Math.abs(hullPoint.x - edgePoint.x) < 1e-10 &&
          Math.abs(hullPoint.y - edgePoint.y) < 1e-10
      )
    );

    const actualBottomHullIndices = bottomEdge.map((edgePoint) =>
      hull.findIndex(
        (hullPoint) =>
          Math.abs(hullPoint.x - edgePoint.x) < 1e-10 &&
          Math.abs(hullPoint.y - edgePoint.y) < 1e-10
      )
    );

    expect(actualTopHullIndices.sort()).toEqual(expectedTopHullIndices.sort());
    expect(actualBottomHullIndices.sort()).toEqual(
      expectedBottomHullIndices.sort()
    );
  });

  test("computes expected final angle", () => {
    expect(finalAngle).toBeCloseTo(90.39928, 4);
  });

  test("finds hull extremes along final angle", () => {
    expect(extremes.leftPoint.x).toBeCloseTo(0.41501, 4);
    expect(extremes.leftPoint.y).toBeCloseTo(0.14777, 4);
    expect(extremes.rightPoint.x).toBeCloseTo(0.43251, 4);
    expect(extremes.rightPoint.y).toBeCloseTo(0.80885, 4);
  });

  test("computes receipt box from line edges", () => {
    expect(receiptBox).toHaveLength(4);
    expect(receiptBox[0].x).toBeCloseTo(0.38272, 4);
    expect(receiptBox[0].y).toBeCloseTo(0.81052, 4);
    expect(receiptBox[1].x).toBeCloseTo(0.72003, 4);
    expect(receiptBox[1].y).toBeCloseTo(0.79921, 4);
    expect(receiptBox[2].x).toBeCloseTo(0.78349, 4);
    expect(receiptBox[2].y).toBeCloseTo(0.16526, 4);
    expect(receiptBox[3].x).toBeCloseTo(0.39785, 4);
    expect(receiptBox[3].y).toBeCloseTo(0.14696, 4);
  });
});
