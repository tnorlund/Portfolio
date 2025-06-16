import fixtureData from "../tests/fixtures/target_receipt.json";
import {
  convexHull,
  computeHullCentroid,
  findLineEdgesAtSecondaryExtremes,
  computeReceiptBoxFromLineEdges,
} from "./geometry";
import {
  computeFinalReceiptTilt,
  findHullExtremesAlongAngle,
} from "./receipt";

describe("bounding box algorithm with fixture", () => {
  const lines = fixtureData.lines;
  const allCorners: { x: number; y: number }[] = [];
  lines.forEach(line => {
    allCorners.push(
      { x: line.top_left.x, y: line.top_left.y },
      { x: line.top_right.x, y: line.top_right.y },
      { x: line.bottom_right.x, y: line.bottom_right.y },
      { x: line.bottom_left.x, y: line.bottom_left.y },
    );
  });

  const hull = convexHull([...allCorners]);
  const centroid = computeHullCentroid(hull);
  const avgAngle = lines.reduce(
    (s: number, l: any) => s + l.angle_degrees,
    0,
  ) / lines.length;
  const finalAngle = computeFinalReceiptTilt(
    lines as any,
    hull,
    centroid,
    avgAngle,
  );
  const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
    lines as any,
    hull,
    centroid,
    avgAngle,
  );
  const extremes = findHullExtremesAlongAngle(hull, centroid, finalAngle);
  const receiptBox = computeReceiptBoxFromLineEdges(
    lines as any,
    hull,
    centroid,
    avgAngle,
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

  test("computes expected final angle", () => {
    expect(finalAngle).toBeCloseTo(78.2377, 4);
  });

  test("finds top and bottom edge points", () => {
    expect(topEdge.length).toBe(12);
    expect(bottomEdge.length).toBe(10);
  });

  test("finds hull extremes along final angle", () => {
    expect(extremes.leftPoint.x).toBeCloseTo(0.41501, 4);
    expect(extremes.leftPoint.y).toBeCloseTo(0.14777, 4);
    expect(extremes.rightPoint.x).toBeCloseTo(0.67054, 4);
    expect(extremes.rightPoint.y).toBeCloseTo(0.80087, 4);
  });

  test("computes receipt box from line edges", () => {
    expect(receiptBox).toHaveLength(4);
    expect(receiptBox[0].x).toBeCloseTo(0.40615, 4);
    expect(receiptBox[0].y).toBeCloseTo(-0.21697, 4);
    expect(receiptBox[1].x).toBeCloseTo(0.6277, 4);
    expect(receiptBox[1].y).toBeCloseTo(1.7216, 4);
  });
});
