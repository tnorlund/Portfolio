import React from "react";
import { animated, useSpring } from "@react-spring/web";
import type { Line, Point } from "../../../types/api";
import {
  computeHullCentroid,
  computeReceiptBoxFromLineEdges,
} from "../../../utils/geometry";

interface AnimatedReceiptFromHullProps {
  hull: Point[];
  lines: Line[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedReceiptFromHull: React.FC<AnimatedReceiptFromHullProps> = ({
  hull,
  lines,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const boxSpring = useSpring({
    from: { opacity: 0 },
    to: { opacity: 1 },
    delay: delay,
    config: { duration: 800 },
  });

  const centroidSpring = useSpring({
    from: { opacity: 0 },
    to: { opacity: 1 },
    delay: delay + 400,
    config: { duration: 600 },
  });

  if (hull.length === 0 || lines.length === 0) return null;

  const hullCentroid = computeHullCentroid(hull);

  console.log(`lines[0]`);
  const avgAngle =
    lines.reduce((sum, line) => sum + line.angle_degrees, 0) / lines.length;

  const receiptCorners = computeReceiptBoxFromLineEdges(
    lines,
    hull,
    hullCentroid,
    avgAngle
  );

  if (receiptCorners.length !== 4) return null;

  const svgCorners = receiptCorners.map((corner) => ({
    x: corner.x * svgWidth,
    y: (1 - corner.y) * svgHeight,
  }));

  const points = svgCorners.map((c) => `${c.x},${c.y}`).join(" ");

  const receiptCentroidX = svgCorners.reduce((sum, c) => sum + c.x, 0) / 4;
  const receiptCentroidY = svgCorners.reduce((sum, c) => sum + c.y, 0) / 4;

  return (
    <>
      <animated.polygon
        style={boxSpring}
        points={points}
        fill="none"
        stroke="var(--color-blue)"
        strokeWidth="4"
      />
      <animated.circle
        cx={receiptCentroidX}
        cy={receiptCentroidY}
        r={12}
        fill="var(--color-blue)"
        strokeWidth="2"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

export default AnimatedReceiptFromHull;
