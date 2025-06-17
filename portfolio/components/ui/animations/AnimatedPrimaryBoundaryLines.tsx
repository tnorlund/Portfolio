import React from "react";
import { useTransition, animated } from "@react-spring/web";
import type { Point } from "../../../types/api";

interface LineSegment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  key: string;
}

interface AnimatedPrimaryBoundaryLinesProps {
  hull: Point[];
  centroid: Point;
  avgAngle: number;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedPrimaryBoundaryLines: React.FC<
  AnimatedPrimaryBoundaryLinesProps
> = ({ hull, centroid, avgAngle, svgWidth, svgHeight, delay }) => {
  let segments: LineSegment[] = [];

  if (hull.length >= 3) {
    const angleRad = (avgAngle * Math.PI) / 180;
    const ux = Math.cos(angleRad),
      uy = Math.sin(angleRad);

    const projections = hull.map((p, i) => ({
      idx: i,
      proj: p.x * ux + p.y * uy,
    }));
    projections.sort((a, b) => a.proj - b.proj);

    const extremes = [
      { key: "left-boundary", idx: projections[0].idx },
      { key: "right-boundary", idx: projections[projections.length - 1].idx },
    ];

    const DIST_WEIGHT = 1;
    const ANGLE_WEIGHT = 1;
    const span = Math.hypot(svgWidth, svgHeight);

    const potentialSegments = extremes.map(({ key, idx }) => {
      const p0 = hull[idx];
      const pCW = hull[(idx + 1) % hull.length];
      const pCCW = hull[(idx - 1 + hull.length) % hull.length];

      const makeLine = (p1: Point, p2: Point) => {
        const a = p2.y - p1.y;
        const b = p1.x - p2.x;
        const c = p2.x * p1.y - p1.x * p2.y;
        return { a, b, c };
      };
      const lineCW = makeLine(p0, pCW);
      const lineCCW = makeLine(p0, pCCW);

      const meanDist = ({ a, b, c }: { a: number; b: number; c: number }) => {
        const norm = Math.hypot(a, b);
        return (
          hull.reduce(
            (sum, p) => sum + Math.abs(a * p.x + b * p.y + c) / norm,
            0
          ) / hull.length
        );
      };
      const distCW = meanDist(lineCW);
      const distCCW = meanDist(lineCCW);

      const diffAngle = (p: Point) => {
        const theta = Math.atan2(p.y - p0.y, p.x - p0.x);
        const d = Math.abs(theta - angleRad);
        return Math.min(d, 2 * Math.PI - d);
      };
      const diffCW = diffAngle(pCW);
      const diffCCW = diffAngle(pCCW);

      const costCW = DIST_WEIGHT * distCW + ANGLE_WEIGHT * diffCW;
      const costCCW = DIST_WEIGHT * distCCW + ANGLE_WEIGHT * diffCCW;
      const pB = costCW < costCCW ? pCW : pCCW;

      const dx = (pB.x - p0.x) * svgWidth;
      const dy = (pB.y - p0.y) * svgHeight;

      // Handle nearly horizontal lines (avoid division by zero)
      if (Math.abs(dy) < 1e-6) {
        // For horizontal lines, extend from left to right at average y
        const avgY = ((1 - p0.y + (1 - pB.y)) * svgHeight) / 2;
        return {
          key,
          x1: 0,
          y1: avgY,
          x2: svgWidth,
          y2: avgY,
        };
      }

      const m = dx / dy;
      const c = p0.x * svgWidth - m * ((1 - p0.y) * svgHeight);

      // Validate that values are finite
      if (!isFinite(m) || !isFinite(c)) {
        return null;
      }

      const x1 = c;
      const x2 = m * svgHeight + c;

      // Validate final coordinates
      if (!isFinite(x1) || !isFinite(x2)) {
        return null;
      }

      return {
        key,
        x1: x1,
        y1: 0,
        x2: x2,
        y2: svgHeight,
      };
    });

    segments = potentialSegments.filter(
      (seg): seg is LineSegment => seg !== null
    );
  }

  const transitions = useTransition(segments, {
    keys: (seg) => seg.key,
    from: { opacity: 0, strokeDasharray: "12,8", strokeDashoffset: 20 },
    enter: (_item, idx) => ({
      opacity: 1,
      strokeDashoffset: 0,
      delay: delay + idx * 300,
    }),
    config: { duration: 800 },
  });

  if (hull.length < 3) return null;

  return (
    <g>
      {segments.map((seg) => (
        <React.Fragment key={`pts-${seg.key}`}>
          <circle
            cx={seg.x1}
            cy={seg.y1}
            r={8}
            fill="var(--color-green)"
            stroke="white"
            strokeWidth="2"
          />
          <circle
            cx={seg.x2}
            cy={seg.y2}
            r={8}
            fill="var(--color-green)"
            stroke="white"
            strokeWidth="2"
          />
        </React.Fragment>
      ))}
      {transitions((style, seg) => (
        <animated.line
          key={seg.key}
          style={style}
          x1={seg.x1}
          y1={seg.y1}
          x2={seg.x2}
          y2={seg.y2}
          stroke="var(--color-green)"
          strokeWidth="5"
          strokeDasharray="12,8"
        />
      ))}
    </g>
  );
};

export default AnimatedPrimaryBoundaryLines;
