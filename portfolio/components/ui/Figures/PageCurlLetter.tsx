import React, { useEffect, useRef, useCallback } from "react";
import { useSpring, animated, to } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";

interface PageCurlLetterProps {
  width?: number;
  height?: number;
  maxCurlDepth?: number;
  paperColor?: string;
  backColor?: string;
  letterColor?: string;
  shadowColor?: string;
}

// SVG viewBox dimensions
const VIEWBOX_WIDTH = 300;
const VIEWBOX_HEIGHT = 150;

// Paper rectangle bounds
const PAPER_X = 108.57;
const PAPER_Y = 20;
const PAPER_WIDTH = 82.86;
const PAPER_HEIGHT = 110;

// Corner coordinates
const TOP_RIGHT = { x: PAPER_X + PAPER_WIDTH, y: PAPER_Y };
const BOTTOM_RIGHT = { x: PAPER_X + PAPER_WIDTH, y: PAPER_Y + PAPER_HEIGHT };
const TOP_LEFT = { x: PAPER_X, y: PAPER_Y };

// Letter "A" path
const LETTER_PATH =
  "M147,53.48h6.59l15.62,43.04h-6.39l-4.37-12.89h-17.02l-4.66,12.89h-5.98l16.2-43.04ZM156.58,78.88l-6.53-19.01-6.94,19.01h13.48Z";

/**
 * Linear interpolation
 */
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/**
 * Generate the crease line endpoints based on curl progress
 * As t increases, the crease moves further into the paper
 * @param ratio - Controls the angle of the crease (0.5 = 45°, <0.5 = more horizontal, >0.5 = more vertical)
 */
function getCreasePoints(t: number, maxDepth: number, ratio: number = 0.5) {
  const depth = t * maxDepth;

  // Ratio determines how much curl goes horizontally vs vertically
  // ratio = 0.5 means equal (45° crease)
  // ratio < 0.5 means more horizontal curl (crease is steeper)
  // ratio > 0.5 means more vertical curl (crease is shallower)
  const horizontalDepth = depth * (1 + (0.5 - ratio));
  const verticalDepth = depth * (1 + (ratio - 0.5));

  // Point on top edge (moving left from corner)
  const topPoint = {
    x: TOP_RIGHT.x - horizontalDepth,
    y: TOP_RIGHT.y,
  };

  // Point on right edge (moving down from corner)
  const rightPoint = {
    x: TOP_RIGHT.x,
    y: TOP_RIGHT.y + verticalDepth,
  };

  return { topPoint, rightPoint, depth };
}

/**
 * Generate the Bezier control point for a curved crease
 */
function getCreaseControlPoint(
  topPoint: { x: number; y: number },
  rightPoint: { x: number; y: number },
  curveFactor: number = 0.3
) {
  // Control point pulled toward the corner for a nice curve
  const midX = (topPoint.x + rightPoint.x) / 2;
  const midY = (topPoint.y + rightPoint.y) / 2;

  // Pull toward corner
  const ctrlX = midX + (TOP_RIGHT.x - midX) * curveFactor;
  const ctrlY = midY + (TOP_RIGHT.y - midY) * curveFactor;

  return { x: ctrlX, y: ctrlY };
}

/**
 * Generate the curled back face polygon
 * This is the corner region reflected/rotated to appear folded
 */
function getCurledBackPolygon(
  topPoint: { x: number; y: number },
  rightPoint: { x: number; y: number },
  t: number
) {
  // The curl rotates around the crease line
  // For a realistic effect, we reflect the corner across the crease

  // Crease midpoint
  const creaseMidX = (topPoint.x + rightPoint.x) / 2;
  const creaseMidY = (topPoint.y + rightPoint.y) / 2;

  // Crease direction vector
  const creaseDx = rightPoint.x - topPoint.x;
  const creaseDy = rightPoint.y - topPoint.y;
  const creaseLen = Math.sqrt(creaseDx * creaseDx + creaseDy * creaseDy);

  if (creaseLen === 0) return null;

  // Unit vectors
  const ux = creaseDx / creaseLen;
  const uy = creaseDy / creaseLen;

  // Perpendicular (pointing into the page, toward bottom-left)
  const px = -uy;
  const py = ux;

  // Reflect corner point across crease line
  // Vector from crease midpoint to corner
  const toCornerX = TOP_RIGHT.x - creaseMidX;
  const toCornerY = TOP_RIGHT.y - creaseMidY;

  // Project onto perpendicular
  const projLen = toCornerX * px + toCornerY * py;

  // Reflected corner (on the other side of crease)
  // As t increases, the curl folds more
  const foldAmount = Math.min(t * 1.5, 1); // How much it's folded over
  const reflectedCornerX = TOP_RIGHT.x - 2 * projLen * px * foldAmount;
  const reflectedCornerY = TOP_RIGHT.y - 2 * projLen * py * foldAmount;

  return {
    topPoint,
    rightPoint,
    reflectedCorner: { x: reflectedCornerX, y: reflectedCornerY },
  };
}

const PageCurlLetter: React.FC<PageCurlLetterProps> = ({
  width = 300,
  height = 150,
  maxCurlDepth = 35,
  paperColor = "var(--paper-color)",
  backColor = "var(--paper-back-color)",
  letterColor = "var(--paper-stroke-color)",
  shadowColor = "var(--paper-shadow-color)",
}) => {
  const [ref, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  // Random ratio for curl angle - stored in ref, changes each cycle
  const curlRatioRef = useRef(0.3 + Math.random() * 0.4);

  // Track if animation has started (for initial fade-in)
  const hasStarted = useRef(false);

  // Track if we're currently animating to prevent race conditions
  const isAnimating = useRef(false);

  // Track pending timeout for cleanup
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [{ t }, api] = useSpring(() => ({
    t: 0,
    config: { tension: 60, friction: 18 },
  }));

  // Function to run one curl cycle
  const runCurlCycle = useCallback(() => {
    if (!isAnimating.current) return;

    // Curl up
    api.start({
      t: 1,
      onResolve: () => {
        if (!isAnimating.current) return;

        // Pause at curled position, then uncurl
        timeoutRef.current = setTimeout(() => {
          if (!isAnimating.current) return;

          api.start({
            t: 0,
            onResolve: () => {
              if (!isAnimating.current) return;

              // Generate new random ratio for next cycle
              curlRatioRef.current = 0.3 + Math.random() * 0.4;

              // Pause at flat position, then start new cycle
              timeoutRef.current = setTimeout(() => {
                if (isAnimating.current) {
                  runCurlCycle();
                }
              }, 400);
            },
          });
        }, 800);
      },
    });
  }, [api]);

  // Handle visibility changes
  useEffect(() => {
    if (inView) {
      hasStarted.current = true;
      isAnimating.current = true;
      runCurlCycle();
    } else {
      // Stop animation when out of view
      isAnimating.current = false;

      // Clear any pending timeouts
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }

      // Reset to flat immediately
      api.start({ t: 0, immediate: true });

      // Reset ratio for next time
      curlRatioRef.current = 0.3 + Math.random() * 0.4;
    }

    // Cleanup on unmount
    return () => {
      isAnimating.current = false;
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, [inView, api, runCurlCycle]);

  return (
    <div style={{ display: "flex", justifyContent: "center" }}>
      <div ref={ref}>
        <div
          style={{
            opacity: hasStarted.current || inView ? 1 : 0,
            transition: "opacity 0.5s ease-in-out",
          }}
        >
          <svg
            viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
            width={width}
            height={height}
            style={{ maxWidth: "100%", height: "auto" }}
          >
            <defs>
              {/* Gradient for back face - darker at edges */}
              <linearGradient
                id="backFaceGradient"
                x1="100%"
                y1="0%"
                x2="0%"
                y2="100%"
              >
                <stop offset="0%" stopColor={backColor} />
                <stop offset="60%" stopColor={backColor} />
                <stop offset="100%" stopColor="var(--paper-back-edge-color)" />
              </linearGradient>

              {/* Shadow blur filter */}
              <filter id="shadowBlur" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur in="SourceGraphic" stdDeviation="3" />
              </filter>

              {/* Clip path for front face - excludes curled region */}
              <clipPath id="frontClip">
                <animated.path
                  d={to([t], (tVal) => {
                    if (tVal <= 0.01) {
                      // No curl yet - show full paper
                      return `M ${PAPER_X} ${PAPER_Y}
                              L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y}
                              L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y + PAPER_HEIGHT}
                              L ${PAPER_X} ${PAPER_Y + PAPER_HEIGHT} Z`;
                    }

                    const { topPoint, rightPoint } = getCreasePoints(tVal, maxCurlDepth, curlRatioRef.current);
                    const ctrl = getCreaseControlPoint(topPoint, rightPoint);

                    // Paper outline minus the curled corner
                    return `M ${PAPER_X} ${PAPER_Y}
                            L ${topPoint.x} ${topPoint.y}
                            Q ${ctrl.x} ${ctrl.y} ${rightPoint.x} ${rightPoint.y}
                            L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y + PAPER_HEIGHT}
                            L ${PAPER_X} ${PAPER_Y + PAPER_HEIGHT} Z`;
                  })}
                />
              </clipPath>

              {/* Clip path for shadow - only visible on the paper surface */}
              <clipPath id="shadowClip">
                <animated.path
                  d={to([t], (tVal) => {
                    if (tVal <= 0.01) {
                      return `M ${PAPER_X} ${PAPER_Y}
                              L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y}
                              L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y + PAPER_HEIGHT}
                              L ${PAPER_X} ${PAPER_Y + PAPER_HEIGHT} Z`;
                    }

                    const { topPoint, rightPoint } = getCreasePoints(tVal, maxCurlDepth, curlRatioRef.current);
                    const ctrl = getCreaseControlPoint(topPoint, rightPoint);

                    // Front face area - where shadow can appear
                    return `M ${PAPER_X} ${PAPER_Y}
                            L ${topPoint.x} ${topPoint.y}
                            Q ${ctrl.x} ${ctrl.y} ${rightPoint.x} ${rightPoint.y}
                            L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y + PAPER_HEIGHT}
                            L ${PAPER_X} ${PAPER_Y + PAPER_HEIGHT} Z`;
                  })}
                />
              </clipPath>
            </defs>

            {/* Shadow under the curl - cast onto the paper surface */}
            <g clipPath="url(#shadowClip)">
              <animated.path
                d={to([t], (tVal) => {
                  if (tVal <= 0.05) return "";

                  const { topPoint, rightPoint } = getCreasePoints(tVal, maxCurlDepth, curlRatioRef.current);
                  const ctrl = getCreaseControlPoint(topPoint, rightPoint, 0.35);

                  // Shadow is cast from the curled flap onto the paper below
                  // It follows the crease line but offset slightly into the paper
                  const shadowInset = 5 + tVal * 10; // Shadow grows as curl increases

                  // Calculate shadow offset perpendicular to crease (into the paper)
                  const creaseDx = rightPoint.x - topPoint.x;
                  const creaseDy = rightPoint.y - topPoint.y;
                  const creaseLen = Math.sqrt(creaseDx * creaseDx + creaseDy * creaseDy);

                  if (creaseLen === 0) return "";

                  // Perpendicular pointing into the paper (toward bottom-left)
                  const perpX = -creaseDy / creaseLen;
                  const perpY = creaseDx / creaseLen;

                  // Shadow line - offset from crease into the paper
                  const shadowTop = {
                    x: topPoint.x + perpX * shadowInset,
                    y: topPoint.y + perpY * shadowInset,
                  };
                  const shadowRight = {
                    x: rightPoint.x + perpX * shadowInset,
                    y: rightPoint.y + perpY * shadowInset,
                  };
                  const shadowCtrl = {
                    x: ctrl.x + perpX * shadowInset,
                    y: ctrl.y + perpY * shadowInset,
                  };

                  // Create shadow shape between crease and shadow line
                  return `M ${topPoint.x} ${topPoint.y}
                          Q ${ctrl.x} ${ctrl.y} ${rightPoint.x} ${rightPoint.y}
                          L ${shadowRight.x} ${shadowRight.y}
                          Q ${shadowCtrl.x} ${shadowCtrl.y} ${shadowTop.x} ${shadowTop.y}
                          Z`;
                })}
                fill={shadowColor}
                filter="url(#shadowBlur)"
                opacity={to([t], (tVal) => Math.min(tVal * 0.8, 0.5))}
              />
            </g>

            {/* Front face - paper with letter, clipped */}
            <g clipPath="url(#frontClip)">
              <rect
                x={PAPER_X}
                y={PAPER_Y}
                width={PAPER_WIDTH}
                height={PAPER_HEIGHT}
                fill={paperColor}
                stroke={letterColor}
                strokeWidth="1"
              />
              <path d={LETTER_PATH} fill={letterColor} />
            </g>

            {/* Back face - the curled portion */}
            <animated.g
              opacity={to([t], (tVal) => (tVal > 0.05 ? 1 : 0))}
            >
              <animated.path
                d={to([t], (tVal) => {
                  if (tVal <= 0.05) return "";

                  const { topPoint, rightPoint } = getCreasePoints(tVal, maxCurlDepth, curlRatioRef.current);
                  const backPoly = getCurledBackPolygon(topPoint, rightPoint, tVal);

                  if (!backPoly) return "";

                  const { reflectedCorner } = backPoly;
                  const ctrl = getCreaseControlPoint(topPoint, rightPoint, 0.3);

                  // Draw the folded back portion as a curved triangle
                  return `M ${topPoint.x} ${topPoint.y}
                          Q ${ctrl.x} ${ctrl.y} ${rightPoint.x} ${rightPoint.y}
                          L ${reflectedCorner.x} ${reflectedCorner.y} Z`;
                })}
                fill="url(#backFaceGradient)"
                stroke={letterColor}
                strokeWidth="1"
              />
            </animated.g>

            {/* Crease highlight - subtle white line along fold */}
            <animated.path
              d={to([t], (tVal) => {
                if (tVal <= 0.05) return "";

                const { topPoint, rightPoint } = getCreasePoints(tVal, maxCurlDepth, curlRatioRef.current);
                const ctrl = getCreaseControlPoint(topPoint, rightPoint);

                return `M ${topPoint.x} ${topPoint.y} Q ${ctrl.x} ${ctrl.y} ${rightPoint.x} ${rightPoint.y}`;
              })}
              fill="none"
              stroke="white"
              strokeWidth="1.5"
              strokeOpacity={to([t], (tVal) => Math.min(tVal * 0.6, 0.3))}
            />

            {/* Crease shadow line - darker line for depth */}
            <animated.path
              d={to([t], (tVal) => {
                if (tVal <= 0.05) return "";

                const { topPoint, rightPoint } = getCreasePoints(tVal, maxCurlDepth, curlRatioRef.current);
                const ctrl = getCreaseControlPoint(topPoint, rightPoint);

                // Slightly offset toward the curl
                const offsetX = 1;
                const offsetY = 1;
                return `M ${topPoint.x + offsetX} ${topPoint.y + offsetY}
                        Q ${ctrl.x + offsetX} ${ctrl.y + offsetY} ${rightPoint.x + offsetX} ${rightPoint.y + offsetY}`;
              })}
              fill="none"
              stroke={letterColor}
              strokeWidth="0.5"
              strokeOpacity={to([t], (tVal) => Math.min(tVal * 0.5, 0.25))}
            />

            {/* Paper border on front (drawn on top) */}
            <animated.path
              d={to([t], (tVal) => {
                if (tVal <= 0.01) {
                  return `M ${PAPER_X} ${PAPER_Y}
                          L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y}
                          L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y + PAPER_HEIGHT}
                          L ${PAPER_X} ${PAPER_Y + PAPER_HEIGHT} Z`;
                }

                const { topPoint, rightPoint } = getCreasePoints(tVal, maxCurlDepth, curlRatioRef.current);
                const ctrl = getCreaseControlPoint(topPoint, rightPoint);

                return `M ${PAPER_X} ${PAPER_Y}
                        L ${topPoint.x} ${topPoint.y}
                        Q ${ctrl.x} ${ctrl.y} ${rightPoint.x} ${rightPoint.y}
                        L ${PAPER_X + PAPER_WIDTH} ${PAPER_Y + PAPER_HEIGHT}
                        L ${PAPER_X} ${PAPER_Y + PAPER_HEIGHT} Z`;
              })}
              fill="none"
              stroke={letterColor}
              strokeWidth="1"
            />
          </svg>
        </div>
      </div>
    </div>
  );
};

export default PageCurlLetter;
