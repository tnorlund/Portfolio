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
  consistentAngleFromPoints,
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

  test("computes final receipt tilt using line fitting (Step 6)", () => {
    // Step 6 should fit lines through the top and bottom edges and average their angles
    // NEW: Using consistent angle calculation that always measures left-to-right

    // First, let's manually extract the angles computed from top and bottom edges
    const angleFromPoints = (
      pts: { x: number; y: number }[]
    ): number | null => {
      if (pts.length < 2) return null;
      const { slope } = theilSen(pts);
      return (Math.atan2(1, slope) * 180) / Math.PI;
    };

    const topAngle = angleFromPoints(topEdge);
    const bottomAngle = angleFromPoints(bottomEdge);

    // Both edges are now measured consistently using the corrected angle calculation

    // Verify that both top and bottom edges produced valid angles
    expect(topAngle).not.toBeNull();
    expect(bottomAngle).not.toBeNull();

    // Calculate the expected final angle using the new consistent angle calculation
    const expectedFinalAngle =
      (consistentAngleFromPoints(topEdge)! +
        consistentAngleFromPoints(bottomEdge)!) /
      2;

    // The computed final angle should match our manual calculation
    expect(finalAngle).toBeCloseTo(expectedFinalAngle, 10);

    // With the new consistent angle calculation, we avoid the parallel line averaging issue
    // Both edges are now measured in a consistent direction (left-to-right)

    // Verify the individual angles match the expected values from the OLD approach:
    // Top edge is nearly horizontal sloping slightly down-left (~178°)
    // Bottom edge is nearly horizontal sloping slightly up-right (~2.7°)
    expect(topAngle).toBeCloseTo(178.08087, 4);
    expect(bottomAngle).toBeCloseTo(2.7177, 4);

    // Verify that the final angle is indeed the average of the consistent edge angles
    expect(Math.abs(finalAngle - expectedFinalAngle)).toBeLessThan(1e-10);
  });

  test("left and right extremes correspond to expected hull points", () => {
    // Find the hull indices that correspond to the left and right extreme points
    const leftHullIndex = hull.findIndex(
      (hullPoint) =>
        Math.abs(hullPoint.x - extremes.leftPoint.x) < 1e-10 &&
        Math.abs(hullPoint.y - extremes.leftPoint.y) < 1e-10
    );

    const rightHullIndex = hull.findIndex(
      (hullPoint) =>
        Math.abs(hullPoint.x - extremes.rightPoint.x) < 1e-10 &&
        Math.abs(hullPoint.y - extremes.rightPoint.y) < 1e-10
    );

    // Based on the fixture data and algorithm, these should be the expected indices
    // Left extreme should correspond to hull index 1
    // Right extreme should correspond to hull index 6
    const expectedLeftHullIndex = 1;
    const expectedRightHullIndex = 6;

    expect(leftHullIndex).toBe(expectedLeftHullIndex);
    expect(rightHullIndex).toBe(expectedRightHullIndex);

    // Verify the coordinates match what we expect from the hull points
    expect(hull[leftHullIndex].x).toBeCloseTo(extremes.leftPoint.x, 10);
    expect(hull[leftHullIndex].y).toBeCloseTo(extremes.leftPoint.y, 10);
    expect(hull[rightHullIndex].x).toBeCloseTo(extremes.rightPoint.x, 10);
    expect(hull[rightHullIndex].y).toBeCloseTo(extremes.rightPoint.y, 10);
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

  test("visualizes hull points in rotated coordinate system", () => {
    // Show all hull points in the rotated coordinate system where the receipt is "straightened"
    console.log("=== HULL POINTS IN ROTATED SPACE ===");
    console.log("Final angle (rotation):", finalAngle, "degrees");
    console.log("Centroid:", centroid);

    const angleRad = (finalAngle * Math.PI) / 180;
    const cosA = Math.cos(angleRad);
    const sinA = Math.sin(angleRad);

    const rotatedPoints = hull.map((point, index) => {
      // Translate relative to centroid
      const rx = point.x - centroid.x;
      const ry = point.y - centroid.y;

      // Apply 2D rotation: rotate by -finalAngle to "straighten" the receipt
      const rotX = rx * cosA + ry * sinA; // Projection along receipt tilt (horizontal in rotated space)
      const rotY = -rx * sinA + ry * cosA; // Perpendicular to receipt tilt (vertical in rotated space)

      return {
        index,
        original: { x: point.x, y: point.y },
        translated: { x: rx, y: ry },
        rotated: { x: rotX, y: rotY },
        projectionAlongTilt: rotX, // This is what findHullExtremesAlongAngle uses
      };
    });

    // Sort by projection along tilt (x-axis in rotated space)
    const sortedByProjection = [...rotatedPoints].sort(
      (a, b) => a.projectionAlongTilt - b.projectionAlongTilt
    );

    console.log(
      "\nHull points in rotated space (sorted by projection along tilt):"
    );
    sortedByProjection.forEach((point) => {
      const isLeftExtreme = point.index === 1; // leftPoint from extremes
      const isRightExtreme = point.index === 6; // rightPoint from extremes
      const marker = isLeftExtreme
        ? " ← LEFT"
        : isRightExtreme
        ? " ← RIGHT"
        : "";

      console.log(
        `Hull[${point.index}]: rotated(${point.rotated.x.toFixed(
          4
        )}, ${point.rotated.y.toFixed(
          4
        )}) projection=${point.projectionAlongTilt.toFixed(4)}${marker}`
      );
    });

    // Show extremes
    const leftmostPoint = sortedByProjection[0];
    const rightmostPoint = sortedByProjection[sortedByProjection.length - 1];

    console.log(`\nExtreme points in rotated space:`);
    console.log(
      `Leftmost:  Hull[${
        leftmostPoint.index
      }] at rotated(${leftmostPoint.rotated.x.toFixed(
        4
      )}, ${leftmostPoint.rotated.y.toFixed(4)})`
    );
    console.log(
      `Rightmost: Hull[${
        rightmostPoint.index
      }] at rotated(${rightmostPoint.rotated.x.toFixed(
        4
      )}, ${rightmostPoint.rotated.y.toFixed(4)})`
    );

    // Verify our understanding matches the algorithm
    expect(leftmostPoint.index).toBe(1);
    expect(rightmostPoint.index).toBe(6);

    // In rotated space, the receipt text should be roughly "horizontal"
    // Calculate the dimensions to understand the receipt's orientation
    const xRange = rightmostPoint.rotated.x - leftmostPoint.rotated.x;
    const yValues = rotatedPoints.map((p) => p.rotated.y);
    const yRange = Math.max(...yValues) - Math.min(...yValues);

    console.log(`\nRotated space dimensions:`);
    console.log(`X-range (along receipt): ${xRange.toFixed(4)}`);
    console.log(`Y-range (perpendicular): ${yRange.toFixed(4)}`);
    console.log(`Aspect ratio: ${(xRange / yRange).toFixed(2)}:1`);

    // Verify we have meaningful dimensions
    expect(xRange).toBeGreaterThan(0);
    expect(yRange).toBeGreaterThan(0);

    // The rotation should result in a small final angle (near horizontal)
    expect(Math.abs(finalAngle)).toBeLessThan(5); // Should be very close to 0° (horizontal)
  });
});
