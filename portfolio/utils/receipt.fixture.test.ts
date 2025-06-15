import fixtureData from "../tests/fixtures/target_receipt.json";
import { convexHull, computeHullCentroid } from "./geometry";
import { computeFinalReceiptTilt } from "./receipt";

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
  const avgAngle = lines.reduce((s: number, l: any) => s + l.angle_degrees, 0) / lines.length;
  const finalAngle = computeFinalReceiptTilt(lines as any, hull, centroid, avgAngle);

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
});
