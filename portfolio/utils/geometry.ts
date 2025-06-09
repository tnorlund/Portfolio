export interface Point {
  x: number;
  y: number;
}

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
