import { computeReceiptBoxFromBoundaries } from "../utils/receipt/boundingBox";

describe("boundary box with hardcoded values", () => {
  test("computes expected corners from hardcoded boundaries", () => {
    // Hardcoded boundary lines that represent a typical receipt
    // Using the OCR coordinate system where y=0 is at the bottom
    const boundaries = {
      // Top boundary: horizontal line at y = 0.82
      top: {
        isVertical: false,
        isInverted: false, // y = slope * x + intercept format
        slope: 0.0,
        intercept: 0.82, // horizontal line at y = 0.82
      },
      // Bottom boundary: horizontal line at y = 0.18
      bottom: {
        isVertical: false,
        isInverted: false, // y = slope * x + intercept format
        slope: 0.0,
        intercept: 0.18, // horizontal line at y = 0.18
      },
      // Left boundary: vertical line at x = 0.234
      left: {
        isVertical: true,
        x: 0.234,
        slope: 0,
        intercept: 0,
      },
      // Right boundary: vertical line at x = 0.726
      right: {
        isVertical: true,
        x: 0.726,
        slope: 0,
        intercept: 0,
      },
    };

    // Centroid for the receipt (center point)
    const centroid = { x: 0.48, y: 0.5 };

    // Compute the box corners
    const box = computeReceiptBoxFromBoundaries(
      boundaries.top,
      boundaries.bottom,
      boundaries.left,
      boundaries.right,
      centroid,
    );

    // Expected corners in OCR coordinate system (y=0 at bottom)
    // Order: [top_left, top_right, bottom_right, bottom_left]
    const expectedCorners = [
      { x: 0.234, y: 0.82 }, // top_left
      { x: 0.726, y: 0.82 }, // top_right
      { x: 0.726, y: 0.18 }, // bottom_right
      { x: 0.234, y: 0.18 }, // bottom_left
    ];

    // Verify the corners match expected values
    const tolerance = 1e-6;
    box.forEach((actual, i) => {
      const expected = expectedCorners[i];
      expect(Math.abs(expected.x - actual.x)).toBeLessThan(tolerance);
      expect(Math.abs(expected.y - actual.y)).toBeLessThan(tolerance);
    });
  });

  test("computes expected corners with slanted boundaries", () => {
    // Hardcoded boundary lines for a slanted receipt
    const boundaries = {
      // Top boundary: slightly slanted line (y = 0.02x + 0.8)
      top: {
        isVertical: false,
        isInverted: false, // y = slope * x + intercept format
        slope: 0.02,
        intercept: 0.8,
      },
      // Bottom boundary: slightly slanted line (y = 0.02x + 0.15)
      bottom: {
        isVertical: false,
        isInverted: false, // y = slope * x + intercept format
        slope: 0.02,
        intercept: 0.15,
      },
      // Left boundary: near-vertical line (x = -0.1y + 0.3)
      left: {
        isVertical: false,
        isInverted: true, // x = slope * y + intercept format
        slope: -0.1,
        intercept: 0.3,
      },
      // Right boundary: near-vertical line (x = -0.1y + 0.8)
      right: {
        isVertical: false,
        isInverted: true, // x = slope * y + intercept format
        slope: -0.1,
        intercept: 0.8,
      },
    };

    // Centroid for the receipt
    const centroid = { x: 0.5, y: 0.5 };

    // Compute the box corners
    const box = computeReceiptBoxFromBoundaries(
      boundaries.top,
      boundaries.bottom,
      boundaries.left,
      boundaries.right,
      centroid,
    );

    // For slanted boundaries, we verify the box has 4 corners
    expect(box.length).toBe(4);

    // Verify corners form a valid quadrilateral (non-zero area)
    // Using shoelace formula for polygon area
    let area = 0.0;
    const n = box.length;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      area += box[i].x * box[j].y;
      area -= box[j].x * box[i].y;
    }
    area = Math.abs(area) / 2.0;

    expect(area).toBeGreaterThan(0.01);
  });
});