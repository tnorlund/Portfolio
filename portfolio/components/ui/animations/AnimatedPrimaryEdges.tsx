import React from "react";
import { useTransition, animated } from "@react-spring/web";
import type { Line, Point } from "../../../types/api";
import { findBoundaryLinesWithSkew } from "../../../utils/receiptGeometry";

interface AnimatedPrimaryEdgesProps {
  lines: Line[];
  hull: Point[];
  centroid: Point;
  avgAngle: number;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedPrimaryEdges: React.FC<AnimatedPrimaryEdgesProps> = ({
  lines,
  hull,
  centroid,
  avgAngle,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const { leftEdgePoints, rightEdgePoints } = findBoundaryLinesWithSkew(
    lines,
    hull,
    centroid,
    avgAngle
  );

  const allEdgePoints = [...leftEdgePoints, ...rightEdgePoints];

  const edgeTransitions = useTransition(allEdgePoints, {
    keys: point => `edge-${point.x}-${point.y}`,
    from: { opacity: 0, scale: 0 },
    enter: (item, index) => ({
      opacity: 1,
      scale: 1,
      delay: delay + index * 100,
    }),
    config: { duration: 400 },
  });

  return (
    <g>
      {edgeTransitions((style, edgePoint) => (
        <animated.circle
          style={style}
          cx={edgePoint.x * svgWidth}
          cy={(1 - edgePoint.y) * svgHeight}
          r="6"
          fill={"var(--color-blue)"}
          stroke="white"
          strokeWidth="1"
        />
      ))}
    </g>
  );
};

export default AnimatedPrimaryEdges;
