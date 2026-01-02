import React, { useMemo, useLayoutEffect, useRef, useState, useEffect, useId } from "react";
import { animated, useSprings } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";

interface CICDLoopDynamicProps {
  /** Width of the component */
  width?: number;
  /** Height of the component */
  height?: number;
  /** Segment definitions */
  segments?: SegmentSpec[];
  /** Delay between segment animations in ms (default: 150) */
  staggerDelay?: number;
  /** Duration of one full loop animation in ms (default: 4000) */
  flowDuration?: number;
}

interface SegmentSpec {
  label: string;
  color: string;
}

type Pt = { x: number; y: number };

// Default CI/CD segments (in order around the figure-8)
// Plan -> Code -> Build -> Test -> Review -> Deploy -> Monitor
const DEFAULT_SEGMENTS: SegmentSpec[] = [
  { label: "Plan", color: "var(--color-yellow)" },
  { label: "Code", color: "var(--color-green)" },
  { label: "Build", color: "var(--color-green)" },
  { label: "Test", color: "var(--color-blue)" },
  { label: "Review", color: "var(--color-blue)" },
  { label: "Deploy", color: "var(--color-red)" },
  { label: "Monitor", color: "var(--color-yellow)" },
];

/**
 * The original figure-8 path from Adobe Illustrator, normalized to center (0,0).
 * This is the exact path exported from the reference design.
 */
const ILLUSTRATOR_PATH = "M331.65,460.21l-68.3-79.42c-22.88-26.61-63.46-29.88-89.47-6.32-11.73,10.63-19.58,25.8-20.1,43.92-.02.7-.03,1.4-.03,2.11h0c0,.71.01,1.41.03,2.11.52,18.12,8.37,33.29,20.1,43.92,26.01,23.56,66.59,20.29,89.47-6.32l68.3-79.42c22.88-26.61,63.46-29.88,89.47-6.32,11.73,10.63,19.58,25.8,20.1,43.92.02.7.03,1.4.03,2.11h0c0,.71-.01,1.41-.03,2.11-.52,18.12-8.37,33.29-20.1,43.92-26.01,23.56-66.59,20.29-89.47-6.32Z";

// Original path bounds (from Illustrator viewBox analysis)
const PATH_CENTER = { x: 331.65, y: 420.5 };
const PATH_WIDTH = 288; // approximate width
const PATH_HEIGHT = 172; // approximate height

/**
 * Evaluate a cubic Bezier curve at parameter t
 */
function cubicBezier(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): Pt {
  const mt = 1 - t;
  const mt2 = mt * mt;
  const mt3 = mt2 * mt;
  const t2 = t * t;
  const t3 = t2 * t;

  return {
    x: mt3 * p0.x + 3 * mt2 * t * p1.x + 3 * mt * t2 * p2.x + t3 * p3.x,
    y: mt3 * p0.y + 3 * mt2 * t * p1.y + 3 * mt * t2 * p2.y + t3 * p3.y,
  };
}

/**
 * Parse the Illustrator SVG path and return sampled points
 */
function parseAndSamplePath(samples: number): Pt[] {
  // Manually parsed path segments from the Illustrator export
  // The path structure: M -> l -> c -> c -> c -> h -> c -> c -> c -> l -> c -> c -> c -> h -> c -> c -> c -> Z

  const segments: { type: string; points: Pt[] }[] = [];
  let current: Pt = { x: 331.65, y: 460.21 }; // M331.65,460.21

  // l-68.3-79.42 (line to upper-left diagonal)
  const p1 = { x: current.x - 68.3, y: current.y - 79.42 };
  segments.push({ type: "L", points: [current, p1] });
  current = p1;

  // c-22.88-26.61-63.46-29.88-89.47-6.32 (left loop top curve)
  let cp1 = { x: current.x - 22.88, y: current.y - 26.61 };
  let cp2 = { x: current.x - 63.46, y: current.y - 29.88 };
  let end = { x: current.x - 89.47, y: current.y - 6.32 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // -11.73,10.63-19.58,25.8-20.1,43.92 (left loop left-top curve)
  cp1 = { x: current.x - 11.73, y: current.y + 10.63 };
  cp2 = { x: current.x - 19.58, y: current.y + 25.8 };
  end = { x: current.x - 20.1, y: current.y + 43.92 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // -.02.7-.03,1.4-.03,2.11 (tiny curve at left apex)
  cp1 = { x: current.x - 0.02, y: current.y + 0.7 };
  cp2 = { x: current.x - 0.03, y: current.y + 1.4 };
  end = { x: current.x - 0.03, y: current.y + 2.11 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // h0 (horizontal line of 0 length - skip)

  // c0,.71.01,1.41.03,2.11 (tiny curve continuing from apex)
  cp1 = { x: current.x, y: current.y + 0.71 };
  cp2 = { x: current.x + 0.01, y: current.y + 1.41 };
  end = { x: current.x + 0.03, y: current.y + 2.11 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // .52,18.12,8.37,33.29,20.1,43.92 (left loop left-bottom curve)
  cp1 = { x: current.x + 0.52, y: current.y + 18.12 };
  cp2 = { x: current.x + 8.37, y: current.y + 33.29 };
  end = { x: current.x + 20.1, y: current.y + 43.92 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // 26.01,23.56,66.59,20.29,89.47-6.32 (left loop bottom curve)
  cp1 = { x: current.x + 26.01, y: current.y + 23.56 };
  cp2 = { x: current.x + 66.59, y: current.y + 20.29 };
  end = { x: current.x + 89.47, y: current.y - 6.32 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // l68.3-79.42 (line to upper-right diagonal)
  const p2 = { x: current.x + 68.3, y: current.y - 79.42 };
  segments.push({ type: "L", points: [current, p2] });
  current = p2;

  // c22.88-26.61,63.46-29.88,89.47-6.32 (right loop top curve)
  cp1 = { x: current.x + 22.88, y: current.y - 26.61 };
  cp2 = { x: current.x + 63.46, y: current.y - 29.88 };
  end = { x: current.x + 89.47, y: current.y - 6.32 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // 11.73,10.63,19.58,25.8,20.1,43.92 (right loop right-top curve)
  cp1 = { x: current.x + 11.73, y: current.y + 10.63 };
  cp2 = { x: current.x + 19.58, y: current.y + 25.8 };
  end = { x: current.x + 20.1, y: current.y + 43.92 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // .02.7.03,1.4.03,2.11 (tiny curve at right apex)
  cp1 = { x: current.x + 0.02, y: current.y + 0.7 };
  cp2 = { x: current.x + 0.03, y: current.y + 1.4 };
  end = { x: current.x + 0.03, y: current.y + 2.11 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // h0 (horizontal line of 0 length - skip)

  // c0,.71-.01,1.41-.03,2.11 (tiny curve continuing from apex)
  cp1 = { x: current.x, y: current.y + 0.71 };
  cp2 = { x: current.x - 0.01, y: current.y + 1.41 };
  end = { x: current.x - 0.03, y: current.y + 2.11 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // -.52,18.12-8.37,33.29-20.1,43.92 (right loop right-bottom curve)
  cp1 = { x: current.x - 0.52, y: current.y + 18.12 };
  cp2 = { x: current.x - 8.37, y: current.y + 33.29 };
  end = { x: current.x - 20.1, y: current.y + 43.92 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });
  current = end;

  // -26.01,23.56-66.59,20.29-89.47-6.32 (right loop bottom curve back to start)
  cp1 = { x: current.x - 26.01, y: current.y + 23.56 };
  cp2 = { x: current.x - 66.59, y: current.y + 20.29 };
  end = { x: current.x - 89.47, y: current.y - 6.32 };
  segments.push({ type: "C", points: [current, cp1, cp2, end] });

  // Sample points along all segments
  const pts: Pt[] = [];
  const samplesPerSegment = Math.ceil(samples / segments.length);

  for (const seg of segments) {
    if (seg.type === "L") {
      // Line segment
      for (let i = 0; i < samplesPerSegment; i++) {
        const t = i / samplesPerSegment;
        pts.push({
          x: seg.points[0].x + t * (seg.points[1].x - seg.points[0].x),
          y: seg.points[0].y + t * (seg.points[1].y - seg.points[0].y),
        });
      }
    } else if (seg.type === "C") {
      // Cubic Bezier
      for (let i = 0; i < samplesPerSegment; i++) {
        const t = i / samplesPerSegment;
        pts.push(cubicBezier(seg.points[0], seg.points[1], seg.points[2], seg.points[3], t));
      }
    }
  }

  return pts;
}

/**
 * Get a point on the figure-8 path, scaled and centered for the component.
 * This uses the exact path from the Illustrator export.
 */
function fig8Point(t: number, cx: number, cy: number, a: number, b: number): Pt {
  // We'll compute this in sampleCurve instead using the parsed path
  // This function is kept for API compatibility but won't be used directly
  return { x: cx, y: cy };
}

/**
 * Sample the figure-8 curve and compute cumulative arc lengths.
 * Uses the exact path from the Illustrator export, scaled to fit the component.
 */
function sampleCurve({
  cx,
  cy,
  a,
  b,
  samples = 1200,
}: {
  cx: number;
  cy: number;
  a: number;
  b: number;
  samples?: number;
}) {
  // Get raw points from the Illustrator path
  const rawPts = parseAndSamplePath(samples);

  // Calculate the bounds of the raw path
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const pt of rawPts) {
    minX = Math.min(minX, pt.x);
    maxX = Math.max(maxX, pt.x);
    minY = Math.min(minY, pt.y);
    maxY = Math.max(maxY, pt.y);
  }

  const rawWidth = maxX - minX;
  const rawHeight = maxY - minY;
  const rawCx = (minX + maxX) / 2;
  const rawCy = (minY + maxY) / 2;

  // Scale to fit within the target dimensions (a = half-width, b = half-height)
  const scaleX = (a * 2) / rawWidth;
  const scaleY = (b * 2) / rawHeight;
  const scale = Math.min(scaleX, scaleY) * 0.95; // 95% to leave some margin

  // Transform points to the target coordinate system
  let pts: Pt[] = rawPts.map(pt => ({
    x: cx + (pt.x - rawCx) * scale,
    y: cy + (pt.y - rawCy) * scale,
  }));

  // Rotate the path starting point to align Test/Review gap under Plan
  const rotateBy = Math.floor(pts.length * 0.01); // 1% backward
  if (rotateBy > 0) {
    pts = [...pts.slice(-rotateBy), ...pts.slice(0, -rotateBy)];
  }

  // Compute cumulative arc length
  const cum: number[] = [0];
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i].x - pts[i - 1].x;
    const dy = pts[i].y - pts[i - 1].y;
    cum.push(cum[i - 1] + Math.hypot(dx, dy));
  }

  return { pts, cum, total: cum[cum.length - 1] };
}

/**
 * Slice points between arc-length s0 and s1
 */
function sliceByArcLength(
  pts: Pt[],
  cum: number[],
  s0: number,
  s1: number
): Pt[] {
  const out: Pt[] = [];

  const lerpPt = (i0: number, i1: number, t: number): Pt => ({
    x: pts[i0].x + (pts[i1].x - pts[i0].x) * t,
    y: pts[i0].y + (pts[i1].y - pts[i0].y) * t,
  });

  for (let i = 1; i < pts.length; i++) {
    const a = cum[i - 1];
    const b = cum[i];

    if (b < s0) continue;
    if (a > s1) break;

    // Add interpolated start point
    if (a <= s0 && b >= s0) {
      const t = (s0 - a) / (b - a || 1);
      out.push(lerpPt(i - 1, i, t));
    }

    // Add point if within range
    if (a >= s0 && b <= s1) {
      out.push(pts[i]);
    }

    // Add interpolated end point
    if (a <= s1 && b >= s1) {
      const t = (s1 - a) / (b - a || 1);
      out.push(lerpPt(i - 1, i, t));
      break;
    }
  }

  return out;
}

/**
 * Normalize a vector
 */
function unit(vx: number, vy: number): Pt {
  const m = Math.hypot(vx, vy) || 1;
  return { x: vx / m, y: vy / m };
}

/**
 * Get tangent and normal at a point in the samples array
 */
function tangentAndNormal(
  samples: Pt[],
  i: number
): { t: Pt; n: Pt } {
  const p0 = samples[Math.max(0, i - 1)];
  const p1 = samples[Math.min(samples.length - 1, i + 1)];
  const t = unit(p1.x - p0.x, p1.y - p0.y);
  const n = { x: -t.y, y: t.x };
  return { t, n };
}

/**
 * Get point and tangent at a specific arc-length position
 */
function getPointAndTangentAtArcLength(
  pts: Pt[],
  cum: number[],
  s: number
): { pt: Pt; t: Pt; n: Pt } {
  // Handle edge cases
  if (s <= 0) {
    const t = unit(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
    return { pt: pts[0], t, n: { x: -t.y, y: t.x } };
  }
  if (s >= cum[cum.length - 1]) {
    const last = pts.length - 1;
    const t = unit(pts[last].x - pts[last - 1].x, pts[last].y - pts[last - 1].y);
    return { pt: pts[last], t, n: { x: -t.y, y: t.x } };
  }

  // Find the segment containing arc-length s
  for (let i = 1; i < pts.length; i++) {
    if (cum[i] >= s) {
      const a = cum[i - 1];
      const b = cum[i];
      const frac = (s - a) / (b - a || 1);

      // Interpolate point
      const pt = {
        x: pts[i - 1].x + frac * (pts[i].x - pts[i - 1].x),
        y: pts[i - 1].y + frac * (pts[i].y - pts[i - 1].y),
      };

      // Tangent from the segment direction
      const t = unit(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
      const n = { x: -t.y, y: t.x };

      return { pt, t, n };
    }
  }

  // Fallback (shouldn't reach here)
  const last = pts.length - 1;
  const t = unit(pts[last].x - pts[last - 1].x, pts[last].y - pts[last - 1].y);
  return { pt: pts[last], t, n: { x: -t.y, y: t.x } };
}

/**
 * Build a filled ribbon segment path with:
 * - Arrow tip at the end (triangle added)
 * - Notch at the start (triangle cut out using evenodd)
 *
 * @param startGap - Gap geometry for the start (notch): position, tangent, normal, and half-width of gap
 * @param endGap - Gap geometry for the end (arrow): position, tangent, normal, and half-width of gap
 */
function buildRibbonSegmentPath(
  center: Pt[],
  width: number,
  arrowLen: number,
  notchLen: number,
  startGap?: { pt: Pt; t: Pt; n: Pt; halfGap: number },
  endGap?: { pt: Pt; t: Pt; n: Pt; halfGap: number }
): string {
  if (center.length < 2) return "";

  const halfW = width / 2;

  const left: Pt[] = [];
  const right: Pt[] = [];

  // Offset points by normal to create ribbon edges
  for (let i = 0; i < center.length; i++) {
    const { n } = tangentAndNormal(center, i);
    const p = center[i];
    left.push({ x: p.x + n.x * halfW, y: p.y + n.y * halfW });
    right.push({ x: p.x - n.x * halfW, y: p.y - n.y * halfW });
  }

  // Arrow geometry at end
  const endI = center.length - 1;
  const end = center[endI];
  const localEnd = tangentAndNormal(center, endI);

  let tip: Pt, endL: Pt, endR: Pt;

  if (endGap) {
    // Use gap geometry for uniform spacing
    // Arrow base is at: gapCenter - tangent * halfGap (back edge of gap)
    const arrowBase = {
      x: endGap.pt.x - endGap.t.x * endGap.halfGap,
      y: endGap.pt.y - endGap.t.y * endGap.halfGap,
    };
    // Arrow tip extends from base
    tip = {
      x: arrowBase.x + endGap.t.x * arrowLen,
      y: arrowBase.y + endGap.t.y * arrowLen,
    };
    // Base corners use gap normal for uniform width
    endL = { x: arrowBase.x + endGap.n.x * halfW, y: arrowBase.y + endGap.n.y * halfW };
    endR = { x: arrowBase.x - endGap.n.x * halfW, y: arrowBase.y - endGap.n.y * halfW };
  } else {
    // Fallback to local geometry
    tip = { x: end.x + localEnd.t.x * arrowLen, y: end.y + localEnd.t.y * arrowLen };
    endL = { x: end.x + localEnd.n.x * halfW, y: end.y + localEnd.n.y * halfW };
    endR = { x: end.x - localEnd.n.x * halfW, y: end.y - localEnd.n.y * halfW };
  }

  // Notch geometry at start
  const start = center[0];
  const localStart = tangentAndNormal(center, 0);

  let notchApex: Pt, notchL: Pt, notchR: Pt;

  if (startGap) {
    // Use gap geometry for uniform spacing
    // Notch base is at: gapCenter + tangent * halfGap (front edge of gap)
    const notchBase = {
      x: startGap.pt.x + startGap.t.x * startGap.halfGap,
      y: startGap.pt.y + startGap.t.y * startGap.halfGap,
    };
    // Notch apex cuts into segment from base
    notchApex = {
      x: notchBase.x + startGap.t.x * notchLen,
      y: notchBase.y + startGap.t.y * notchLen,
    };
    // Base corners use gap normal for uniform width
    notchL = { x: notchBase.x + startGap.n.x * halfW, y: notchBase.y + startGap.n.y * halfW };
    notchR = { x: notchBase.x - startGap.n.x * halfW, y: notchBase.y - startGap.n.y * halfW };
  } else {
    // Fallback to local geometry
    notchApex = {
      x: start.x + localStart.t.x * notchLen,
      y: start.y + localStart.t.y * notchLen,
    };
    notchL = { x: start.x + localStart.n.x * halfW, y: start.y + localStart.n.y * halfW };
    notchR = { x: start.x - localStart.n.x * halfW, y: start.y - localStart.n.y * halfW };
  }

  // Replace edge endpoints with arrow/notch corners to avoid discontinuities on curves
  // This ensures the ribbon body connects smoothly to the arrow/notch geometry
  const leftEdge = [...left];
  const rightEdge = [...right];

  // Replace start points with notch corners (if using gap geometry)
  if (startGap) {
    leftEdge[0] = notchL;
    rightEdge[0] = notchR;
  }

  // Replace end points with arrow corners (if using gap geometry)
  if (endGap) {
    leftEdge[leftEdge.length - 1] = endL;
    rightEdge[rightEdge.length - 1] = endR;
  }

  // Build outer polygon path (left edge -> arrow tip -> right edge back)
  const outer =
    `M ${leftEdge[0].x} ${leftEdge[0].y} ` +
    leftEdge
      .slice(1)
      .map((p) => `L ${p.x} ${p.y}`)
      .join(" ") +
    ` L ${tip.x} ${tip.y} ` +
    rightEdge
      .slice(0, -1)
      .reverse()
      .map((p) => `L ${p.x} ${p.y}`)
      .join(" ") +
    ` Z`;

  // Build notch hole as a triangle subpath (evenodd will subtract it)
  const hole =
    `M ${notchL.x} ${notchL.y}` +
    ` L ${notchApex.x} ${notchApex.y}` +
    ` L ${notchR.x} ${notchR.y}` +
    ` Z`;

  return `${outer} ${hole}`;
}

/**
 * Create a simple polyline path for text to follow
 */
function polylinePathD(points: Pt[]): string {
  if (!points.length) return "";
  return (
    `M ${points[0].x} ${points[0].y} ` +
    points
      .slice(1)
      .map((p) => `L ${p.x} ${p.y}`)
      .join(" ")
  );
}

/**
 * Check if text on this path would render upside-down
 * (i.e., the path goes predominantly right-to-left)
 */
function shouldReverseTextPath(points: Pt[]): boolean {
  if (points.length < 2) return false;
  // Compare start and end x coordinates
  const startX = points[0].x;
  const endX = points[points.length - 1].x;
  // If path goes right-to-left, text will be upside down
  return endX < startX;
}

/**
 * Create a reversed polyline path for text (to keep text right-side up)
 */
function polylinePathDReversed(points: Pt[]): string {
  if (!points.length) return "";
  const reversed = [...points].reverse();
  return (
    `M ${reversed[0].x} ${reversed[0].y} ` +
    reversed
      .slice(1)
      .map((p) => `L ${p.x} ${p.y}`)
      .join(" ")
  );
}

const CICDLoopDynamic: React.FC<CICDLoopDynamicProps> = ({
  width = 600,
  height = 300,
  segments = DEFAULT_SEGMENTS,
  staggerDelay = 150,
  flowDuration = 4000,
}) => {
  // Unique ID for this component instance (for SVG path IDs)
  const instanceId = useId();

  // Animation hooks
  const [containerRef, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: false,
  });
  const [mounted, setMounted] = useState(false);
  const timeoutIds = useRef<NodeJS.Timeout[]>([]);
  const pulseIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const pulseTimeoutsRef = useRef<NodeJS.Timeout[]>([]);

  const N = segments.length;

  // Animation springs for each segment
  const [springs, api] = useSprings(
    N,
    () => ({
      opacity: 0,
      transform: "scale(0.9)",
      config: { tension: 120, friction: 14 },
    }),
    [N]
  );

  useEffect(() => {
    setMounted(true);
  }, []);

  // Clear all pending timeouts
  const clearAllTimeouts = () => {
    timeoutIds.current.forEach((id) => clearTimeout(id));
    timeoutIds.current = [];
  };

  // Clear pulse animation refs
  const clearPulseAnimation = () => {
    if (pulseIntervalRef.current) {
      clearInterval(pulseIntervalRef.current);
      pulseIntervalRef.current = null;
    }
    pulseTimeoutsRef.current.forEach((id) => clearTimeout(id));
    pulseTimeoutsRef.current = [];
  };

  // Trigger animations when in view
  useEffect(() => {
    if (!mounted) return;

    if (inView) {
      clearAllTimeouts();

      // Staggered fade-in for each segment
      segments.forEach((_, index) => {
        const id = setTimeout(() => {
          api.start((i) => {
            if (i === index) {
              return {
                opacity: 1,
                transform: "scale(1)",
                config: { tension: 120, friction: 14 },
              };
            }
            return false;
          });
        }, index * staggerDelay);
        timeoutIds.current.push(id);
      });
    } else {
      clearAllTimeouts();
      clearPulseAnimation();
      // Reset when out of view
      api.start(() => ({
        opacity: 0,
        transform: "scale(0.9)",
        immediate: true,
      }));
    }

    return () => clearAllTimeouts();
  }, [inView, mounted, staggerDelay, api, segments]);

  // Continuous pulsing animation after all segments have animated in
  useEffect(() => {
    if (!inView || !mounted) {
      clearPulseAnimation();
      return;
    }

    // Wait for all sections to finish initial animation
    const totalAnimationTime = segments.length * staggerDelay + 600 + 1000;

    const continuousAnimationTimeout = setTimeout(() => {
      // Start continuous pulsing animation loop
      const startPulse = () => {
        // Clear previous pulse timeouts before starting new ones
        pulseTimeoutsRef.current.forEach((id) => clearTimeout(id));
        pulseTimeoutsRef.current = [];

        segments.forEach((_, index) => {
          // Stagger the pulse for each section
          const pulseTimeout = setTimeout(() => {
            api.start((i) => {
              if (i === index) {
                return {
                  opacity: 1,
                  transform: "scale(1.08)",
                  config: { tension: 300, friction: 25 },
                };
              }
              return false;
            });

            // Return to normal after pulse
            const returnTimeout = setTimeout(() => {
              api.start((i) => {
                if (i === index) {
                  return {
                    opacity: 1,
                    transform: "scale(1)",
                    config: { tension: 300, friction: 25 },
                  };
                }
                return false;
              });
            }, 400);
            pulseTimeoutsRef.current.push(returnTimeout);
          }, index * 100); // Stagger pulses
          pulseTimeoutsRef.current.push(pulseTimeout);
        });
      };

      // Start first pulse immediately, then repeat
      startPulse();
      pulseIntervalRef.current = setInterval(startPulse, flowDuration);
    }, totalAnimationTime);

    timeoutIds.current.push(continuousAnimationTimeout);

    return () => {
      clearTimeout(continuousAnimationTimeout);
      clearPulseAnimation();
    };
  }, [inView, mounted, staggerDelay, flowDuration, api, segments]);

  const cx = width / 2;
  const cy = height / 2;
  const a = width * 0.42; // Horizontal scale
  const b = height * 0.38; // Vertical scale
  const ribbonWidth = height * 0.18;
  const arrowLen = ribbonWidth * 0.6;
  const notchLen = ribbonWidth * 0.5;

  // Gap size between segments (as arc length)
  const gapArcLength = ribbonWidth * 0.4;

  // Generate segment geometries
  const segmentGeoms = useMemo(() => {
    const { pts, cum, total } = sampleCurve({ cx, cy, a, b, samples: 1400 });

    // Define the gap width as a straight-line distance (not arc-length)
    // This ensures uniform gap width on both inner and outer edges of curves
    const gapWidth = ribbonWidth * 0.4;
    const halfGap = gapWidth / 2;

    // Calculate gap geometry at each segment boundary
    // Gap i is between segment i-1 and segment i (gap 0 is between segment N-1 and segment 0)
    const gapGeoms: { pt: Pt; t: Pt; n: Pt; halfGap: number }[] = [];
    for (let i = 0; i < N; i++) {
      // Gap position is at the boundary between segments
      const gapCenter = (i / N) * total;
      const { pt, t, n } = getPointAndTangentAtArcLength(pts, cum, gapCenter);
      gapGeoms.push({ pt, t, n, halfGap });
    }

    return Array.from({ length: N }, (_, i) => {
      // Segment centerline still uses arc-length for sampling
      // but the actual gap geometry uses fixed-width positioning
      const s0 = (i / N) * total + gapArcLength / 2;
      const s1 = ((i + 1) / N) * total - gapArcLength / 2;
      const segmentLength = s1 - s0;

      const centerPts = sliceByArcLength(pts, cum, s0, s1);

      // Use reversed path for text if the segment goes right-to-left
      const needsReverse = shouldReverseTextPath(centerPts);
      const textPathD = needsReverse
        ? polylinePathDReversed(centerPts)
        : polylinePathD(centerPts);

      // Get the gap geometry for uniform spacing:
      // - startGap: gap at the start of this segment (gap i)
      // - endGap: gap at the end of this segment (gap i+1, wrapping around)
      const startGap = gapGeoms[i];
      const endGap = gapGeoms[(i + 1) % N];

      const ribbonD = buildRibbonSegmentPath(
        centerPts,
        ribbonWidth,
        arrowLen,
        notchLen,
        startGap,
        endGap
      );

      // Calculate text X offset to center on visible ribbon body
      // The notch apex is at `notchLen` from the path start
      // The arrow base is at the path end (arrow tip extends beyond)
      // Center the text between notch apex and arrow base:
      //   Visual body: from notchLen to pathLength
      //   Center: (notchLen + pathLength) / 2 = pathLength/2 + notchLen/2
      //   As percentage: 50% + (notchLen / (2 * pathLength)) * 100%
      // For reversed paths, flip the direction
      const offsetAdjustment = (notchLen / (2 * segmentLength)) * 100;
      const textStartOffset = needsReverse
        ? 50 - offsetAdjustment
        : 50 + offsetAdjustment;

      return { textPathD, ribbonD, textStartOffset };
    });
  }, [N, cx, cy, a, b, ribbonWidth, arrowLen, notchLen, gapArcLength]);

  const fontSize = height * 0.11;

  // Ref to measure text height
  const measureTextRef = useRef<SVGTextElement>(null);
  const [textDy, setTextDy] = useState(0);

  // Refs for overlap detection
  const svgRef = useRef<SVGSVGElement>(null);
  const ribbonRefs = useRef<(SVGPathElement | null)[]>([]);
  const textRefs = useRef<(SVGTextElement | null)[]>([]);

  // State for text offset adjustments due to overlap
  const [textOffsetAdjustments, setTextOffsetAdjustments] = useState<number[]>([]);

  // Find the "Plan" segment index
  const planIndex = useMemo(() => {
    const idx = segments.findIndex(s => s.label === "Plan");
    return idx >= 0 ? idx : 0;
  }, [segments]);

  // Measure actual text height and calculate centering offset
  useLayoutEffect(() => {
    if (measureTextRef.current) {
      const bbox = measureTextRef.current.getBBox();
      // bbox.y = distance from baseline to top of text (negative, since above baseline)
      // bbox.height = total height of text
      // Center of text relative to baseline = bbox.y + bbox.height / 2
      // To center text ON the path, we offset by the negative of that
      const centerOffset = -(bbox.y + bbox.height / 2);
      setTextDy(centerOffset);
    }
  }, [fontSize]);

  // Detect overlap between Plan ribbon and other segments' text
  // Wait for all segments to animate in before checking
  useEffect(() => {
    if (!svgRef.current || !mounted || !inView) return;

    // Wait for all segment animations to complete
    const animationCompleteDelay = segments.length * staggerDelay + 500;

    const timeoutId = setTimeout(() => {
      const planRibbon = ribbonRefs.current[planIndex];
      if (!planRibbon) return;

      const planBBox = planRibbon.getBBox();

      // Shrink the Plan bounding box to a tighter region (center 50%)
      // This avoids false positives from the diagonal ribbon's rectangular bbox
      const shrinkFactor = 0.5;
      const tightPlanBBox = {
        x: planBBox.x + planBBox.width * (1 - shrinkFactor) / 2,
        y: planBBox.y + planBBox.height * (1 - shrinkFactor) / 2,
        width: planBBox.width * shrinkFactor,
        height: planBBox.height * shrinkFactor,
      };

      // Check each text element for overlap with Plan ribbon
      const adjustments: number[] = new Array(segments.length).fill(0);

      textRefs.current.forEach((textEl, i) => {
        if (i === planIndex || !textEl) return;

        const textBBox = textEl.getBBox();

        // Calculate text center point
        const textCenterX = textBBox.x + textBBox.width / 2;
        const textCenterY = textBBox.y + textBBox.height / 2;

        // Check if text center is inside the tightened Plan region
        const textCenterInsidePlan =
          textCenterX > tightPlanBBox.x &&
          textCenterX < tightPlanBBox.x + tightPlanBBox.width &&
          textCenterY > tightPlanBBox.y &&
          textCenterY < tightPlanBBox.y + tightPlanBBox.height;

        if (textCenterInsidePlan) {
          // Calculate how much to shift the text
          const planCenterX = planBBox.x + planBBox.width / 2;

          // Shift text in the direction away from Plan's center
          const shiftAmount = textCenterX < planCenterX ? -15 : 15;
          adjustments[i] = shiftAmount;
        }
      });

      setTextOffsetAdjustments(adjustments);
    }, animationCompleteDelay);

    return () => clearTimeout(timeoutId);
  }, [mounted, inView, planIndex, segments, staggerDelay, segmentGeoms, textDy]);

  return (
    <div ref={containerRef}>
      <svg
        ref={svgRef}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ maxWidth: "100%", height: "auto" }}
      >
        <defs>
          {/* Define centerline paths for text to follow */}
          {segmentGeoms.map((g, i) => (
            <path key={i} id={`segc-${instanceId}-${i}`} d={g.textPathD} fill="none" />
          ))}
        </defs>

        {/* Hidden text element to measure actual text height */}
        <text
          ref={measureTextRef}
          style={{
            fontSize: `${fontSize}px`,
            fontWeight: "bold",
            fontStyle: "italic",
          }}
          opacity={0}
          x={0}
          y={0}
        >
          Mg
        </text>

        {/* Render segments in two passes: background first, then "Plan" on top */}
        {(() => {
          // Render order: all segments except Plan, then Plan last
          const renderOrder = [
            ...segments.map((_, i) => i).filter(i => i !== planIndex),
            planIndex,
          ];

          return renderOrder.map((i) => {
            const g = segmentGeoms[i];
            const { label, color } = segments[i];

            // Apply overlap adjustment to text offset
            const adjustment = textOffsetAdjustments[i] || 0;
            const finalOffset = g.textStartOffset + adjustment;

            return (
              <animated.g key={i} style={springs[i]}>
                {/* Ribbon with notch + arrow tip */}
                <path
                  ref={(el) => { ribbonRefs.current[i] = el; }}
                  d={g.ribbonD}
                  fill={color}
                  fillRule="evenodd"
                />

                {/* Label along segment centerline */}
                <text
                  ref={(el) => { textRefs.current[i] = el; }}
                  style={{
                    fontSize: `${fontSize}px`,
                    fontWeight: "bold",
                    fontStyle: "italic",
                    fill: "var(--background-color)",
                  }}
                  dy={textDy}
                >
                  <textPath
                    href={`#segc-${instanceId}-${i}`}
                    startOffset={`${finalOffset}%`}
                    textAnchor="middle"
                  >
                    {label}
                  </textPath>
                </text>
              </animated.g>
            );
          });
        })()}

      </svg>
    </div>
  );
};

export default CICDLoopDynamic;
