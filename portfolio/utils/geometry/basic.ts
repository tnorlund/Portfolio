/**
 * Represents a 2D coordinate used by the geometry utilities.
 */
export interface Point {
  /** Horizontal coordinate in normalized [0,1] space. */
  x: number;
  /** Vertical coordinate in normalized [0,1] space. */
  y: number;
}


/**
 * Compute the convex hull of a set of points using a monotone chain
 * algorithm.
 *
 * @param points - Points to compute the hull for.
 * @returns An array of points describing the outer hull in
 * counter‑clockwise order.
 */
export const convexHull = (points: Point[]): Point[] => {
  const sorted = Array.from(new Set(points.map(p => `${p.x},${p.y}`))).map(s => {
    const [x, y] = s.split(',').map(Number);
    return { x, y } as Point;
  }).sort((a, b) => (a.x === b.x ? a.y - b.y : a.x - b.x));
  if (sorted.length <= 1) {
    return sorted;
  }

  const cross = (o: Point, a: Point, b: Point): number =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);

  const lower: Point[] = [];
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }

  const upper: Point[] = [];
  for (const p of [...sorted].reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }

  upper.pop();
  lower.pop();
  return lower.concat(upper);
};

/**
 * Compute the centroid of a polygon described by its convex hull.
 *
 * The function falls back to simple averages for degenerate cases such
 * as a hull with less than three points.
 *
 * @param hull - Polygon vertices in counter‑clockwise order.
 * @returns The centroid point of the polygon.
 */
export const computeHullCentroid = (hull: Point[]): Point => {
  const n = hull.length;
  if (n === 0) return { x: 0, y: 0 };
  if (n === 1) return { x: hull[0].x, y: hull[0].y };
  if (n === 2) return { x: (hull[0].x + hull[1].x) / 2, y: (hull[0].y + hull[1].y) / 2 };

  let areaSum = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < n; i++) {
    const p0 = hull[i];
    const p1 = hull[(i + 1) % n];
    const crossVal = p0.x * p1.y - p1.x * p0.y;
    areaSum += crossVal;
    cx += (p0.x + p1.x) * crossVal;
    cy += (p0.y + p1.y) * crossVal;
  }
  const area = areaSum / 2;
  if (Math.abs(area) < 1e-14) {
    const xAvg = hull.reduce((acc, p) => acc + p.x, 0) / n;
    const yAvg = hull.reduce((acc, p) => acc + p.y, 0) / n;
    return { x: xAvg, y: yAvg };
  }
  cx /= 6 * area;
  cy /= 6 * area;
  return { x: cx, y: cy };
};
/**
 * Circular mean of two angles (handles ±π wraparound).
 * For example, averaging +179° and -179° gives ±180° instead of 0°.
 *
 * @param angle1 - First angle in radians
 * @param angle2 - Second angle in radians
 * @returns Mean angle in radians
 */
export const circularMeanAngle = (angle1: number, angle2: number): number => {
  const sinSum = Math.sin(angle1) + Math.sin(angle2);
  const cosSum = Math.cos(angle1) + Math.cos(angle2);
  return Math.atan2(sinSum, cosSum);
};

/**
 * Find intersection of two lines defined by point + direction.
 *
 * @param p1 - Point on first line
 * @param d1 - Direction vector of first line
 * @param p2 - Point on second line
 * @param d2 - Direction vector of second line
 * @returns Intersection point, or null if lines are parallel
 */
export const lineIntersection = (
  p1: Point,
  d1: Point,
  p2: Point,
  d2: Point
): Point | null => {
  const cross = d1.x * d2.y - d1.y * d2.x;
  if (Math.abs(cross) < 1e-9) {
    return null; // Parallel
  }
  const dp = { x: p2.x - p1.x, y: p2.y - p1.y };
  const t = (dp.x * d2.y - dp.y * d2.x) / cross;
  return { x: p1.x + t * d1.x, y: p1.y + t * d1.y };
};

/**
 * Compute receipt corners using the rotated bounding box approach.
 *
 * This derives left/right edges from the receipt tilt (average of top/bottom
 * edge angles) and projects hull points onto the perpendicular axis to find
 * extremes.
 *
 * @param hull - Convex hull points of all word corners
 * @param topLineCorners - Corners from top line [TL, TR, BL, BR]
 * @param bottomLineCorners - Corners from bottom line [TL, TR, BL, BR]
 * @returns Receipt corners [top_left, top_right, bottom_right, bottom_left]
 */
export const computeRotatedBoundingBoxCorners = (
  hull: Point[],
  topLineCorners: Point[],
  bottomLineCorners: Point[]
): Point[] => {
  if (hull.length < 3 || topLineCorners.length < 4 || bottomLineCorners.length < 4) {
    return [];
  }

  // Extract key points from line corners
  const topLeftPt = topLineCorners[0]; // TL
  const topRightPt = topLineCorners[1]; // TR
  const bottomLeftPt = bottomLineCorners[2]; // BL
  const bottomRightPt = bottomLineCorners[3]; // BR

  // Compute angle of top edge
  const topDx = topRightPt.x - topLeftPt.x;
  const topDy = topRightPt.y - topLeftPt.y;
  const topAngle = Math.atan2(topDy, topDx);

  // Compute angle of bottom edge
  const bottomDx = bottomRightPt.x - bottomLeftPt.x;
  const bottomDy = bottomRightPt.y - bottomLeftPt.y;
  const bottomAngle = Math.atan2(bottomDy, bottomDx);

  // Average angle using circular mean (handles wraparound at ±π)
  const avgAngle = circularMeanAngle(topAngle, bottomAngle);

  // Left edge direction: perpendicular to horizontal edges
  const leftEdgeAngle = avgAngle + Math.PI / 2;
  const leftDx = Math.cos(leftEdgeAngle);
  const leftDy = Math.sin(leftEdgeAngle);

  // Edge directions
  const topDir = { x: topDx, y: topDy };
  const bottomDir = { x: bottomDx, y: bottomDy };
  const leftDir = { x: leftDx, y: leftDy };

  // Compute hull centroid
  const centroid = computeHullCentroid(hull);

  // Horizontal direction (along receipt tilt)
  const horizDir = { x: Math.cos(avgAngle), y: Math.sin(avgAngle) };

  // Project hull points onto perpendicular axis to find left/right extremes
  const perpProjections: Array<{ proj: number; point: Point }> = [];
  for (const p of hull) {
    const rel = { x: p.x - centroid.x, y: p.y - centroid.y };
    const proj = rel.x * horizDir.x + rel.y * horizDir.y;
    perpProjections.push({ proj, point: p });
  }

  perpProjections.sort((a, b) => a.proj - b.proj);
  const leftmostHullPt = perpProjections[0].point;
  const rightmostHullPt = perpProjections[perpProjections.length - 1].point;

  // Final corners: intersect edges
  const topLeft = lineIntersection(topLeftPt, topDir, leftmostHullPt, leftDir);
  const topRight = lineIntersection(topLeftPt, topDir, rightmostHullPt, leftDir);
  const bottomLeft = lineIntersection(bottomLeftPt, bottomDir, leftmostHullPt, leftDir);
  const bottomRight = lineIntersection(bottomLeftPt, bottomDir, rightmostHullPt, leftDir);

  // Handle intersection failures - fallback to axis-aligned bounds
  if (!topLeft || !topRight || !bottomLeft || !bottomRight) {
    const hullXs = hull.map((p) => p.x);
    const minHullX = Math.min(...hullXs);
    const maxHullX = Math.max(...hullXs);
    return [
      { x: minHullX, y: topLeftPt.y },
      { x: maxHullX, y: topRightPt.y },
      { x: maxHullX, y: bottomRightPt.y },
      { x: minHullX, y: bottomLeftPt.y },
    ];
  }

  return [topLeft, topRight, bottomRight, bottomLeft];
};

/**
 * Perform Theil–Sen regression to estimate a line through a set of
 * points.
 *
 * @param pts - Sample points where `x` is the independent variable and
 * `y` is the dependent variable.
 * @returns The estimated slope and intercept of the regression line.
 */
export const theilSen = (pts: Point[]) => {
  if (pts.length < 2) return { slope: 0, intercept: pts[0] ? pts[0].y : 0 };

  const slopes: number[] = [];
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      if (pts[i].y === pts[j].y) continue;
      slopes.push((pts[j].x - pts[i].x) / (pts[j].y - pts[i].y));
    }
  }
  if (slopes.length === 0) {
    return { slope: 0, intercept: pts[0].y };
  }
  slopes.sort((a, b) => a - b);
  const slope = slopes[Math.floor(slopes.length / 2)];

  const intercepts = pts.map(p => p.x - slope * p.y).sort((a, b) => a - b);
  const intercept = intercepts[Math.floor(intercepts.length / 2)];

  return { slope, intercept };
};
