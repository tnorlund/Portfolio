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

interface AnimatedSecondaryBoundaryLinesProps {
  lines: Line[];
  hull: Point[];
  centroid: Point;
  avgAngle: number;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedSecondaryBoundaryLines: React.FC<
  AnimatedSecondaryBoundaryLinesProps
> = ({ lines, hull, centroid, avgAngle, svgWidth, svgHeight, delay }) => {
  let bottomPts: Point[] = [];
  let topPts: Point[] = [];
  let lineSegments: LineSegment[] = [];

  if (hull.length >= 3) {
    // Use the same algorithm as the tests
    const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
      lines,
      hull,
      centroid,
      avgAngle
    );

    topPts = topEdge;
    bottomPts = bottomEdge;

    const extendFullWidth = (
      pA: Point,
      pB: Point,
      key: string
    ): LineSegment | null => {
      const xA = pA.x * svgWidth,
        yA = (1 - pA.y) * svgHeight;
      const xB = pB.x * svgWidth,
        yB = (1 - pB.y) * svgHeight;

      const dx = xB - xA;
      const dy = yB - yA;

      // Handle nearly vertical lines (avoid division by zero)
      if (Math.abs(dx) < 1e-6) {
        // For vertical lines, extend from top to bottom at average x
        const avgX = (xA + xB) / 2;
        return {
          key,
          x1: avgX,
          y1: 0,
          x2: avgX,
          y2: svgHeight,
        };
      }

      // Handle normal lines
      const m = dy / dx;
      const c = yA - m * xA;

      // Calculate line endpoints
      const y1 = c;
      const y2 = m * svgWidth + c;

      // Validate all values are finite
      if (!isFinite(m) || !isFinite(c) || !isFinite(y1) || !isFinite(y2)) {
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

    // Create line segments that pass exactly through the 2 boundary points
    const segments: (LineSegment | null)[] = [];

    if (bottomPts.length >= 2) {
      // Calculate line directly through the 2 bottom points
      const p1 = bottomPts[0];
      const p2 = bottomPts[1];

      // Calculate slope and intercept in normalized coordinates
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;

      if (Math.abs(dx) < 1e-6) {
        // Nearly vertical line - extend from top to bottom at average x
        const avgX = (p1.x + p2.x) / 2;
        segments.push({
          key: "bottom-hull-boundary",
          x1: avgX * svgWidth,
          y1: 0,
          x2: avgX * svgWidth,
          y2: svgHeight,
        });
      } else {
        // Normal line - calculate slope and extend across full width
        const slope = dy / dx;
        const intercept = p1.y - slope * p1.x;

        // Calculate line endpoints in normalized coordinates
        const y1 = slope * 0 + intercept; // Left edge (x=0)
        const y2 = slope * 1 + intercept; // Right edge (x=1)

        segments.push({
          key: "bottom-hull-boundary",
          x1: 0,
          y1: (1 - y1) * svgHeight, // Convert to SVG coordinates
          x2: svgWidth,
          y2: (1 - y2) * svgHeight, // Convert to SVG coordinates
        });
      }
    }

    if (topPts.length >= 2) {
      // Calculate line directly through the 2 top points
      const p1 = topPts[0];
      const p2 = topPts[1];

      // Calculate slope and intercept in normalized coordinates
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;

      if (Math.abs(dx) < 1e-6) {
        // Nearly vertical line - extend from top to bottom at average x
        const avgX = (p1.x + p2.x) / 2;
        segments.push({
          key: "top-hull-boundary",
          x1: avgX * svgWidth,
          y1: 0,
          x2: avgX * svgWidth,
          y2: svgHeight,
        });
      } else {
        // Normal line - calculate slope and extend across full width
        const slope = dy / dx;
        const intercept = p1.y - slope * p1.x;

        // Calculate line endpoints in normalized coordinates
        const y1 = slope * 0 + intercept; // Left edge (x=0)
        const y2 = slope * 1 + intercept; // Right edge (x=1)

        segments.push({
          key: "top-hull-boundary",
          x1: 0,
          y1: (1 - y1) * svgHeight, // Convert to SVG coordinates
          x2: svgWidth,
          y2: (1 - y2) * svgHeight, // Convert to SVG coordinates
        });
      }
    }

    lineSegments = segments.filter((seg): seg is LineSegment => seg !== null);
  }

  const dotPoints = [...bottomPts, ...topPts];

  const dotTransitions = useTransition(dotPoints, {
    from: { opacity: 0 },
    enter: (_pt, idx) => ({
      opacity: 1,
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
      delay: delay + index * 200,
    }),
    config: { duration: 800 },
  });

  if (hull.length < 3) return null;

  return (
    <g>
      {/* Animated yellow dots */}
      {dotTransitions((style, pt, _item, idx) => (
        <animated.circle
          key={`secondary-extreme-${idx}`}
          style={style}
          cx={pt.x * svgWidth}
          cy={(1 - pt.y) * svgHeight}
          r={8}
          fill="var(--color-yellow)"
          stroke="white"
          strokeWidth="2"
        />
      ))}
      {lineTransitions((style, seg) => (
        <animated.line
          key={seg.key}
          style={style}
          x1={seg.x1}
          y1={seg.y1}
          x2={seg.x2}
          y2={seg.y2}
          stroke="var(--color-yellow)"
          strokeWidth="5"
          strokeDasharray="10,10"
        />
      ))}
    </g>
  );
};

export default AnimatedSecondaryBoundaryLines;
