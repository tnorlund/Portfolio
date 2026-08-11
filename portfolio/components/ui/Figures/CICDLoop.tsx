import React, { useMemo, useLayoutEffect, useRef, useState, useEffect, useId } from "react";
import { animated, useSprings } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import useOptimizedInView from "../../../hooks/useOptimizedInView";

interface CICDLoopProps {
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

// Default CI/CD segments, in flow order around the figure-8:
// Plan -> Code -> Build -> Test -> Release -> Deploy -> Operate -> Monitor.
// The order must match SEGMENT_GEOMETRY, which carries the shape for each slot.
const DEFAULT_SEGMENTS: SegmentSpec[] = [
  { label: "Plan", color: "var(--color-yellow)" },
  { label: "Code", color: "var(--color-green)" },
  { label: "Build", color: "var(--color-green)" },
  { label: "Test", color: "var(--color-blue)" },
  { label: "Release", color: "var(--color-blue)" },
  { label: "Deploy", color: "var(--color-red)" },
  { label: "Operate", color: "var(--color-red)" },
  { label: "Monitor", color: "var(--color-yellow)" },
];

// Static geometry extracted from the reference Illustrator design (CICD_loop.ai),
// scaled into a 600x300 base coordinate space. Each entry carries:
// - ribbonD: the exact ribbon outline (arrow tip + chevron notch as authored),
// - textPts: a polyline along the ribbon's centerline for the label textPath,
// - center: the ribbon's bounding-box center, used as the pulse transform origin.
// The two center-crossing transitions (Test -> Release, Monitor -> Plan) are
// butt joints hidden under the crossing bands, exactly as in the reference.
const BASE_WIDTH = 600;
const BASE_HEIGHT = 300;

const SEGMENT_GEOMETRY: {
  ribbonD: string;
  textPts: Pt[];
  /** startOffset (%) centering the label on the visible ribbon body */
  textOffset: number;
  center: Pt;
}[] = [
  {
    // Plan
    ribbonD:
      "M190.9 79.1 L192.8 94.6 L317.2 195.6 L314.2 160.2 L347.9 157.3 L222.3 55.3 L188.4 59.6 Z M207.9 57.1 L188.4 59.6 L190.9 79.1 L197.0 70.8 Z",
    textPts: [
      { x: 161.3, y: 47.8 }, { x: 172.6, y: 48.8 }, { x: 183.7, y: 50.9 }, { x: 194.5, y: 54.4 }, { x: 204.8, y: 59.0 }, { x: 214.6, y: 64.7 }, { x: 223.6, y: 71.5 }, { x: 231.9, y: 79.2 }, { x: 239.2, y: 87.8 }, { x: 246.1, y: 96.8 }, { x: 254.2, y: 104.7 }, { x: 262.3, y: 112.7 }, { x: 270.3, y: 120.6 }, { x: 278.4, y: 128.6 }, { x: 286.5, y: 136.5 }, { x: 294.6, y: 144.5 }, { x: 302.6, y: 152.4 }, { x: 310.7, y: 160.3 }, { x: 318.8, y: 168.3 }, { x: 326.9, y: 176.2 }, { x: 334.9, y: 184.2 }, { x: 343.0, y: 192.1 }, { x: 345.7, y: 194.8 },
    ],
    textOffset: 53.1,
    center: { x: 268.1, y: 125.4 },
  },
  {
    // Code
    ribbonD:
      "M59.4 109.9 L74.0 107.2 C88.3 85.7 112.2 72.6 138.5 72.6 C153.4 72.6 167.7 76.9 180.2 84.9 L175.7 49.8 L209.6 45.5 C188.5 31.2 164.1 23.5 138.5 23.5 C94.8 23.5 55.0 45.7 31.9 82.1 L37.8 114.0 Z",
    textPts: [
      { x: 56.8, y: 149.3 }, { x: 57.4, y: 137.9 }, { x: 59.3, y: 126.8 }, { x: 62.4, y: 115.9 }, { x: 66.8, y: 105.4 }, { x: 72.2, y: 95.5 }, { x: 78.8, y: 86.3 }, { x: 86.3, y: 77.8 }, { x: 94.7, y: 70.2 }, { x: 103.9, y: 63.6 }, { x: 113.8, y: 58.1 }, { x: 124.2, y: 53.7 }, { x: 135.0, y: 50.5 }, { x: 146.2, y: 48.5 }, { x: 157.5, y: 47.8 }, { x: 168.8, y: 48.3 }, { x: 180.0, y: 50.1 }, { x: 190.9, y: 53.1 }, { x: 201.4, y: 57.3 }, { x: 211.4, y: 62.7 }, { x: 217.7, y: 66.8 },
    ],
    textOffset: 47.5,
    center: { x: 120.8, y: 68.8 },
  },
  {
    // Build
    ribbonD:
      "M107.2 221.5 C77.6 211.5 61.1 184.0 61.1 150.0 C61.1 138.8 62.9 129.3 67.0 120.0 L28.8 127.2 L23.3 97.9 C16.1 113.8 12.0 131.4 12.0 150.0 C12.0 202.7 44.4 247.9 90.3 266.9 L104.1 261.4 L112.4 234.5 Z M112.4 234.5 L104.1 261.4 L120.5 254.8 Z",
    textPts: [
      { x: 153.4, y: 250.9 }, { x: 142.2, y: 249.7 }, { x: 131.1, y: 247.3 }, { x: 120.4, y: 243.7 }, { x: 110.1, y: 238.8 }, { x: 100.5, y: 232.9 }, { x: 91.6, y: 225.9 }, { x: 83.5, y: 218.0 }, { x: 76.3, y: 209.3 }, { x: 70.1, y: 199.8 }, { x: 65.1, y: 189.7 }, { x: 61.2, y: 179.0 }, { x: 58.5, y: 168.0 }, { x: 57.0, y: 156.8 }, { x: 56.9, y: 145.5 }, { x: 57.9, y: 134.2 }, { x: 60.2, y: 123.1 }, { x: 63.7, y: 112.4 }, { x: 68.5, y: 102.1 }, { x: 74.3, y: 92.4 }, { x: 81.2, y: 83.4 },
    ],
    textOffset: 50.0,
    center: { x: 62.2, y: 182.4 },
  },
  {
    // Test
    ribbonD:
      "M273.0 170.1 L239.0 167.9 L184.0 212.6 C170.7 222.3 155.0 227.4 138.5 227.4 C132.5 227.4 126.7 226.7 120.9 225.4 L135.2 261.1 L107.0 272.4 C117.2 275.1 127.8 276.5 138.5 276.5 C165.8 276.5 191.7 267.9 213.6 251.7 L270.7 205.4 Z",
    textPts: [
      { x: 302.9, y: 147.0 }, { x: 294.8, y: 154.9 }, { x: 286.7, y: 162.8 }, { x: 278.6, y: 170.8 }, { x: 270.5, y: 178.7 }, { x: 262.4, y: 186.6 }, { x: 254.3, y: 194.5 }, { x: 246.2, y: 202.4 }, { x: 239.1, y: 211.2 }, { x: 231.7, y: 219.8 }, { x: 223.4, y: 227.5 }, { x: 214.3, y: 234.3 }, { x: 204.5, y: 240.0 }, { x: 194.2, y: 244.6 }, { x: 183.4, y: 247.9 }, { x: 172.3, y: 250.1 }, { x: 161.0, y: 251.0 }, { x: 149.6, y: 250.7 }, { x: 138.4, y: 249.1 }, { x: 127.5, y: 246.2 }, { x: 116.9, y: 242.2 }, { x: 106.8, y: 237.0 }, { x: 97.4, y: 230.7 }, { x: 91.6, y: 225.9 },
    ],
    textOffset: 48.5,
    center: { x: 190.0, y: 222.2 },
  },
  {
    // Release
    ribbonD:
      "M410.3 58.2 L376.8 56.0 L252.0 157.4 L285.1 159.6 L282.7 195.7 L407.9 94.0 Z",
    textPts: [
      { x: 443.1, y: 48.4 }, { x: 431.8, y: 48.8 }, { x: 420.6, y: 50.5 }, { x: 409.7, y: 53.5 }, { x: 399.1, y: 57.7 }, { x: 389.1, y: 63.0 }, { x: 379.8, y: 69.3 }, { x: 371.2, y: 76.7 }, { x: 363.5, y: 85.0 }, { x: 356.7, y: 94.1 }, { x: 348.8, y: 102.1 }, { x: 340.7, y: 110.1 }, { x: 332.6, y: 118.0 }, { x: 324.5, y: 125.9 }, { x: 316.4, y: 133.8 }, { x: 308.3, y: 141.7 }, { x: 300.2, y: 149.6 }, { x: 292.1, y: 157.6 }, { x: 284.0, y: 165.5 }, { x: 275.9, y: 173.4 }, { x: 267.8, y: 181.3 }, { x: 259.7, y: 189.2 }, { x: 254.3, y: 194.5 },
    ],
    textOffset: 46.2,
    center: { x: 331.1, y: 125.8 },
  },
  {
    // Deploy
    ribbonD:
      "M559.5 116.5 L569.4 84.0 C546.5 46.5 506.1 23.5 461.5 23.5 C435.9 23.5 411.4 31.2 390.4 45.6 L422.3 47.7 L419.9 84.8 C432.3 76.8 446.6 72.6 461.5 72.6 C487.3 72.6 510.9 85.2 525.2 106.1 Z",
    textPts: [
      { x: 543.2, y: 150.4 }, { x: 542.7, y: 139.1 }, { x: 540.8, y: 127.9 }, { x: 537.8, y: 117.1 }, { x: 533.5, y: 106.6 }, { x: 528.1, y: 96.6 }, { x: 521.6, y: 87.3 }, { x: 514.1, y: 78.8 }, { x: 505.8, y: 71.2 }, { x: 496.6, y: 64.5 }, { x: 486.8, y: 58.9 }, { x: 476.4, y: 54.5 }, { x: 465.5, y: 51.2 }, { x: 454.4, y: 49.2 }, { x: 443.1, y: 48.4 }, { x: 431.8, y: 48.8 }, { x: 420.6, y: 50.5 }, { x: 409.7, y: 53.5 }, { x: 399.1, y: 57.7 }, { x: 389.1, y: 63.0 }, { x: 382.8, y: 67.1 },
    ],
    textOffset: 47.8,
    center: { x: 479.9, y: 70.0 },
  },
  {
    // Operate
    ribbonD:
      "M576.9 98.2 L567.0 130.6 L533.0 120.2 C536.9 129.6 538.9 139.6 538.9 150.0 C538.9 180.3 521.7 207.0 495.2 219.7 L487.1 233.4 L477.1 250.2 L507.0 268.0 C555.8 249.3 588.0 203.0 588.0 150.0 C588.0 131.9 584.2 114.5 576.9 98.2 Z",
    textPts: [
      { x: 438.5, y: 251.6 }, { x: 449.8, y: 251.3 }, { x: 461.0, y: 249.8 }, { x: 472.0, y: 247.0 }, { x: 482.6, y: 243.0 }, { x: 492.7, y: 237.9 }, { x: 502.1, y: 231.7 }, { x: 510.8, y: 224.4 }, { x: 518.7, y: 216.3 }, { x: 525.6, y: 207.3 }, { x: 531.4, y: 197.6 }, { x: 536.2, y: 187.3 }, { x: 539.7, y: 176.6 }, { x: 542.1, y: 165.5 }, { x: 543.1, y: 154.2 }, { x: 543.0, y: 142.9 }, { x: 541.6, y: 131.7 }, { x: 538.9, y: 120.6 }, { x: 535.1, y: 110.0 }, { x: 530.0, y: 99.9 }, { x: 523.9, y: 90.3 }, { x: 519.2, y: 84.4 },
    ],
    textOffset: 51.6,
    center: { x: 532.5, y: 183.1 },
  },
  {
    // Monitor
    ribbonD:
      "M329.4 205.5 L385.5 251.0 L386.4 251.7 C408.3 267.9 434.3 276.5 461.5 276.5 C472.0 276.5 482.4 275.1 492.5 272.6 L461.5 254.2 L478.6 225.5 C473.0 226.8 467.3 227.4 461.5 227.4 C445.0 227.4 429.3 222.3 416.0 212.6 L360.5 167.5 L326.4 170.5 Z",
    textPts: [
      { x: 297.3, y: 147.1 }, { x: 305.3, y: 155.1 }, { x: 313.4, y: 163.0 }, { x: 321.5, y: 170.9 }, { x: 329.5, y: 178.9 }, { x: 337.6, y: 186.8 }, { x: 345.7, y: 194.8 }, { x: 353.8, y: 202.7 }, { x: 360.6, y: 211.4 }, { x: 367.9, y: 220.0 }, { x: 376.2, y: 227.7 }, { x: 385.2, y: 234.6 }, { x: 395.0, y: 240.3 }, { x: 405.3, y: 244.9 }, { x: 416.1, y: 248.4 }, { x: 427.2, y: 250.6 }, { x: 438.5, y: 251.6 }, { x: 449.8, y: 251.3 }, { x: 461.0, y: 249.8 }, { x: 472.0, y: 247.0 }, { x: 482.6, y: 243.0 }, { x: 492.7, y: 237.9 }, { x: 502.1, y: 231.7 },
    ],
    textOffset: 53.0,
    center: { x: 409.4, y: 222.0 },
  },
];

const INITIAL_STAGGER_SETTLE_MS = 600;
const PULSE_START_BUFFER_MS = 1000;

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
 * Check if text on this path would render upside-down.
 * textPath glyphs stand upright when the path's net direction has
 * positive dx (glyph "up" is the tangent rotated -90°), so reverse
 * right-to-left paths regardless of how vertical they are.
 */
function shouldReverseTextPath(points: Pt[]): boolean {
  if (points.length < 2) return false;
  const startX = points[0].x;
  const endX = points[points.length - 1].x;
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

const CICDLoop: React.FC<CICDLoopProps> = ({
  width = 600,
  height = 300,
  segments: segmentsProp = DEFAULT_SEGMENTS,
  staggerDelay = 150,
  flowDuration = 4000,
}) => {
  // Each segment slot maps 1:1 onto a static shape; extra entries have no
  // geometry to render.
  const segments = useMemo(
    () => segmentsProp.slice(0, SEGMENT_GEOMETRY.length),
    [segmentsProp]
  );

  // Lazy loading: mount when near viewport
  const { ref: lazyRef, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "200px",
  });

  // Unique ID for this component instance (for SVG path IDs)
  const instanceId = useId();

  // Animation hooks
  const [containerRef, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: false,
  });
  const [mounted, setMounted] = useState(false);
  // Tracked as a ref (not state) so flipping it does NOT trigger a re-run
  // of the animation effect below. A previous state-based version caused
  // the effect's cleanup function to fire on the hasEntered=false→true
  // re-render, which cancelled every staggered setTimeout the moment it
  // was scheduled — leaving the segment springs stuck at opacity 0 (an
  // invisible figure-8).
  const hasEnteredRef = useRef(false);
  const introCompleteRef = useRef(false);
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

  // Trigger animations when in view. Note: hasEnteredRef is a ref (not
  // state) so mutating it does not retrigger this effect — the cleanup
  // only fires on real dep changes (inView, mounted) and on unmount.
  useEffect(() => {
    if (!mounted) return;

    if (inView && !hasEnteredRef.current) {
      hasEnteredRef.current = true;
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

          if (index === segments.length - 1) {
            introCompleteRef.current = true;
          }
        }, index * staggerDelay);
        timeoutIds.current.push(id);
      });
    } else if (!inView && hasEnteredRef.current && !introCompleteRef.current) {
      clearAllTimeouts();
      introCompleteRef.current = true;
      api.start(() => ({
        opacity: 1,
        transform: "scale(1)",
        immediate: true,
      }));
    } else if (!hasEnteredRef.current) {
      clearAllTimeouts();
      clearPulseAnimation();
      // Reset when out of view (before first entrance)
      api.start(() => ({
        opacity: 0,
        transform: "scale(0.9)",
        immediate: true,
      }));
    }

    return () => clearAllTimeouts();
  }, [inView, mounted, staggerDelay, api, N, segments]);

  // Continuous pulsing animation after all segments have animated in. Keep the
  // first-entry state in refs so reveal timers are not cancelled by rerenders,
  // but stop pulse timers while the figure is offscreen.
  const pulseStartedRef = useRef(false);
  const hasPulsedRef = useRef(false);

  useEffect(() => {
    if (!mounted || !hasEnteredRef.current) {
      clearPulseAnimation();
      pulseStartedRef.current = false;
      return;
    }

    if (!inView) {
      clearPulseAnimation();
      pulseStartedRef.current = false;
      return;
    }

    if (pulseStartedRef.current) return; // already running — don't restart
    pulseStartedRef.current = true;

    // Wait for all sections to finish initial animation
    const totalAnimationTime = hasPulsedRef.current
      ? 0
      : segments.length * staggerDelay +
        INITIAL_STAGGER_SETTLE_MS +
        PULSE_START_BUFFER_MS;

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
                  transform: "scale(1.04)",
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
      hasPulsedRef.current = true;
      pulseIntervalRef.current = setInterval(startPulse, flowDuration);
    }, totalAnimationTime);

    return () => {
      clearTimeout(continuousAnimationTimeout);
      clearPulseAnimation();
      pulseStartedRef.current = false;
    };
  }, [inView, mounted, staggerDelay, flowDuration, api, N, segments]);

  // Unmount-only cleanup — clears the pulse when the component goes away.
  useEffect(() => () => clearPulseAnimation(), []);

  // Per-segment render data from the static geometry
  const segmentGeoms = useMemo(
    () =>
      SEGMENT_GEOMETRY.map((g) => {
        const needsReverse = shouldReverseTextPath(g.textPts);
        return {
          ribbonD: g.ribbonD,
          textPathD: needsReverse
            ? polylinePathDReversed(g.textPts)
            : polylinePathD(g.textPts),
          textStartOffset: g.textOffset,
          segmentCenter: g.center,
        };
      }),
    []
  );

  // Uniform scale from the 600x300 base space into the requested size
  const scale = Math.min(width / BASE_WIDTH, height / BASE_HEIGHT);
  const offsetX = (width - BASE_WIDTH * scale) / 2;
  const offsetY = (height - BASE_HEIGHT * scale) / 2;

  const fontSize = BASE_HEIGHT * 0.088;

  // Ref to measure text height
  const measureTextRef = useRef<SVGTextElement>(null);
  const [textDy, setTextDy] = useState(0);

  // Refs for overlap detection
  const svgRef = useRef<SVGSVGElement>(null);
  const ribbonRefs = useRef<(SVGPathElement | null)[]>([]);
  const textRefs = useRef<(SVGTextElement | null)[]>([]);

  // State for text offset adjustments due to overlap. Initialize to a
  // zero-filled array of the right length so the no-overlap setState call
  // is dedupe'd in the overlap-detection effect — avoiding a needless
  // re-render that would otherwise reset useSprings in @react-spring/web@10.
  const [textOffsetAdjustments, setTextOffsetAdjustments] = useState<number[]>(
    () => new Array(segments.length).fill(0),
  );

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
  }, [fontSize, nearViewport]);

  // Detect overlap between Plan ribbon and other segments' text
  // Wait for all segments to animate in before checking
  useEffect(() => {
    if (!svgRef.current || !mounted || !inView) return;

    // Wait for all segment animations to complete
    const animationCompleteDelay =
      segments.length * staggerDelay + INITIAL_STAGGER_SETTLE_MS;

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

      // Only setState if values actually changed — every re-render of
      // CICDLoop triggers @react-spring/web@10 to reset segment springs
      // to their init state (opacity:0), so we avoid spurious setState
      // that would cause an unnecessary re-render here.
      setTextOffsetAdjustments((prev) => {
        if (
          prev.length === adjustments.length &&
          prev.every((v, idx) => v === adjustments[idx])
        ) {
          return prev;
        }
        return adjustments;
      });
    }, animationCompleteDelay);

    return () => clearTimeout(timeoutId);
  }, [mounted, inView, planIndex, segments, staggerDelay, segmentGeoms, textDy]);

  if (!nearViewport) {
    return (
      <div ref={lazyRef} style={{ display: "flex", justifyContent: "center", minHeight: `${height}px` }} />
    );
  }

  return (
    <div ref={(el) => { lazyRef(el); if (typeof containerRef === 'function') containerRef(el); }} style={{ display: "flex", justifyContent: "center" }}>
      <svg
        ref={svgRef}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{ maxWidth: "100%", height: "auto", overflow: "visible" }}
      >
        <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
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

          {/* Render segments in two passes: background first, then "Plan" on top.
              Plan overlaps the Release band at the crossing and its arrow prong
              overlaps Code's chevron, so it must paint last (as in the source art). */}
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
                <animated.g
                  key={i}
                  style={{
                    ...springs[i],
                    transformOrigin: `${g.segmentCenter.x}px ${g.segmentCenter.y}px`,
                  }}
                >
                  {/* Ribbon with notch + arrow tip */}
                  <path
                    ref={(el) => { ribbonRefs.current[i] = el; }}
                    d={g.ribbonD}
                    fill={color}
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
        </g>
      </svg>
    </div>
  );
};

// React.memo guards against parent re-renders. The receipt page re-renders
// every time a question is clicked in the QAAgentFlow marquee above us
// (state in useQAQueue updates), which @react-spring/web@10 was treating
// as a signal to reset all springs to their init state — leaving segments
// invisible until the next pulse cycle (~5s). CICDLoop takes no props from
// the page so a stable memo here is safe.
export default React.memo(CICDLoop);
