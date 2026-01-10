import { useSpring, animated, to } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import React, { useMemo, useEffect } from "react";

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
  // triggerOnce: false allows animation to pause when scrolling out of view
  const [ref, inView] = useOptimizedInView({ threshold: 0.3, triggerOnce: false });

  // Calculate center positions for both planes
  const centerX = viewBoxWidth / 2;
  const topPlaneY = viewBoxHeight / 2 - gapY / 2;
  const bottomPlaneY = viewBoxHeight / 2 + gapY / 2;

  // Animation parameters scaled by intensity (reduced for subtlety)
  const floatRange = 8 * animationIntensity;
  const horizRange = 6 * animationIntensity;
  const skewRange = allowSkew ? 8 * animationIntensity : 0;

  // Single spring driving a phase value for sinusoidal motion
  const [{ phase }, api] = useSpring(() => ({
    phase: 0,
    config: { duration: 8000 }, // 8 second cycle
  }));

  // Start/stop animation based on visibility
  useEffect(() => {
    if (inView) {
      api.start({
        from: { phase: 0 },
        to: { phase: Math.PI * 2 },
        loop: true,
      });
    } else {
      api.stop();
      api.set({ phase: 0 });
    }
  }, [inView, api]);

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

  // Arrow and circle sizes
  const arrowSize = 5;
  const circleRadius = 4;

  // Helper to compute constraint line geometry from animation phase
  // Returns unit vectors and positions needed for line and arrow
  const computeConstraintGeometry = (p: number) => {
    const x = Math.sin(p * 0.8) * horizRange;
    const y = Math.sin(p) * floatRange;
    const topX = topCornerX + x;
    const topY = topCornerY + y;
    const dx = topX - bottomCornerX;
    const dy = topY - bottomCornerY;
    // Guard against zero-length vector (when gapY=0 or corners coincide)
    const len = Math.max(Math.sqrt(dx * dx + dy * dy), 0.001);
    const ux = dx / len;
    const uy = dy / len;
    return { topX, topY, ux, uy };
  };

  return (
    <div style={{ display: "flex", justifyContent: "center" }}>
      <div ref={ref}>
        <div
          style={{
            opacity: inView ? 1 : 0,
            transition: "opacity 0.5s ease-in-out",
          }}
        >
          <svg
            viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`}
            width="100%"
            style={{ maxWidth: "300px", height: "auto" }}
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
                transform: phase.to((p) => {
                  const x = Math.sin(p * 0.8) * horizRange;
                  const y = Math.sin(p) * floatRange;
                  const skew = allowSkew ? Math.sin(p * 0.9) * skewRange : 0;
                  return `translate(${x}px, ${y}px)${allowSkew ? ` skewY(${skew}deg)` : ""}`;
                }),
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

            {/* Constraint line (calculated dynamically) */}
            {showConstraintLine && (
              <>
                {/* Line from circle edge to arrow base */}
                <animated.line
                  x1={phase.to((p) => {
                    const { ux } = computeConstraintGeometry(p);
                    return bottomCornerX + ux * (circleRadius + 1);
                  })}
                  y1={phase.to((p) => {
                    const { uy } = computeConstraintGeometry(p);
                    return bottomCornerY + uy * (circleRadius + 1);
                  })}
                  x2={phase.to((p) => {
                    const { topX, ux } = computeConstraintGeometry(p);
                    return topX - ux * (arrowSize * 1.5 + 2);
                  })}
                  y2={phase.to((p) => {
                    const { topY, uy } = computeConstraintGeometry(p);
                    return topY - uy * (arrowSize * 1.5 + 2);
                  })}
                  stroke={strokeColor}
                  strokeWidth="1"
                />
                {/* Arrow pointing along line toward moving corner */}
                <animated.polygon
                  points={to([phase], (p) => {
                    const { topX, topY, ux, uy } = computeConstraintGeometry(p);
                    const px = -uy;
                    const py = ux;
                    // Arrow tip near the corner
                    const tipX = topX - ux * 2;
                    const tipY = topY - uy * 2;
                    const baseOffset = arrowSize * 1.5;
                    const base1X = tipX - ux * baseOffset + px * arrowSize;
                    const base1Y = tipY - uy * baseOffset + py * arrowSize;
                    const base2X = tipX - ux * baseOffset - px * arrowSize;
                    const base2Y = tipY - uy * baseOffset - py * arrowSize;
                    return `${base1X},${base1Y} ${tipX},${tipY} ${base2X},${base2Y}`;
                  })}
                  fill={strokeColor}
                />
                {/* Circle on the static bottom plane corner */}
                <circle
                  cx={bottomCornerX}
                  cy={bottomCornerY}
                  r={circleRadius}
                  fill={strokeColor}
                  stroke={strokeColor}
                  strokeWidth="1"
                />
              </>
            )}
          </svg>
        </div>
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
