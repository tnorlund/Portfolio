import type { Line } from "../../types/api";
import { Point, theilSen } from "./basic";

/**
 * Determine a bounding box from a skewed hull by estimating the
 * vertical extents after de-skewing.
 *
 * @param hull - Convex hull points of the receipt.
 * @param cx - X‑coordinate of the hull centroid.
 * @param cy - Y‑coordinate of the hull centroid.
 * @param rotationDeg - Rotation angle in degrees used to deskew
 * the hull.
 * @returns Four points representing the receipt box in clockwise
 * order or `null` when the hull is empty.
 */
export const computeReceiptBoxFromSkewedExtents = (
  hull: Point[],
  cx: number,
  cy: number,
  rotationDeg: number
): Point[] | null => {
  if (hull.length === 0) return null;
  const theta = (rotationDeg * Math.PI) / 180;
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);

  const deskew = (p: Point): Point => {
    const dx = p.x - cx;
    const dy = p.y - cy;
    return { x: dx * cosT + dy * sinT, y: -dx * sinT + dy * cosT };
  };

  const deskewed = hull.map(deskew);
  const top = deskewed.filter((p) => p.y < 0);
  const bottom = deskewed.filter((p) => p.y >= 0);
  const topHalf = top.length ? top : deskewed.slice();
  const bottomHalf = bottom.length ? bottom : deskewed.slice();

  const minX = (pts: Point[]) => pts.reduce((a, b) => (a.x < b.x ? a : b));
  const maxX = (pts: Point[]) => pts.reduce((a, b) => (a.x > b.x ? a : b));

  const leftTop = minX(topHalf);
  const rightTop = maxX(topHalf);
  const leftBottom = minX(bottomHalf);
  const rightBottom = maxX(bottomHalf);

  const allY = deskewed.map((p) => p.y);
  const topY = Math.min(...allY);
  const bottomY = Math.max(...allY);

  const interpolate = (
    vTop: Point,
    vBottom: Point,
    desiredY: number
  ): Point => {
    const dy = vBottom.y - vTop.y;
    if (Math.abs(dy) < 1e-9) return { x: vTop.x, y: desiredY };
    const t = (desiredY - vTop.y) / dy;
    const x = vTop.x + t * (vBottom.x - vTop.x);
    return { x, y: desiredY };
  };

  const leftTopPoint = interpolate(leftTop, leftBottom, topY);
  const leftBottomPoint = interpolate(leftTop, leftBottom, bottomY);
  const rightTopPoint = interpolate(rightTop, rightBottom, topY);
  const rightBottomPoint = interpolate(rightTop, rightBottom, bottomY);

  const cosInv = Math.cos(-theta);
  const sinInv = Math.sin(-theta);
  const inverse = (p: Point): Point => {
    const rx = p.x * cosInv + p.y * sinInv;
    const ry = -p.x * sinInv + p.y * cosInv;
    return { x: rx + cx, y: ry + cy };
  };

  const corners = [
    leftTopPoint,
    rightTopPoint,
    rightBottomPoint,
    leftBottomPoint,
  ].map(inverse);
  return corners.map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }));
};
/**
 * Sample points from a convex hull to estimate the left or right edge
 * as a straight line.
 *
 * @param hull - Polygon points representing the convex hull.
 * @param bins - Number of vertical bins used to sample representative
 * points.
 * @param pick - Which side of the hull to estimate: `"left"` or
 * `"right"`.
 * @returns The line through the sampled points or `null` when not
 * enough samples are available.
 */
export const computeHullEdge = (
  hull: Point[],
  bins = 12,
  pick: "left" | "right"
): { top: Point; bottom: Point } | null => {
  if (hull.length < 4) return null;
  const binPts: (Point | null)[] = Array.from({ length: bins }, () => null);
  hull.forEach((p) => {
    const idx = Math.min(bins - 1, Math.floor(p.y * bins));
    const current = binPts[idx];
    if (!current || (pick === "left" ? p.x < current.x : p.x > current.x)) {
      binPts[idx] = p;
    }
  });
  const samples = binPts.filter(Boolean) as Point[];
  if (samples.length < 2) return null;
  const { slope, intercept } = theilSen(samples);
  return {
    top: { x: slope * 1 + intercept, y: 1 },
    bottom: { x: slope * 0 + intercept, y: 0 },
  };
};

/**
 * Estimate a straight edge from OCR line data.
 *
 * @param lines - Detected OCR lines for the image.
 * @param pick - Whether to compute the `"left"` or `"right"` edge.
 * @param bins - Number of vertical bins to reduce the point cloud.
 * @returns The approximated edge or `null` if there are not enough
 * samples.
 */
export const computeEdge = (
  lines: Line[],
  pick: "left" | "right",
  bins = 6
): { top: Point; bottom: Point } | null => {
  const binPts: (Point | null)[] = Array.from({ length: bins }, () => null);

  lines.forEach((l) => {
    const yMid = (l.top_left.y + l.bottom_left.y) / 2;
    const x =
      pick === "left"
        ? Math.min(l.top_left.x, l.bottom_left.x)
        : Math.max(l.top_right.x, l.bottom_right.x);

    const idx = Math.min(bins - 1, Math.floor(yMid * bins));
    const current = binPts[idx];

    if (!current) {
      binPts[idx] = { x, y: yMid };
    } else if (pick === "left" ? x < current.x : x > current.x) {
      binPts[idx] = { x, y: yMid };
    }
  });

  const selected = binPts.filter(Boolean) as Point[];
  if (selected.length < 2) return null;

  const { slope, intercept } = theilSen(selected);
  return {
    top: { x: slope * 1 + intercept, y: 1 },
    bottom: { x: slope * 0 + intercept, y: 0 },
  };
};

/**
 * Locate points along the top and bottom edges of the text lines at the
 * extreme secondary-axis positions.
 *
 * @param lines - OCR lines used to derive the edges.
 * @param hull - Convex hull of all line points.
 * @param centroid - Centroid of the hull.
 * @param avgAngle - Average rotation angle of the text lines in
 * degrees.
 * @returns Arrays of points describing the top and bottom edges.
 */
export const findLineEdgesAtSecondaryExtremes = (
  lines: Line[],
  hull: Point[],
  centroid: Point,
  avgAngle: number
): { topEdge: Point[]; bottomEdge: Point[] } => {
  const angleRad = (avgAngle * Math.PI) / 180;
  const secondaryAxisAngle = angleRad + Math.PI / 2;

  // Project all hull points onto the secondary axis
  const hullProjections = hull.map((point, index) => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);
    return { point, projection: secondaryProjection, index };
  });

  // Sort by secondary projection
  hullProjections.sort((a, b) => b.projection - a.projection);

  // Take the top 2 points (highest projections) and bottom 2 points (lowest projections)
  const topHullPoints = hullProjections.slice(0, 2).map((hp) => hp.point);
  const bottomHullPoints = hullProjections.slice(-2).map((hp) => hp.point);

  // Always use exactly the 2 hull points for each boundary - no text line supplementation
  const topEdgePoints = topHullPoints;
  const bottomEdgePoints = bottomHullPoints;

  return { topEdge: topEdgePoints, bottomEdge: bottomEdgePoints };
};

/**
 * Build a four‑point bounding box for a receipt based on estimated
 * line edges.
 *
 * @param lines - OCR lines from which the edges are derived.
 * @param hull - Convex hull of all line corners.
 * @param centroid - Centroid of the hull.
 * @param avgAngle - Average orientation of the lines in degrees.
 * @returns The receipt polygon defined in clockwise order. Returns an
 * empty array when no lines are supplied.
 */
export const computeReceiptBoxFromLineEdges = (
  lines: Line[],
  hull: Point[],
  centroid: Point,
  avgAngle: number
): Point[] => {
  if (lines.length === 0) return [];

  const leftEdge = computeEdge(lines, "left");
  const rightEdge = computeEdge(lines, "right");

  if (!leftEdge || !rightEdge) {
    return [
      { x: centroid.x - 0.1, y: centroid.y + 0.1 },
      { x: centroid.x + 0.1, y: centroid.y + 0.1 },
      { x: centroid.x + 0.1, y: centroid.y - 0.1 },
      { x: centroid.x - 0.1, y: centroid.y - 0.1 },
    ];
  }

  const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
    lines,
    hull,
    centroid,
    avgAngle
  );

  const topEdgeLine = topEdge.length >= 2 ? theilSen(topEdge) : null;
  const bottomEdgeLine = bottomEdge.length >= 2 ? theilSen(bottomEdge) : null;

  const leftSlope =
    (leftEdge.top.x - leftEdge.bottom.x) / (leftEdge.top.y - leftEdge.bottom.y);
  const leftIntercept = leftEdge.top.x - leftSlope * leftEdge.top.y;

  const rightSlope =
    (rightEdge.top.x - rightEdge.bottom.x) /
    (rightEdge.top.y - rightEdge.bottom.y);
  const rightIntercept = rightEdge.top.x - rightSlope * rightEdge.top.y;

  let topLeft: Point;
  let topRight: Point;
  let bottomLeft: Point;
  let bottomRight: Point;

  if (topEdgeLine) {
    const topLeftY =
      (leftIntercept - topEdgeLine.intercept) / (topEdgeLine.slope - leftSlope);
    const topLeftX = leftSlope * topLeftY + leftIntercept;
    topLeft = { x: topLeftX, y: topLeftY };

    const topRightY =
      (rightIntercept - topEdgeLine.intercept) /
      (topEdgeLine.slope - rightSlope);
    const topRightX = rightSlope * topRightY + rightIntercept;
    topRight = { x: topRightX, y: topRightY };
  } else {
    const avgTopY =
      topEdge.reduce((sum, p) => sum + p.y, 0) / Math.max(topEdge.length, 1);
    topLeft = { x: leftSlope * avgTopY + leftIntercept, y: avgTopY };
    topRight = { x: rightSlope * avgTopY + rightIntercept, y: avgTopY };
  }

  if (bottomEdgeLine) {
    const bottomLeftY =
      (leftIntercept - bottomEdgeLine.intercept) /
      (bottomEdgeLine.slope - leftSlope);
    const bottomLeftX = leftSlope * bottomLeftY + leftIntercept;
    bottomLeft = { x: bottomLeftX, y: bottomLeftY };

    const bottomRightY =
      (rightIntercept - bottomEdgeLine.intercept) /
      (bottomEdgeLine.slope - rightSlope);
    const bottomRightX = rightSlope * bottomRightY + rightIntercept;
    bottomRight = { x: bottomRightX, y: bottomRightY };
  } else {
    const avgBottomY =
      bottomEdge.reduce((sum, p) => sum + p.y, 0) /
      Math.max(bottomEdge.length, 1);
    bottomLeft = { x: leftSlope * avgBottomY + leftIntercept, y: avgBottomY };
    bottomRight = {
      x: rightSlope * avgBottomY + rightIntercept,
      y: avgBottomY,
    };
  }

  return [topLeft, topRight, bottomRight, bottomLeft];
};
