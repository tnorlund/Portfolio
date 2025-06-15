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
