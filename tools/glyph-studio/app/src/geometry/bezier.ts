// Cubic bezier helpers in cap-unit space. All points {x,y}.
import type { Node, Stroke } from "../types";

export interface Pt {
  x: number;
  y: number;
}

export const lerp = (a: Pt, b: Pt, t: number): Pt => ({
  x: a.x + (b.x - a.x) * t,
  y: a.y + (b.y - a.y) * t,
});

// Effective control points for the segment between two nodes.
// LINE if neither a.hOut nor b.hIn exists; else cubic (missing handle
// defaults to its own anchor).
export function segmentControls(
  a: Node,
  b: Node,
): { p0: Pt; c1: Pt; c2: Pt; p3: Pt; isLine: boolean } {
  const isLine = !a.hOut && !b.hIn;
  const p0 = { x: a.x, y: a.y };
  const p3 = { x: b.x, y: b.y };
  const c1 = a.hOut ? { x: a.hOut.x, y: a.hOut.y } : p0;
  const c2 = b.hIn ? { x: b.hIn.x, y: b.hIn.y } : p3;
  return { p0, c1, c2, p3, isLine };
}

export function evalCubic(p0: Pt, c1: Pt, c2: Pt, p3: Pt, t: number): Pt {
  const u = 1 - t;
  const a = u * u * u;
  const b = 3 * u * u * t;
  const c = 3 * u * t * t;
  const d = t * t * t;
  return {
    x: a * p0.x + b * c1.x + c * c2.x + d * p3.x,
    y: a * p0.y + b * c1.y + c * c2.y + d * p3.y,
  };
}

// Split a cubic at t via De Casteljau. Returns the two sub-curves' controls
// and the split point.
export function splitCubic(
  p0: Pt,
  c1: Pt,
  c2: Pt,
  p3: Pt,
  t: number,
): {
  left: { p0: Pt; c1: Pt; c2: Pt; p3: Pt };
  right: { p0: Pt; c1: Pt; c2: Pt; p3: Pt };
  point: Pt;
} {
  const a = lerp(p0, c1, t);
  const b = lerp(c1, c2, t);
  const c = lerp(c2, p3, t);
  const d = lerp(a, b, t);
  const e = lerp(b, c, t);
  const f = lerp(d, e, t);
  return {
    left: { p0, c1: a, c2: d, p3: f },
    right: { p0: f, c1: e, c2: c, p3 },
    point: f,
  };
}

// Nearest t on a segment to a target point: sample 64, then 2 Newton-ish
// refinement steps by local bisection around the best sample.
export function nearestTOnSegment(a: Node, b: Node, target: Pt): number {
  const { p0, c1, c2, p3 } = segmentControls(a, b);
  const at = (t: number) => evalCubic(p0, c1, c2, p3, t);
  const dist2 = (t: number) => {
    const p = at(t);
    const dx = p.x - target.x;
    const dy = p.y - target.y;
    return dx * dx + dy * dy;
  };
  let bestT = 0;
  let bestD = Infinity;
  const N = 64;
  for (let i = 0; i <= N; i++) {
    const t = i / N;
    const d = dist2(t);
    if (d < bestD) {
      bestD = d;
      bestT = t;
    }
  }
  let lo = Math.max(0, bestT - 1 / N);
  let hi = Math.min(1, bestT + 1 / N);
  for (let iter = 0; iter < 2; iter++) {
    for (let i = 0; i <= 8; i++) {
      const t = lo + ((hi - lo) * i) / 8;
      const d = dist2(t);
      if (d < bestD) {
        bestD = d;
        bestT = t;
      }
    }
    lo = Math.max(0, bestT - (hi - lo) / 8);
    hi = Math.min(1, bestT + (hi - lo) / 8);
  }
  return bestT;
}

// Flatten a stroke into a polyline for SVG path 'd' (moveto + curves/lines).
export function strokePathD(stroke: Stroke): string {
  const n = stroke.nodes;
  if (n.length === 0) return "";
  let d = `M ${n[0].x} ${n[0].y}`;
  const segCount = stroke.closed ? n.length : n.length - 1;
  for (let i = 0; i < segCount; i++) {
    const a = n[i];
    const b = n[(i + 1) % n.length];
    const { c1, c2, p3, isLine } = segmentControls(a, b);
    if (isLine) d += ` L ${p3.x} ${p3.y}`;
    else d += ` C ${c1.x} ${c1.y} ${c2.x} ${c2.y} ${p3.x} ${p3.y}`;
  }
  if (stroke.closed) d += " Z";
  return d;
}

// Bounding box over stroke geometry (anchors + handles + sampled curves).
export function strokesBounds(
  strokes: Stroke[],
): { minX: number; minY: number; maxX: number; maxY: number } | null {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const acc = (p: Pt) => {
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  };
  for (const s of strokes) {
    const segCount = s.closed ? s.nodes.length : s.nodes.length - 1;
    for (let i = 0; i < segCount; i++) {
      const a = s.nodes[i];
      const b = s.nodes[(i + 1) % s.nodes.length];
      const { p0, c1, c2, p3 } = segmentControls(a, b);
      for (let k = 0; k <= 16; k++) acc(evalCubic(p0, c1, c2, p3, k / 16));
    }
    if (s.nodes.length === 1) acc(s.nodes[0]);
  }
  if (!isFinite(minX)) return null;
  return { minX, minY, maxX, maxY };
}
