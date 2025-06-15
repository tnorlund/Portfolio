import type { Line, Point } from "../../types/api";

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
  centroid: Point,
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

  hull.forEach(point => {
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
  avgAngle: number,
): Point[] => {
  if (hull.length < 3) return [];

  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;

  const angleRad = (-avgAngle * Math.PI) / 180;
  const cosA = Math.cos(angleRad);
  const sinA = Math.sin(angleRad);

  hull.forEach(point => {
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

  return corners.map(corner => ({
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
  avgAngle: number,
): {
  leftEdge: Point[];
  rightEdge: Point[];
} => {
  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;
  const secondaryAxisAngle = primaryAxisAngle + Math.PI / 2;

  const lineProjections = lines.map(line => {
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
  const leftBoundaryLines = lineProjections.slice(0, boundaryCount).map(p => p.line);
  const rightBoundaryLines = lineProjections
    .slice(-boundaryCount)
    .map(p => p.line);

  let leftEdgePoints: Point[] = [];
  let rightEdgePoints: Point[] = [];

  leftBoundaryLines.forEach(line => {
    const leftX = Math.min(line.top_left.x, line.bottom_left.x);
    leftEdgePoints.push(
      { x: leftX, y: line.top_left.y },
      { x: leftX, y: line.bottom_left.y },
    );
  });

  rightBoundaryLines.forEach(line => {
    const rightX = Math.max(line.top_right.x, line.bottom_right.x);
    rightEdgePoints.push(
      { x: rightX, y: line.top_right.y },
      { x: rightX, y: line.bottom_right.y },
    );
  });

  return {
    leftEdge: leftEdgePoints,
    rightEdge: rightEdgePoints,
  };
};
