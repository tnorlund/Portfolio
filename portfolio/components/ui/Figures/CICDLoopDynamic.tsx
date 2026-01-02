import React, { useMemo, useLayoutEffect, useRef, useState, useEffect } from "react";
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
  { label: "Plan", color: "#FFC107" },
  { label: "Code", color: "#4CAF50" },
  { label: "Build", color: "#4CAF50" },
  { label: "Test", color: "#2196F3" },
  { label: "Review", color: "#2196F3" },
  { label: "Deploy", color: "#F44336" },
  { label: "Monitor", color: "#F44336" },
];

/**
 * Lemniscate of Bernoulli - parametric figure-8 curve
 * t ranges from 0 to 2π
 */
function fig8Point(t: number, cx: number, cy: number, a: number, b: number): Pt {
  const s = Math.sin(t);
  const c = Math.cos(t);
  const denom = 1 + s * s;

  return {
    x: cx + (a * c) / denom,
    y: cy + (b * s * c) / denom,
  };
}

/**
 * Sample the figure-8 curve and compute cumulative arc lengths
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
  const pts: Pt[] = [];
  for (let i = 0; i <= samples; i++) {
    const t = (i / samples) * Math.PI * 2;
    pts.push(fig8Point(t, cx, cy, a, b));
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
 * Build a filled ribbon segment path with:
 * - Arrow tip at the end (triangle added)
 * - Notch at the start (triangle cut out using evenodd)
 */
function buildRibbonSegmentPath(
  center: Pt[],
  width: number,
  arrowLen: number,
  notchLen: number
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

  // Arrow tip at end
  const endI = center.length - 1;
  const end = center[endI];
  const { t: tend, n: nend } = tangentAndNormal(center, endI);
  const tip = { x: end.x + tend.x * arrowLen, y: end.y + tend.y * arrowLen };
  const endL = { x: end.x + nend.x * halfW, y: end.y + nend.y * halfW };
  const endR = { x: end.x - nend.x * halfW, y: end.y - nend.y * halfW };

  // Notch triangle hole at start
  const start = center[0];
  const { t: t0, n: n0 } = tangentAndNormal(center, 0);
  const notchApex = {
    x: start.x + t0.x * notchLen,
    y: start.y + t0.y * notchLen,
  };
  const notchL = { x: start.x + n0.x * halfW, y: start.y + n0.y * halfW };
  const notchR = { x: start.x - n0.x * halfW, y: start.y - n0.y * halfW };

  // Build outer polygon path (left edge -> arrow tip -> right edge back)
  const outer =
    `M ${left[0].x} ${left[0].y} ` +
    left
      .slice(1)
      .map((p) => `L ${p.x} ${p.y}`)
      .join(" ") +
    ` L ${endL.x} ${endL.y}` +
    ` L ${tip.x} ${tip.y}` +
    ` L ${endR.x} ${endR.y} ` +
    right
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
  const ribbonWidth = height * 0.12;
  const arrowLen = ribbonWidth * 0.6;
  const notchLen = ribbonWidth * 0.5;

  // Gap size between segments (as arc length)
  const gapArcLength = ribbonWidth * 0.4;

  // Generate segment geometries
  const segmentGeoms = useMemo(() => {
    const { pts, cum, total } = sampleCurve({ cx, cy, a, b, samples: 1400 });

    return Array.from({ length: N }, (_, i) => {
      // Create gaps between segments by shortening each segment
      const s0 = (i / N) * total + gapArcLength / 2;
      const s1 = ((i + 1) / N) * total - gapArcLength / 2;

      const centerPts = sliceByArcLength(pts, cum, s0, s1);

      // Use reversed path for text if the segment goes right-to-left
      const needsReverse = shouldReverseTextPath(centerPts);
      const textPathD = needsReverse
        ? polylinePathDReversed(centerPts)
        : polylinePathD(centerPts);

      const ribbonD = buildRibbonSegmentPath(
        centerPts,
        ribbonWidth,
        arrowLen,
        notchLen
      );
      return { textPathD, ribbonD };
    });
  }, [N, cx, cy, a, b, ribbonWidth, arrowLen, notchLen, gapArcLength]);

  const fontSize = height * 0.07;

  // Ref to measure text height
  const measureTextRef = useRef<SVGTextElement>(null);
  const [textDy, setTextDy] = useState(0);

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

  return (
    <div ref={containerRef}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ maxWidth: "100%", height: "auto" }}
      >
        <defs>
          {/* Define centerline paths for text to follow */}
          {segmentGeoms.map((g, i) => (
            <path key={i} id={`segc-${i}`} d={g.textPathD} fill="none" />
          ))}
        </defs>

        {/* Hidden text element to measure actual text height */}
        <text
          ref={measureTextRef}
          fontSize={fontSize}
          fontWeight="bold"
          fontStyle="italic"
          opacity={0}
          x={0}
          y={0}
        >
          Mg
        </text>

        {/* Render each segment with animation */}
        {segmentGeoms.map((g, i) => {
          const { label, color } = segments[i];

          return (
            <animated.g key={i} style={springs[i]}>
              {/* Ribbon with notch + arrow tip */}
              <path d={g.ribbonD} fill={color} fillRule="evenodd" />

              {/* Label along segment centerline */}
              <text
                fontSize={fontSize}
                fontWeight="bold"
                fontStyle="italic"
                fill="var(--background-color)"
                dy={textDy}
              >
                <textPath
                  href={`#segc-${i}`}
                  startOffset="50%"
                  textAnchor="middle"
                >
                  {label}
                </textPath>
              </text>
            </animated.g>
          );
        })}

      </svg>
    </div>
  );
};

export default CICDLoopDynamic;
