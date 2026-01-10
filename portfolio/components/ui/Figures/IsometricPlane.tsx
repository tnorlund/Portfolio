import { useSpring, animated, to } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import React, { useMemo } from "react";

/**
 * Parameters for generating an isometric plane shape
 */
interface PlaneParams {
  width: number;       // Width of the plane (horizontal dimension)
  depth: number;       // Depth of the plane (the slanted dimension)
  angle: number;       // Isometric angle in degrees (angle of depth edges from horizontal)
}

/**
 * Generates SVG polygon points for an isometric plane centered at origin
 */
const generatePlanePoints = (
  { width, depth, angle }: PlaneParams,
  offsetX: number = 0,
  offsetY: number = 0
): string => {
  const angleRad = (angle * Math.PI) / 180;
  const dx = depth * Math.cos(angleRad); // Horizontal component of depth
  const dy = depth * Math.sin(angleRad); // Vertical component of depth

  // Four corners of the parallelogram, starting front-left, going clockwise
  // Front edge is at the bottom, back edge is at the top
  const frontLeft = { x: offsetX - width / 2, y: offsetY + dy / 2 };
  const backLeft = { x: offsetX - width / 2 + dx, y: offsetY - dy / 2 };
  const backRight = { x: offsetX + width / 2 + dx, y: offsetY - dy / 2 };
  const frontRight = { x: offsetX + width / 2, y: offsetY + dy / 2 };

  return `${frontLeft.x},${frontLeft.y} ${backLeft.x},${backLeft.y} ${backRight.x},${backRight.y} ${frontRight.x},${frontRight.y}`;
};

/**
 * Generates SVG path for the plane border (with proper stroke)
 */
const generatePlanePath = (
  { width, depth, angle }: PlaneParams,
  offsetX: number = 0,
  offsetY: number = 0
): string => {
  const angleRad = (angle * Math.PI) / 180;
  const dx = depth * Math.cos(angleRad);
  const dy = depth * Math.sin(angleRad);

  const frontLeft = { x: offsetX - width / 2, y: offsetY + dy / 2 };
  const backLeft = { x: offsetX - width / 2 + dx, y: offsetY - dy / 2 };
  const backRight = { x: offsetX + width / 2 + dx, y: offsetY - dy / 2 };
  const frontRight = { x: offsetX + width / 2, y: offsetY + dy / 2 };

  return `M ${frontLeft.x} ${frontLeft.y} L ${backLeft.x} ${backLeft.y} L ${backRight.x} ${backRight.y} L ${frontRight.x} ${frontRight.y} Z`;
};

interface IsometricPlaneProps {
  /** Width of the planes */
  planeWidth?: number;
  /** Depth of the planes */
  planeDepth?: number;
  /** Isometric angle in degrees */
  angle?: number;
  /** Vertical gap between the two planes */
  gapY?: number;
  /** Whether to show the Z-depth constraint line */
  showConstraintLine?: boolean;
  /** Whether to allow the top plane to skew/tilt */
  allowSkew?: boolean;
  /** Animation intensity (0-1, affects movement range) */
  animationIntensity?: number;
  /** SVG viewBox width */
  viewBoxWidth?: number;
  /** SVG viewBox height */
  viewBoxHeight?: number;
  /** Fill color for top plane */
  topPlaneFill?: string;
  /** Fill color for bottom plane */
  bottomPlaneFill?: string;
  /** Stroke color */
  strokeColor?: string;
}

const IsometricPlane: React.FC<IsometricPlaneProps> = ({
  planeWidth = 66,
  planeDepth = 60,
  angle = 48,
  gapY = 58,
  showConstraintLine = true,
  allowSkew = false,
  animationIntensity = 1,
  viewBoxWidth = 300,
  viewBoxHeight = 160,
  topPlaneFill = "var(--code-background)",
  bottomPlaneFill = "var(--background-color)",
  strokeColor = "var(--text-color)",
}) => {
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });

  // Calculate center positions for both planes
  const centerX = viewBoxWidth / 2;
  const topPlaneY = viewBoxHeight / 2 - gapY / 2;
  const bottomPlaneY = viewBoxHeight / 2 + gapY / 2;

  // Animation parameters scaled by intensity
  const floatRange = 20 * animationIntensity;
  const horizRange = 15 * animationIntensity;
  const skewRange = allowSkew ? 15 * animationIntensity : 0;

  // Springs for animations
  const [fadeStyles, fadeApi] = useSpring(() => ({
    opacity: 0,
    config: { tension: 120, friction: 14 },
  }));

  const [floatStyles, floatApi] = useSpring(() => ({
    y: 0,
    config: { tension: 60, friction: 20 },
  }));

  const [horizStyles, horizApi] = useSpring(() => ({
    x: 0,
    config: { tension: 40, friction: 14 },
  }));

  const [skewStyles, skewApi] = useSpring(() => ({
    s: 0,
    config: { tension: 35, friction: 12 },
  }));

  // Start/stop animations based on visibility
  React.useEffect(() => {
    if (inView) {
      fadeApi.start({ opacity: 1 });
      floatApi.start({
        to: [{ y: -floatRange }, { y: 0 }],
        loop: { reverse: true },
      });
      horizApi.start({
        to: [{ x: -horizRange }, { x: horizRange }],
        loop: { reverse: true },
      });
      if (allowSkew) {
        skewApi.start({
          to: [{ s: -skewRange / 2 }, { s: skewRange / 2 }],
          loop: { reverse: true },
        });
      }
    } else {
      floatApi.stop();
      floatApi.set({ y: 0 });
      horizApi.stop();
      horizApi.set({ x: 0 });
      skewApi.stop();
      skewApi.set({ s: 0 });
    }
  }, [inView, fadeApi, floatApi, horizApi, skewApi, floatRange, horizRange, skewRange, allowSkew]);

  // Memoize the plane geometry
  const planeParams = useMemo(
    () => ({ width: planeWidth, depth: planeDepth, angle }),
    [planeWidth, planeDepth, angle]
  );

  const topPlanePoints = useMemo(
    () => generatePlanePoints(planeParams, centerX, topPlaneY),
    [planeParams, centerX, topPlaneY]
  );

  const bottomPlanePoints = useMemo(
    () => generatePlanePoints(planeParams, centerX, bottomPlaneY),
    [planeParams, centerX, bottomPlaneY]
  );

  // Calculate the exact front-left corner positions for constraint line
  const angleRad = (angle * Math.PI) / 180;
  const dy = (planeDepth * Math.sin(angleRad)) / 2;

  // Front-left corner of each plane (where constraint line attaches)
  const topCornerX = centerX - planeWidth / 2;
  const topCornerY = topPlaneY + dy;
  const bottomCornerX = centerX - planeWidth / 2;
  const bottomCornerY = bottomPlaneY + dy;

  // Arrow size
  const arrowSize = 5;
  const circleRadius = 3;

  return (
    <div style={{ display: "flex", justifyContent: "center" }}>
      <div ref={ref}>
        <animated.div style={fadeStyles}>
          <svg
            viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`}
            width="100%"
            height="auto"
            style={{ maxWidth: "300px" }}
          >
            {/* Bottom plane (static) */}
            <g>
              <polygon points={bottomPlanePoints} fill={bottomPlaneFill} />
              <path
                d={generatePlanePath(planeParams, centerX, bottomPlaneY)}
                fill="none"
                stroke={strokeColor}
                strokeWidth="1"
              />
            </g>

            {/* Top plane (animated) */}
            <animated.g
              style={{
                transform: horizStyles.x.to((x) => `translateX(${x}px)`),
              }}
            >
              <animated.g
                style={{
                  transform: allowSkew
                    ? to(
                        [floatStyles.y, skewStyles.s],
                        (y, s) => `translateY(${y}px) skewY(${s}deg)`
                      )
                    : floatStyles.y.to((y) => `translateY(${y}px)`),
                  transformOrigin: `${centerX}px ${topPlaneY}px`,
                }}
              >
                <polygon points={topPlanePoints} fill={topPlaneFill} />
                <path
                  d={generatePlanePath(planeParams, centerX, topPlaneY)}
                  fill="none"
                  stroke={strokeColor}
                  strokeWidth="1"
                />
              </animated.g>
            </animated.g>

            {/* Constraint line connecting front-left corners (only if showConstraintLine) */}
            {showConstraintLine && (
              <g>
                {/* Line from circle to arrow */}
                <animated.line
                  x1={bottomCornerX}
                  y1={bottomCornerY}
                  x2={to(
                    [horizStyles.x, floatStyles.y],
                    (x, y) => {
                      const topX = topCornerX + x;
                      const topY = topCornerY + y;
                      // Calculate direction and offset line end to arrow base
                      const dx = topX - bottomCornerX;
                      const dy = topY - bottomCornerY;
                      const len = Math.sqrt(dx * dx + dy * dy);
                      const offsetDist = arrowSize * 1.5 + 2;
                      return topX - (dx / len) * offsetDist;
                    }
                  )}
                  y2={to(
                    [horizStyles.x, floatStyles.y],
                    (x, y) => {
                      const topX = topCornerX + x;
                      const topY = topCornerY + y;
                      const dx = topX - bottomCornerX;
                      const dy = topY - bottomCornerY;
                      const len = Math.sqrt(dx * dx + dy * dy);
                      const offsetDist = arrowSize * 1.5 + 2;
                      return topY - (dy / len) * offsetDist;
                    }
                  )}
                  stroke={strokeColor}
                  strokeWidth="1"
                />
                {/* Arrow pointing along line toward the moving top plane corner */}
                <animated.polygon
                  points={to(
                    [horizStyles.x, floatStyles.y],
                    (x, y) => {
                      const topX = topCornerX + x;
                      const topY = topCornerY + y;
                      // Direction from bottom to top corner
                      const dx = topX - bottomCornerX;
                      const dy = topY - bottomCornerY;
                      const len = Math.sqrt(dx * dx + dy * dy);
                      // Unit vector along line
                      const ux = dx / len;
                      const uy = dy / len;
                      // Perpendicular vector
                      const px = -uy;
                      const py = ux;
                      // Arrow tip near the corner
                      const tipX = topX - ux * 2;
                      const tipY = topY - uy * 2;
                      // Arrow base points (perpendicular to line direction)
                      const baseOffset = arrowSize * 1.5;
                      const base1X = tipX - ux * baseOffset + px * arrowSize;
                      const base1Y = tipY - uy * baseOffset + py * arrowSize;
                      const base2X = tipX - ux * baseOffset - px * arrowSize;
                      const base2Y = tipY - uy * baseOffset - py * arrowSize;
                      return `${base1X},${base1Y} ${tipX},${tipY} ${base2X},${base2Y}`;
                    }
                  )}
                  fill={strokeColor}
                />
                {/* Circle on the static bottom plane corner */}
                <circle
                  cx={bottomCornerX}
                  cy={bottomCornerY}
                  r={circleRadius}
                  fill={strokeColor}
                />
              </g>
            )}
          </svg>
        </animated.div>
      </div>
    </div>
  );
};

export default IsometricPlane;

/**
 * Constrained version - plane maintains parallel alignment
 */
export const ZDepthConstrainedParametric: React.FC<Partial<IsometricPlaneProps>> = (props) => (
  <IsometricPlane
    showConstraintLine={true}
    allowSkew={false}
    animationIntensity={1}
    {...props}
  />
);

/**
 * Unconstrained version - plane can tilt/skew
 */
export const ZDepthUnconstrainedParametric: React.FC<Partial<IsometricPlaneProps>> = (props) => (
  <IsometricPlane
    showConstraintLine={false}
    allowSkew={true}
    animationIntensity={0.5}
    {...props}
  />
);
