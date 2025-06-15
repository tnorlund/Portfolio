import React from "react";
import { useTransition, animated } from "@react-spring/web";
import type { Line, Point } from "../../../types/api";

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
> = ({ hull, centroid, avgAngle, svgWidth, svgHeight, delay }) => {
  let bottomPts: Point[] = [];
  let topPts: Point[] = [];
  let lineSegments: LineSegment[] = [];

  if (hull.length >= 3) {
    const angleRad = (avgAngle * Math.PI) / 180;
    const secondaryAxisAngle = angleRad + Math.PI / 2;
    const cosS = Math.cos(secondaryAxisAngle);
    const sinS = Math.sin(secondaryAxisAngle);

    const projHull = hull
      .map(p => {
        const rx = p.x - centroid.x;
        const ry = p.y - centroid.y;
        return { point: p, proj: rx * cosS + ry * sinS };
      })
      .sort((a, b) => a.proj - b.proj);

    bottomPts = [projHull[0].point, projHull[1].point];
    topPts = [
      projHull[projHull.length - 2].point,
      projHull[projHull.length - 1].point,
    ];

    const extendFullWidth = (
      pA: Point,
      pB: Point,
      key: string
    ): LineSegment => {
      const xA = pA.x * svgWidth,
        yA = (1 - pA.y) * svgHeight;
      const xB = pB.x * svgWidth,
        yB = (1 - pB.y) * svgHeight;
      const m = (yB - yA) / (xB - xA);
      const c = yA - m * xA;
      return {
        key,
        x1: 0,
        y1: c,
        x2: svgWidth,
        y2: m * svgWidth + c,
      };
    };

    lineSegments = [
      extendFullWidth(bottomPts[0], bottomPts[1], "bottom-hull-boundary"),
      extendFullWidth(topPts[0], topPts[1], "top-hull-boundary"),
    ];
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
    keys: line => line.key,
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
