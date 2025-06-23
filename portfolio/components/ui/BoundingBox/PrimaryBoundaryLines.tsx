import React from "react";
import { animated, useTransition } from "@react-spring/web";
import type { Point } from "../../../types/api";

interface LineSegment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  key: string;
}

interface PrimaryBoundaryLinesProps {
  segments: LineSegment[];
  delay: number;
}

const PrimaryBoundaryLines: React.FC<PrimaryBoundaryLinesProps> = ({
  segments,
  delay,
}) => {
  const transitions = useTransition(segments, {
    keys: seg => seg.key,
    from: { opacity: 0, strokeDasharray: "12,8", strokeDashoffset: 20 },
    enter: (_item, idx) => ({
      opacity: 1,
      strokeDashoffset: 0,
      delay: delay + idx * 300,
    }),
    config: { duration: 800 },
  });

  return (
    <g>
      {segments.map(seg => (
        <React.Fragment key={`pts-${seg.key}`}>
          <circle
            cx={seg.x1}
            cy={seg.y1}
            r={8}
            fill="var(--color-green)"
            stroke="white"
            strokeWidth={2}
          />
          <circle
            cx={seg.x2}
            cy={seg.y2}
            r={8}
            fill="var(--color-green)"
            stroke="white"
            strokeWidth={2}
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
          strokeWidth={5}
          strokeDasharray="12,8"
        />
      ))}
    </g>
  );
};

export default PrimaryBoundaryLines;
export type { LineSegment, PrimaryBoundaryLinesProps };
