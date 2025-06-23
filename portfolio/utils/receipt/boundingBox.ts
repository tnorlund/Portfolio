import type { Line, Point } from "../../types/api";
import { theilSen } from "../geometry";
import { findLineEdgesAtSecondaryExtremes } from "../geometry/receipt";

/**
 * Get the extreme coordinates of a convex hull relative to its centroid.
 *
 * The hull is translated so that the centroid is at the origin. The
 * returned values include both the minimum and maximum offsets as well
 * as the corresponding hull points.
 *
 * @param hull - Convex hull points of the receipt.
 * @param centroid - Centroid of the hull used for translation.
 */
export const findHullExtentsRelativeToCentroid = (
  hull: Point[],
  centroid: Point
): {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  leftPoint: Point;
  rightPoint: Point;
  topPoint: Point;
  bottomPoint: Point;
} => {
  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;
  let leftPoint = hull[0],
    rightPoint = hull[0];
  let topPoint = hull[0],
    bottomPoint = hull[0];

  hull.forEach((point) => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;

    if (relX < minX) {
      minX = relX;
      leftPoint = point;
    }
    if (relX > maxX) {
      maxX = relX;
      rightPoint = point;
    }
    if (relY < minY) {
      minY = relY;
      bottomPoint = point;
    }
    if (relY > maxY) {
      maxY = relY;
      topPoint = point;
    }
  });

  return {
    minX,
    maxX,
    minY,
    maxY,
    leftPoint,
    rightPoint,
    topPoint,
    bottomPoint,
  };
};

/**
 * Compute a bounding box that best fits a skewed receipt hull.
 *
 * The hull is rotated so the receipt is axis aligned. After finding the
 * minimum rectangle in that orientation, the corners are rotated back to
 * the original space.
 *
 * @param hull - Convex hull of receipt points.
 * @param centroid - Centroid of the hull.
 * @param avgAngle - Average text angle in degrees used to deskew the hull.
 * @returns Polygon describing the receipt in clockwise order.
 */
export const computeReceiptBoxFromHull = (
  hull: Point[],
  centroid: Point,
  avgAngle: number
): Point[] => {
  if (hull.length < 3) return [];

  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;

  const angleRad = (-avgAngle * Math.PI) / 180;
  const cosA = Math.cos(angleRad);
  const sinA = Math.sin(angleRad);

  hull.forEach((point) => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;

    const rotX = relX * cosA - relY * sinA;
    const rotY = relX * sinA + relY * cosA;

    minX = Math.min(minX, rotX);
    maxX = Math.max(maxX, rotX);
    minY = Math.min(minY, rotY);
    maxY = Math.max(maxY, rotY);
  });

  const corners = [
    { x: minX, y: maxY },
    { x: maxX, y: maxY },
    { x: maxX, y: minY },
    { x: minX, y: minY },
  ];

  const reverseAngleRad = -angleRad;
  const cosRA = Math.cos(reverseAngleRad);
  const sinRA = Math.sin(reverseAngleRad);

  return corners.map((corner) => ({
    x: corner.x * cosRA - corner.y * sinRA + centroid.x,
    y: corner.x * sinRA + corner.y * cosRA + centroid.y,
  }));
};

/**
 * Gather points along the left and right edges of text lines that sit at
 * the outermost positions along the primary axis.
 *
 * This is used when the receipt is skewed: lines are projected onto the
 * secondary axis to determine which belong to the extreme left and right
 * boundaries.
 *
 * @param lines - OCR lines detected on the receipt image.
 * @param _hull - Unused hull points of the receipt.
 * @param centroid - Centroid of the receipt hull.
 * @param avgAngle - Average text angle in degrees.
 * @returns Arrays of points approximating the left and right edges.
 */
export const findLineEdgesAtPrimaryExtremes = (
  lines: Line[],
  _hull: Point[],
  centroid: Point,
  avgAngle: number
): {
  leftEdge: Point[];
  rightEdge: Point[];
} => {
  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;
  const secondaryAxisAngle = primaryAxisAngle + Math.PI / 2;

  const lineProjections = lines.map((line) => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    const lineCenterY =
      (line.top_left.y +
        line.top_right.y +
        line.bottom_left.y +
        line.bottom_right.y) /
      4;

    const relX = lineCenterX - centroid.x;
    const relY = lineCenterY - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    return {
      line,
      projection: secondaryProjection,
      centerX: lineCenterX,
      centerY: lineCenterY,
    };
  });

  lineProjections.sort((a, b) => a.projection - b.projection);

  const boundaryCount = Math.max(1, Math.ceil(lines.length * 0.2));
  const leftBoundaryLines = lineProjections
    .slice(0, boundaryCount)
    .map((p) => p.line);
  const rightBoundaryLines = lineProjections
    .slice(-boundaryCount)
    .map((p) => p.line);

  let leftEdgePoints: Point[] = [];
  let rightEdgePoints: Point[] = [];

  leftBoundaryLines.forEach((line) => {
    const leftX = Math.min(line.top_left.x, line.bottom_left.x);
    leftEdgePoints.push(
      { x: leftX, y: line.top_left.y },
      { x: leftX, y: line.bottom_left.y }
    );
  });

  rightBoundaryLines.forEach((line) => {
    const rightX = Math.max(line.top_right.x, line.bottom_right.x);
    rightEdgePoints.push(
      { x: rightX, y: line.top_right.y },
      { x: rightX, y: line.bottom_right.y }
    );
  });

  return {
    leftEdge: leftEdgePoints,
    rightEdge: rightEdgePoints,
  };
};

/**
 * Compute angle from points ensuring consistent left-to-right direction.
 * This eliminates angle direction inconsistencies caused by hull point ordering.
 * For line orientations, angles are normalized to [0°, 90°] where values near 180°
 * are treated as being close to 0°.
 */
export const consistentAngleFromPoints = (pts: Point[]): number | null => {
  if (pts.length < 2) return null;

  // Sort points by X coordinate to ensure consistent left-to-right measurement
  const sortedPts = [...pts].sort((a, b) => a.x - b.x);
  const leftPoint = sortedPts[0];
  const rightPoint = sortedPts[sortedPts.length - 1];

  // Calculate angle from leftmost to rightmost point
  const dx = rightPoint.x - leftPoint.x;
  const dy = rightPoint.y - leftPoint.y;

  // Convert slope to angle
  const angleRad = Math.atan2(dy, dx);
  let angleDeg = (angleRad * 180) / Math.PI;

  // Normalize to [0, 180) first
  if (angleDeg < 0) angleDeg += 180;
  if (angleDeg >= 180) angleDeg -= 180;

  // For line orientations, angles > 90° should be treated as their supplement
  // This maps the range to [0°, 90°] where 0° is horizontal
  if (angleDeg > 90) {
    angleDeg = 180 - angleDeg;
  }

  return angleDeg;
};

/**
 * Compute the final tilt angle of the receipt by analyzing text line edges.
 *
 * This function refines the average text angle by examining the top and bottom
 * edges of the text lines. It uses the Theil-Sen estimator to compute robust
 * slope estimates from the edge points and returns the average of the resulting
 * angles.
 *
 * @param lines - OCR lines detected on the receipt image.
 * @param hull - Convex hull points of the receipt.
 * @param centroid - Centroid of the receipt hull.
 * @param avgAngle - Initial average text angle in degrees as fallback.
 * @returns Refined tilt angle in degrees, or original avgAngle if computation fails.
 */
export const computeFinalReceiptTilt = (
  lines: Line[],
  hull: Point[],
  centroid: Point,
  avgAngle: number
): number => {
  if (lines.length === 0 || hull.length < 3) return avgAngle;

  const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
    lines,
    hull,
    centroid,
    avgAngle
  );

  // Use consistent angle calculation that always measures left-to-right
  const aTop = consistentAngleFromPoints(topEdge);
  const aBottom = consistentAngleFromPoints(bottomEdge);

  if (aTop === null || aBottom === null) return avgAngle;

  // Since both angles are now measured consistently, simple averaging should work
  return (aTop + aBottom) / 2;
};

/**
 * Find the extreme points of a convex hull when projected along a specific angle.
 *
 * This function projects all hull points onto a line oriented at the given angle
 * and returns the points that fall at the minimum and maximum positions along
 * that projection. This is useful for finding the boundary points of a rotated
 * bounding box.
 *
 * @param hull - Convex hull points to analyze.
 * @param centroid - Reference point for computing relative positions.
 * @param angleDeg - Projection angle in degrees (0° = horizontal right).
 * @returns Object containing the leftmost and rightmost points along the projection.
 */
export const findHullExtremesAlongAngle = (
  hull: Point[],
  centroid: Point,
  angleDeg: number
): { leftPoint: Point; rightPoint: Point } => {
  if (hull.length === 0) {
    return { leftPoint: centroid, rightPoint: centroid };
  }

  const rad = (angleDeg * Math.PI) / 180;
  const cosA = Math.cos(rad);
  const sinA = Math.sin(rad);

  let minProj = Infinity,
    maxProj = -Infinity;
  let leftPoint = hull[0],
    rightPoint = hull[0];

  hull.forEach((p) => {
    const rx = p.x - centroid.x;
    const ry = p.y - centroid.y;
    const proj = rx * cosA + ry * sinA;
    if (proj < minProj) {
      minProj = proj;
      leftPoint = p;
    }
    if (proj > maxProj) {
      maxProj = proj;
      rightPoint = p;
    }
  });

  return { leftPoint, rightPoint };
};

/**
 * Refine hull extreme points by selecting CW/CCW neighbors using Hull Edge Alignment.
 *
 * For each extreme point, this function compares the clockwise and counter-clockwise
 * neighbors to determine which creates a line that best aligns with adjacent hull edges.
 * This creates boundary lines that "hug" the hull contour more naturally.
 *
 * @param hull - Convex hull points (ordered CCW).
 * @param leftExtreme - Left extreme point from Step 7.
 * @param rightExtreme - Right extreme point from Step 7.
 * @param targetAngle - Target orientation angle in degrees.
 * @returns Refined extreme segments with optimal CW/CCW neighbors.
 */
export const refineHullExtremesWithHullEdgeAlignment = (
  hull: Point[],
  leftExtreme: Point,
  rightExtreme: Point,
  targetAngle: number
): {
  leftSegment: { extreme: Point; optimizedNeighbor: Point };
  rightSegment: { extreme: Point; optimizedNeighbor: Point };
} => {
  if (hull.length < 3) {
    return {
      leftSegment: { extreme: leftExtreme, optimizedNeighbor: leftExtreme },
      rightSegment: { extreme: rightExtreme, optimizedNeighbor: rightExtreme },
    };
  }

  const targetRad = (targetAngle * Math.PI) / 180;

  /**
   * Calculate a comprehensive score for CW/CCW neighbor selection.
   * Uses geometric consistency, hull edge alignment, text proximity, and projection direction.
   */
  const calculateNeighborScore = (
    extreme: Point,
    neighbor: Point,
    isLeftExtreme: boolean
  ): number => {
    // Find the index of the extreme point in the hull
    const extremeIndex = hull.findIndex(
      (p) =>
        Math.abs(p.x - extreme.x) < 1e-10 && Math.abs(p.y - extreme.y) < 1e-10
    );

    if (extremeIndex === -1) return 0; // Extreme not found in hull

    // 1. BOUNDARY APPROPRIATENESS SCORE (30% weight)
    // Enhanced scoring that considers both geometric appropriateness and visual quality
    const dx = neighbor.x - extreme.x;
    const dy = neighbor.y - extreme.y;
    const distance = Math.sqrt(dx * dx + dy * dy);

    let boundaryAppropriatenessScore: number;

    if (isLeftExtreme) {
      // For left boundaries: prefer neighbors that extend leftward or create natural left edges
      if (dx <= 0) {
        // Leftward extension is ideal for left boundaries
        boundaryAppropriatenessScore = 1.0 + distance * 2;
      } else if (dx < 0.03) {
        // Small rightward movement is acceptable
        boundaryAppropriatenessScore = 0.8;
      } else {
        // Excessive rightward movement is problematic for left boundaries
        boundaryAppropriatenessScore = 1 / (1 + dx * 20);
      }
    } else {
      // For right boundaries: consider both rightward extension AND vertical alignment
      // The key insight is that sometimes a neighbor that's slightly leftward
      // but well-aligned creates a better visual boundary than one that extends rightward

      if (dx >= 0.01) {
        // Clear rightward extension is generally good for right boundaries
        boundaryAppropriatenessScore = 1.0 + distance;
      } else if (dx >= -0.05) {
        // Small leftward or near-vertical neighbors can be good for text alignment
        // This handles cases where the CW neighbor is slightly leftward but visually appropriate
        const verticalAlignment = Math.abs(dy) / (Math.abs(dx) + 0.01); // Prevent extreme values
        const cappedVerticalBonus = Math.min(verticalAlignment * 0.3, 0.5); // Cap the bonus
        boundaryAppropriatenessScore = 0.7 + cappedVerticalBonus;
      } else {
        // Significant leftward movement gets penalized for right boundaries
        boundaryAppropriatenessScore = 1 / (1 + Math.abs(dx) * 15);
      }
    }

    // 2. HULL EDGE ALIGNMENT SCORE (30% weight)
    // Get adjacent hull edges around the extreme point
    const prevIndex = (extremeIndex - 1 + hull.length) % hull.length;
    const nextIndex = (extremeIndex + 1) % hull.length;

    const prevPoint = hull[prevIndex];
    const nextPoint = hull[nextIndex];

    // Calculate angles of adjacent hull edges
    const edge1Angle = Math.atan2(
      extreme.y - prevPoint.y,
      extreme.x - prevPoint.x
    );
    const edge2Angle = Math.atan2(
      nextPoint.y - extreme.y,
      nextPoint.x - extreme.x
    );

    // Calculate angle of the potential boundary line
    const lineAngle = Math.atan2(
      neighbor.y - extreme.y,
      neighbor.x - extreme.x
    );

    // Calculate alignment scores (how parallel the line is to each edge)
    const alignmentScore1 = Math.abs(Math.cos(lineAngle - edge1Angle));
    const alignmentScore2 = Math.abs(Math.cos(lineAngle - edge2Angle));
    const hullEdgeAlignment = (alignmentScore1 + alignmentScore2) / 2;

    // 3. TARGET ANGLE ALIGNMENT SCORE (10% weight)
    const targetAlignment = Math.abs(Math.cos(lineAngle - targetRad));

    // Weighted combination: balanced approach with boundary appropriateness and hull alignment
    return (
      boundaryAppropriatenessScore * 0.3 +
      hullEdgeAlignment * 0.6 +
      targetAlignment * 0.1
    );
  };

  /**
   * Find the optimal neighbor (CW or CCW) for an extreme point.
   */
  const findOptimalNeighbor = (
    extreme: Point,
    isLeftExtreme: boolean
  ): Point => {
    const extremeIndex = hull.findIndex(
      (p) =>
        Math.abs(p.x - extreme.x) < 1e-10 && Math.abs(p.y - extreme.y) < 1e-10
    );

    if (extremeIndex === -1) return extreme; // Fallback if extreme not found

    const cwIndex = (extremeIndex + 1) % hull.length;
    const ccwIndex = (extremeIndex - 1 + hull.length) % hull.length;

    const cwNeighbor = hull[cwIndex];
    const ccwNeighbor = hull[ccwIndex];

    const cwScore = calculateNeighborScore(extreme, cwNeighbor, isLeftExtreme);
    const ccwScore = calculateNeighborScore(
      extreme,
      ccwNeighbor,
      isLeftExtreme
    );

    return cwScore > ccwScore ? cwNeighbor : ccwNeighbor;
  };

  return {
    leftSegment: {
      extreme: leftExtreme,
      optimizedNeighbor: findOptimalNeighbor(leftExtreme, true),
    },
    rightSegment: {
      extreme: rightExtreme,
      optimizedNeighbor: findOptimalNeighbor(rightExtreme, false),
    },
  };
};

/**
 * Represents a boundary line in the receipt detection algorithm.
 */
export interface BoundaryLine {
  /** True if the line is vertical (infinite slope) */
  isVertical: boolean;
  /** True if the line is stored in x = slope * y + intercept form */
  isInverted?: boolean;
  /** X-coordinate for vertical lines */
  x?: number;
  /** Slope for non-vertical lines (y = slope * x + intercept) */
  slope: number;
  /** Y-intercept for non-vertical lines (y = slope * x + intercept) */
  intercept: number;
}

/**
 * Compute the final receipt bounding box from four boundary lines (Step 9).
 *
 * This simplified function takes the top, bottom, left, and right boundary lines
 * and computes their four intersection points to form the final receipt quadrilateral.
 * This is a cleaner, more focused approach that separates boundary computation
 * from intersection calculation.
 *
 * @param topBoundary - Top boundary line of the receipt
 * @param bottomBoundary - Bottom boundary line of the receipt
 * @param leftBoundary - Left boundary line of the receipt
 * @param rightBoundary - Right boundary line of the receipt
 * @param fallbackCentroid - Fallback point if intersections fail
 * @returns Four-point quadrilateral representing the final receipt boundary.
 */
export const computeReceiptBoxFromBoundaries = (
  topBoundary: BoundaryLine,
  bottomBoundary: BoundaryLine,
  leftBoundary: BoundaryLine,
  rightBoundary: BoundaryLine,
  fallbackCentroid?: Point
): Point[] => {
  // Function to find intersection of two lines with bounds checking
  const findIntersection = (
    line1: BoundaryLine,
    line2: BoundaryLine
  ): Point => {
    let x: number, y: number;

    if (line1.isVertical && line2.isVertical) {
      return fallbackCentroid
        ? fallbackCentroid
        : { x: (line1.x! + line2.x!) / 2, y: 0.5 };
    }

    if (line1.isVertical) {
      x = line1.x!;
      if (line2.isInverted) {
        if (Math.abs(line2.slope) < 1e-9) {
          if (Math.abs(x - line2.intercept) > 1e-6) {
            return fallbackCentroid ? fallbackCentroid : { x, y: 0.5 };
          }
          y = 0.5;
        } else {
          y = (x - line2.intercept) / line2.slope;
        }
      } else {
        y = line2.slope * x + line2.intercept;
      }
      return { x, y };
    }

    if (line2.isVertical) {
      x = line2.x!;
      if (line1.isInverted) {
        if (Math.abs(line1.slope) < 1e-9) {
          if (Math.abs(x - line1.intercept) > 1e-6) {
            return fallbackCentroid ? fallbackCentroid : { x, y: 0.5 };
          }
          y = 0.5;
        } else {
          y = (x - line1.intercept) / line1.slope;
        }
      } else {
        y = line1.slope * x + line1.intercept;
      }
      return { x, y };
    }

    const line1Inverted = line1.isInverted ?? false;
    const line2Inverted = line2.isInverted ?? false;

    if (line1Inverted && line2Inverted) {
      const denom = line1.slope - line2.slope;
      if (Math.abs(denom) < 1e-9) {
        return fallbackCentroid ? fallbackCentroid : { x: 0.5, y: 0.5 };
      }
      y = (line2.intercept - line1.intercept) / denom;
      x = line1.slope * y + line1.intercept;
    } else if (line1Inverted && !line2Inverted) {
      if (Math.abs(line1.slope) < 1e-9) {
        x = line1.intercept;
        y = line2.slope * x + line2.intercept;
      } else {
        const denom = 1 - line1.slope * line2.slope;
        if (Math.abs(denom) < 1e-9) {
          return fallbackCentroid ? fallbackCentroid : { x: 0.5, y: 0.5 };
        }
        x = (line1.slope * line2.intercept + line1.intercept) / denom;
        y = line2.slope * x + line2.intercept;
      }
    } else if (!line1Inverted && line2Inverted) {
      if (Math.abs(line2.slope) < 1e-9) {
        x = line2.intercept;
        y = line1.slope * x + line1.intercept;
      } else {
        const denom = 1 - line2.slope * line1.slope;
        if (Math.abs(denom) < 1e-9) {
          return fallbackCentroid ? fallbackCentroid : { x: 0.5, y: 0.5 };
        }
        x = (line2.slope * line1.intercept + line2.intercept) / denom;
        y = line1.slope * x + line1.intercept;
      }
    } else {
      const denom = line1.slope - line2.slope;
      if (Math.abs(denom) < 1e-6) {
        return fallbackCentroid ? fallbackCentroid : { x: 0.5, y: 0.5 };
      }
      x = (line2.intercept - line1.intercept) / denom;
      y = line1.slope * x + line1.intercept;
    }

    // Validate and clamp coordinates to reasonable bounds
    const clamp = (value: number, min: number, max: number) =>
      Math.max(min, Math.min(max, value));

    // If coordinates are way out of bounds, use fallback
    if (Math.abs(x) > 10 || Math.abs(y) > 10 || !isFinite(x) || !isFinite(y)) {
      if (fallbackCentroid) {
        return fallbackCentroid;
      }
      // Apply reasonable defaults
      x = clamp(x, -0.5, 1.5);
      y = clamp(y, -0.5, 1.5);
    } else {
      // Apply less restrictive clamping for normal cases
      x = clamp(x, -0.5, 1.5);
      y = clamp(y, -0.5, 1.5);
    }

    return { x, y };
  };

  // Compute the four corner points by intersecting boundaries
  const topLeft = findIntersection(topBoundary, leftBoundary);
  const topRight = findIntersection(topBoundary, rightBoundary);
  const bottomLeft = findIntersection(bottomBoundary, leftBoundary);
  const bottomRight = findIntersection(bottomBoundary, rightBoundary);

  // Return in clockwise order: top-left, top-right, bottom-right, bottom-left
  return [topLeft, topRight, bottomRight, bottomLeft];
};

/**
 * Create a boundary line from two points.
 *
 * @param point1 - First point on the line
 * @param point2 - Second point on the line
 * @returns BoundaryLine representation
 */
export const createBoundaryLineFromPoints = (
  point1: Point,
  point2: Point
): BoundaryLine => {
  const dx = point2.x - point1.x;
  const dy = point2.y - point1.y;

  // Handle nearly vertical lines (infinite slope)
  if (Math.abs(dx) < 1e-9) {
    return {
      isVertical: true,
      x: point1.x,
      slope: 0,
      intercept: 0,
    };
  }

  // Regular line: y = slope * x + intercept
  const slope = dy / dx;
  const intercept = point1.y - slope * point1.x;

  return {
    isVertical: false,
    slope,
    intercept,
  };
};

/**
 * Convert a Theil-Sen line result to a BoundaryLine.
 *
 * @param theilSenResult - Result from theilSen function
 * @returns BoundaryLine representation
 */
export const createBoundaryLineFromTheilSen = (theilSenResult: {
  slope: number;
  intercept: number;
}): BoundaryLine => {
  const { slope, intercept } = theilSenResult;

  // Handle case where all points have the same y-coordinate (horizontal line)
  // In the inverted Theil-Sen system (x = slope*y + intercept), this produces slope ≈ 0
  // FIXED: When slope is near 0, it means a HORIZONTAL line, not vertical!
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
};

/**
 * Compute the final receipt bounding box from refined hull edge alignment segments (Step 9).
 *
 * This function takes the optimal left and right boundary segments determined by Hull Edge
 * Alignment (step 8) and intersects them with the top and bottom edges to create the final
 * receipt quadrilateral. This now delegates to the simpler computeReceiptBoxFromBoundaries.
 *
 * @param lines - OCR lines detected on the receipt image.
 * @param hull - Convex hull points of the receipt.
 * @param centroid - Centroid of the receipt hull.
 * @param finalAngle - Final refined angle from Step 6.
 * @param refinedSegments - Refined boundary segments from Hull Edge Alignment (step 8).
 * @returns Four-point quadrilateral representing the final receipt boundary.
 */
export const computeReceiptBoxFromRefinedSegments = (
  lines: Line[],
  hull: Point[],
  centroid: Point,
  finalAngle: number,
  refinedSegments: {
    leftSegment: { extreme: Point; optimizedNeighbor: Point };
    rightSegment: { extreme: Point; optimizedNeighbor: Point };
  }
): Point[] => {
  if (lines.length === 0 || hull.length < 3) return [];

  // Get top and bottom edges using the final refined angle (same as computed in Step 6)
  const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
    lines,
    hull,
    centroid,
    finalAngle
  );

  if (topEdge.length < 2 || bottomEdge.length < 2) {
    // Fallback to centroid-based box if edge detection fails
    return [
      { x: centroid.x - 0.1, y: centroid.y + 0.1 },
      { x: centroid.x + 0.1, y: centroid.y + 0.1 },
      { x: centroid.x + 0.1, y: centroid.y - 0.1 },
      { x: centroid.x - 0.1, y: centroid.y - 0.1 },
    ];
  }

  // Create the four boundary lines
  const topBoundary = createBoundaryLineFromTheilSen(theilSen(topEdge));
  const bottomBoundary = createBoundaryLineFromTheilSen(theilSen(bottomEdge));
  const leftBoundary = createBoundaryLineFromPoints(
    refinedSegments.leftSegment.extreme,
    refinedSegments.leftSegment.optimizedNeighbor
  );
  const rightBoundary = createBoundaryLineFromPoints(
    refinedSegments.rightSegment.extreme,
    refinedSegments.rightSegment.optimizedNeighbor
  );

  // Compute intersections using the new simplified function
  return computeReceiptBoxFromBoundaries(
    topBoundary,
    bottomBoundary,
    leftBoundary,
    rightBoundary,
    centroid
  );
};
