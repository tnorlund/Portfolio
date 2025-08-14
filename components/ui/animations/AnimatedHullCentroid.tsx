import React from "react";
import { animated, useSpring } from "@react-spring/web";
import type { Point } from "../../../types/api";

interface AnimatedHullCentroidProps {
  centroid: Point;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedHullCentroid: React.FC<AnimatedHullCentroidProps> = ({
  centroid,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const centroidSpring = useSpring({
    from: { opacity: 0, scale: 0 },
    to: { opacity: 1, scale: 1 },
    delay: delay,
    config: { duration: 600 },
  });

  const centroidX = centroid.x * svgWidth;
  const centroidY = (1 - centroid.y) * svgHeight;

  return (
    <animated.circle
      cx={centroidX}
      cy={centroidY}
      r={15}
      fill="var(--color-red)"
      strokeWidth="3"
      style={{
        opacity: centroidSpring.opacity,
        transform: centroidSpring.scale.to(s => `scale(${s})`),
        transformOrigin: `${centroidX}px ${centroidY}px`,
      }}
    />
  );
};

export default AnimatedHullCentroid;
