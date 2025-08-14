import React from "react";
import { useTransition, animated } from "@react-spring/web";
import type { Line, Point } from "../../../types/api";
import {
  findLineEdgesAtSecondaryExtremes,
  theilSen,
} from "../../../utils/geometry";

interface LineSegment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  key: string;
}

interface AnimatedTopAndBottomProps {
  lines: Line[];
  hull: Point[];
  centroid: Point;
  avgAngle: number; // Receives avgAngle (preliminary angle) for Step 5 boundary detection
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedTopAndBottom: React.FC<AnimatedTopAndBottomProps> = ({
  lines,
  hull,
  centroid,
  avgAngle,
  svgWidth,
  svgHeight,
  delay,
}) => {
  let bottomPts: Point[] = [];
  let topPts: Point[] = [];
  let lineSegments: LineSegment[] = [];

  if (hull.length >= 3) {
    // Step 6: Find top and bottom boundary lines and compute final tilt
    const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
      lines,
      hull,
      centroid,
      avgAngle
    );

    topPts = topEdge;
    bottomPts = bottomEdge;

    // Create line segments that pass through the top and bottom boundary points
    const createBoundaryLine = (
      pA: Point,
      pB: Point,
      key: string
    ): LineSegment | null => {
      const xA = pA.x * svgWidth;
      const yA = (1 - pA.y) * svgHeight;
      const xB = pB.x * svgWidth;
      const yB = (1 - pB.y) * svgHeight;

      const dx = xB - xA;
      const dy = yB - yA;

      // Handle nearly vertical lines (avoid division by zero)
      if (Math.abs(dx) < 1e-6) {
        const avgX = (xA + xB) / 2;
        return {
          key,
          x1: avgX,
          y1: 0,
          x2: avgX,
          y2: svgHeight,
        };
      }

      // Calculate slope and extend line across full width
      const slope = dy / dx;
      const intercept = yA - slope * xA;

      // Calculate line endpoints
      const y1 = intercept;
      const y2 = slope * svgWidth + intercept;

      // Validate all values are finite
      if (
        !isFinite(slope) ||
        !isFinite(intercept) ||
        !isFinite(y1) ||
        !isFinite(y2)
      ) {
        return null;
      }

      return {
        key,
        x1: 0,
        y1: y1,
        x2: svgWidth,
        y2: y2,
      };
    };

    const segments: (LineSegment | null)[] = [];

    // Create top boundary line
    if (topPts.length >= 2) {
      const topLine = createBoundaryLine(topPts[0], topPts[1], "top-boundary");
      if (topLine) segments.push(topLine);
    }

    // Create bottom boundary line
    if (bottomPts.length >= 2) {
      const bottomLine = createBoundaryLine(
        bottomPts[0],
        bottomPts[1],
        "bottom-boundary"
      );
      if (bottomLine) segments.push(bottomLine);
    }

    lineSegments = segments.filter((seg): seg is LineSegment => seg !== null);
  }

  const allBoundaryPoints = [...topPts, ...bottomPts];

  const pointTransitions = useTransition(allBoundaryPoints, {
    keys: (point) => `boundary-point-${point.x}-${point.y}`,
    from: { opacity: 0, scale: 0 },
    enter: (_pt, idx) => ({
      opacity: 1,
      scale: 1,
      delay: delay + idx * 200,
    }),
    config: { duration: 400 },
  });

  const lineTransitions = useTransition(lineSegments, {
    keys: (line) => line.key,
    from: { opacity: 0, strokeDasharray: "10,10", strokeDashoffset: 20 },
    enter: (_item, index) => ({
      opacity: 1,
      strokeDashoffset: 0,
      delay: delay + allBoundaryPoints.length * 200 + index * 300,
    }),
    config: { duration: 800 },
  });

  if (hull.length < 3) return null;

  return (
    <g>
      {/* Animated boundary points (yellow dots) */}
      {pointTransitions((style, pt, _item, idx) => (
        <animated.circle
          key={`boundary-point-${idx}`}
          style={style}
          cx={pt.x * svgWidth}
          cy={(1 - pt.y) * svgHeight}
          r={8}
          fill="var(--color-yellow)"
          stroke="var(--color-yellow)"
          strokeWidth="2"
        />
      ))}

      {/* Animated boundary lines (yellow dashed lines) */}
      {lineTransitions((style, seg) => (
        <animated.line
          key={seg.key}
          style={style}
          x1={seg.x1}
          y1={seg.y1}
          x2={seg.x2}
          y2={seg.y2}
          stroke="var(--color-yellow)"
          strokeWidth="6"
          strokeDasharray="10,10"
        />
      ))}
    </g>
  );
};

export default AnimatedTopAndBottom;
