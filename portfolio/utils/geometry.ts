export interface Point {
  x: number;
  y: number;
}

import type { Line } from "../types/api";

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
  const top = deskewed.filter(p => p.y < 0);
  const bottom = deskewed.filter(p => p.y >= 0);
  const topHalf = top.length ? top : deskewed.slice();
  const bottomHalf = bottom.length ? bottom : deskewed.slice();

  const minX = (pts: Point[]) => pts.reduce((a, b) => (a.x < b.x ? a : b));
  const maxX = (pts: Point[]) => pts.reduce((a, b) => (a.x > b.x ? a : b));

  const leftTop = minX(topHalf);
  const rightTop = maxX(topHalf);
  const leftBottom = minX(bottomHalf);
  const rightBottom = maxX(bottomHalf);

  const allY = deskewed.map(p => p.y);
  const topY = Math.min(...allY);
  const bottomY = Math.max(...allY);

  const interpolate = (vTop: Point, vBottom: Point, desiredY: number): Point => {
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

  const corners = [leftTopPoint, rightTopPoint, rightBottomPoint, leftBottomPoint].map(inverse);
  return corners.map(p => ({ x: Math.round(p.x), y: Math.round(p.y) }));
};

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

export const computeHullEdge = (
  hull: Point[],
  bins = 12,
  pick: "left" | "right"
): { top: Point; bottom: Point } | null => {
  if (hull.length < 4) return null;
  const binPts: (Point | null)[] = Array.from({ length: bins }, () => null);
  hull.forEach(p => {
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

export const computeEdge = (
  lines: Line[],
  pick: "left" | "right",
  bins = 6
): { top: Point; bottom: Point } | null => {
  const binPts: (Point | null)[] = Array.from({ length: bins }, () => null);

  lines.forEach(l => {
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

export const findLineEdgesAtSecondaryExtremes = (
  lines: Line[],
  hull: Point[],
  centroid: Point,
  avgAngle: number
): { topEdge: Point[]; bottomEdge: Point[] } => {
  const angleRad = (avgAngle * Math.PI) / 180;
  const secondaryAxisAngle = angleRad + Math.PI / 2;

  let minSecondary = Infinity,
    maxSecondary = -Infinity;
  let topExtremeX = 0,
    bottomExtremeX = 0;

  hull.forEach(point => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    if (secondaryProjection < minSecondary) {
      minSecondary = secondaryProjection;
      bottomExtremeX = point.x;
    }
    if (secondaryProjection > maxSecondary) {
      maxSecondary = secondaryProjection;
      topExtremeX = point.x;
    }
  });

  const tolerance = 0.1;
  const topLines = lines.filter(line => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    return Math.abs(lineCenterX - topExtremeX) < tolerance;
  });

  const bottomLines = lines.filter(line => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    return Math.abs(lineCenterX - bottomExtremeX) < tolerance;
  });

  let topEdgePoints: Point[] = [];
  let bottomEdgePoints: Point[] = [];

  if (topLines.length > 0) {
    topLines.forEach(line => {
      const topY = Math.max(line.top_left.y, line.top_right.y);
      topEdgePoints.push(
        { x: line.top_left.x, y: topY },
        { x: line.top_right.x, y: topY }
      );
    });
  }

  if (bottomLines.length > 0) {
    bottomLines.forEach(line => {
      const bottomY = Math.min(line.bottom_left.y, line.bottom_right.y);
      bottomEdgePoints.push(
        { x: line.bottom_left.x, y: bottomY },
        { x: line.bottom_right.x, y: bottomY }
      );
    });
  }

  if (topEdgePoints.length === 0) {
    const topHullPoint = hull.find(point => {
      const relX = point.x - centroid.x;
      const relY = point.y - centroid.y;
      const projection =
        relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);
      return Math.abs(projection - maxSecondary) < 0.001;
    });
    if (topHullPoint) topEdgePoints = [topHullPoint];
  }

  if (bottomEdgePoints.length === 0) {
    const bottomHullPoint = hull.find(point => {
      const relX = point.x - centroid.x;
      const relY = point.y - centroid.y;
      const projection =
        relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);
      return Math.abs(projection - minSecondary) < 0.001;
    });
    if (bottomHullPoint) bottomEdgePoints = [bottomHullPoint];
  }

  return { topEdge: topEdgePoints, bottomEdge: bottomEdgePoints };
};

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
