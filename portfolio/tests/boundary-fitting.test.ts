/**
 * Comprehensive tests for boundary fitting including the horizontal line fix.
 * This consolidates key boundary fitting tests and demonstrates the proposed fixes.
 */

import { theilSen } from "../utils/geometry";
import { 
  createBoundaryLineFromTheilSen,
  computeReceiptBoxFromBoundaries,
  type BoundaryLine
} from "../utils/receipt/boundingBox";

describe("Boundary Fitting Tests", () => {
  describe("Theil-Sen horizontal line bug", () => {
    test("correctly handles horizontal lines (FIXED)", () => {
      // Horizontal line at y = 0.180233 (from bar receipt)
      const horizontalPoints = [
        { x: 0.257752, y: 0.180233 },
        { x: 0.500000, y: 0.180233 },
      ];

      // Theil-Sen result
      const result = theilSen(horizontalPoints);
      expect(result.slope).toBe(0);
      expect(result.intercept).toBeCloseTo(0.180233);

      // Fixed behavior: correctly interprets as horizontal line
      const boundary = createBoundaryLineFromTheilSen(result);
      expect(boundary.isVertical).toBe(false); // Now correct!
      expect(boundary.isInverted).toBe(false);
      expect(boundary.slope).toBe(0);
      expect(boundary.intercept).toBeCloseTo(0.180233);
    });

    test("produces valid corners with fixed implementation", () => {
      // Create boundaries using the actual implementation
      const topResult = { slope: -61.333328444811286, intercept: 57.546507147981885 };
      const bottomResult = { slope: 0, intercept: 0.1802325584269746 };
      
      const boundaries = {
        top: createBoundaryLineFromTheilSen(topResult),
        bottom: createBoundaryLineFromTheilSen(bottomResult), // Now correctly horizontal!
        left: {
          isVertical: true,
          x: 0.23449612316018242,
          slope: 0,
          intercept: 0,
        },
        right: {
          isVertical: false,
          slope: -13.548385902431102,
          intercept: 10.358264931625259,
        },
      };

      const centroid = { x: 0.4788584657767676, y: 0.544328338549982 };

      // This now produces valid corners
      const box = computeReceiptBoxFromBoundaries(
        boundaries.top,
        boundaries.bottom,
        boundaries.left,
        boundaries.right,
        centroid
      );

      // Bottom right corner is now within [0,1]
      const bottomRight = box[2];
      expect(bottomRight.x).toBeCloseTo(0.751, 2);
      expect(bottomRight.y).toBeCloseTo(0.180, 2); // Fixed!
    });
  });

  describe("Proposed fix for horizontal lines", () => {
    // Fixed version of createBoundaryLineFromTheilSen
    function createBoundaryLineFromTheilSenFixed(theilSenResult: {
      slope: number;
      intercept: number;
    }): BoundaryLine {
      const { slope, intercept } = theilSenResult;

      // FIXED: When slope is near 0 in inverted coordinates,
      // it means a HORIZONTAL line, not vertical!
      if (Math.abs(slope) < 1e-9) {
        return {
          isVertical: false,
          isInverted: false, // Use standard y = mx + b form
          slope: 0,
          intercept: intercept, // This is the Y value of the horizontal line
        };
      }

      // Otherwise, it's a near-vertical line in inverted coordinates
      return {
        isVertical: false,
        isInverted: true,
        slope,
        intercept,
      };
    }

    test("correctly interprets horizontal lines", () => {
      const theilResult = { slope: 0, intercept: 0.180233 };
      
      const fixedBoundary = createBoundaryLineFromTheilSenFixed(theilResult);
      
      expect(fixedBoundary.isVertical).toBe(false);
      expect(fixedBoundary.isInverted).toBe(false);
      expect(fixedBoundary.slope).toBe(0);
      expect(fixedBoundary.intercept).toBeCloseTo(0.180233);
    });

    test("produces valid corners with the fix", () => {
      const boundaries = {
        top: createBoundaryLineFromTheilSenFixed({
          slope: -61.333328444811286,
          intercept: 57.546507147981885,
        }),
        bottom: createBoundaryLineFromTheilSenFixed({
          slope: 0,
          intercept: 0.1802325584269746,
        }),
        left: {
          isVertical: true,
          x: 0.23449612316018242,
          slope: 0,
          intercept: 0,
        },
        right: {
          isVertical: false,
          slope: -13.548385902431102,
          intercept: 10.358264931625259,
        },
      };

      const centroid = { x: 0.4788584657767676, y: 0.544328338549982 };

      const box = computeReceiptBoxFromBoundaries(
        boundaries.top,
        boundaries.bottom,
        boundaries.left,
        boundaries.right,
        centroid
      );

      // All corners should be within [0,1] bounds
      box.forEach((corner, i) => {
        expect(corner.x).toBeGreaterThanOrEqual(-0.1);
        expect(corner.x).toBeLessThanOrEqual(1.1);
        expect(corner.y).toBeGreaterThanOrEqual(-0.1);
        expect(corner.y).toBeLessThanOrEqual(1.1);
      });

      // Specifically check bottom right is fixed
      const bottomRight = box[2];
      expect(bottomRight.x).toBeCloseTo(0.751, 2);
      expect(bottomRight.y).toBeCloseTo(0.180, 2); // Not 7.916!
    });
  });

  describe("Hardcoded boundary tests", () => {
    test("computes correct corners for axis-aligned boundaries", () => {
      const boundaries = {
        top: { isVertical: false, slope: 0, intercept: 0.82 },
        bottom: { isVertical: false, slope: 0, intercept: 0.18 },
        left: { isVertical: true, x: 0.234, slope: 0, intercept: 0 },
        right: { isVertical: true, x: 0.726, slope: 0, intercept: 0 },
      };

      const centroid = { x: 0.48, y: 0.5 };

      const box = computeReceiptBoxFromBoundaries(
        boundaries.top,
        boundaries.bottom,
        boundaries.left,
        boundaries.right,
        centroid
      );

      // Expected corners for axis-aligned box
      const expected = [
        { x: 0.234, y: 0.82 }, // top_left
        { x: 0.726, y: 0.82 }, // top_right
        { x: 0.726, y: 0.18 }, // bottom_right
        { x: 0.234, y: 0.18 }, // bottom_left
      ];

      box.forEach((actual, i) => {
        expect(actual.x).toBeCloseTo(expected[i].x, 6);
        expect(actual.y).toBeCloseTo(expected[i].y, 6);
      });
    });

    test("handles slanted boundaries correctly", () => {
      const boundaries = {
        top: { isVertical: false, slope: 0.02, intercept: 0.8 },
        bottom: { isVertical: false, slope: 0.02, intercept: 0.15 },
        left: { isVertical: false, isInverted: true, slope: -0.1, intercept: 0.3 },
        right: { isVertical: false, isInverted: true, slope: -0.1, intercept: 0.8 },
      };

      const centroid = { x: 0.5, y: 0.5 };

      const box = computeReceiptBoxFromBoundaries(
        boundaries.top,
        boundaries.bottom,
        boundaries.left,
        boundaries.right,
        centroid
      );

      expect(box.length).toBe(4);

      // Verify corners form a valid quadrilateral (non-zero area)
      let area = 0;
      for (let i = 0; i < box.length; i++) {
        const j = (i + 1) % box.length;
        area += box[i].x * box[j].y;
        area -= box[j].x * box[i].y;
      }
      area = Math.abs(area) / 2;

      expect(area).toBeGreaterThan(0.01);
    });
  });

  describe("Bar receipt boundaries", () => {
    test("identifies horizontal bottom edge", () => {
      // Bottom edge points from bar receipt
      const bottomEdge = [
        { x: 0.257752, y: 0.180233 },
        { x: 0.500000, y: 0.180233 },
      ];

      // All points have same Y coordinate
      const yValues = bottomEdge.map(p => p.y);
      const yMin = Math.min(...yValues);
      const yMax = Math.max(...yValues);
      const yRange = yMax - yMin;

      expect(yRange).toBeLessThan(1e-6); // Horizontal line
    });

    test("exact boundary values from bar receipt", () => {
      // These are the exact boundaries computed from the bar receipt
      const boundaries = {
        top: {
          isVertical: false,
          isInverted: true,
          slope: -61.333328444811286,
          intercept: 57.546507147981885,
        },
        bottom: {
          isVertical: false,
          isInverted: false, // Fixed: horizontal line
          slope: 0,
          intercept: 0.1802325584269746,
        },
        left: {
          isVertical: true,
          x: 0.23449612316018242,
          slope: 0,
          intercept: 0,
        },
        right: {
          isVertical: false,
          slope: -13.548385902431102,
          intercept: 10.358264931625259,
        },
      };

      const centroid = { x: 0.4788584657767676, y: 0.544328338549982 };

      const box = computeReceiptBoxFromBoundaries(
        boundaries.top,
        boundaries.bottom,
        boundaries.left,
        boundaries.right,
        centroid
      );

      // Expected corners (matching Python implementation with fix)
      const expectedCorners = [
        { x: 0.234496, y: 0.934448 }, // top_left
        { x: 0.696123, y: 0.926922 }, // top_right
        { x: 0.751238, y: 0.180233 }, // bottom_right
        { x: 0.234496, y: 0.180233 }, // bottom_left
      ];

      // Allow small tolerance for floating-point differences
      const tolerance = 2e-5;
      box.forEach((actual, i) => {
        expect(Math.abs(actual.x - expectedCorners[i].x)).toBeLessThan(tolerance);
        expect(Math.abs(actual.y - expectedCorners[i].y)).toBeLessThan(tolerance);
      });
    });
  });
});