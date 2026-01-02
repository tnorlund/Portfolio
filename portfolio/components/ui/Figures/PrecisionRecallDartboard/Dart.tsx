import React from "react";
import { animated, useSpring } from "@react-spring/web";
import type { DartProps } from "./types";

/**
 * Individual dart SVG element with fade-in animation.
 */
const Dart: React.FC<DartProps> = ({
  x,
  y,
  angle = 0,
  animationDelay = 0,
  size = 12,
}) => {
  const springProps = useSpring({
    from: { opacity: 0, scale: 0 },
    to: { opacity: 1, scale: 1 },
    delay: animationDelay,
    config: { tension: 300, friction: 20 },
  });

  const strokeWidth = size * 0.25;
  const halfSize = size * 0.5;

  return (
    <animated.g
      style={{
        opacity: springProps.opacity,
        transformOrigin: `${x}px ${y}px`,
        transform: springProps.scale.to((s) => `scale(${s})`),
      }}
    >
      <g transform={`translate(${x}, ${y}) rotate(${angle})`}>
        {/* Red X mark */}
        <line
          x1={-halfSize}
          y1={-halfSize}
          x2={halfSize}
          y2={halfSize}
          stroke="var(--color-red)"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
        />
        <line
          x1={halfSize}
          y1={-halfSize}
          x2={-halfSize}
          y2={halfSize}
          stroke="var(--color-red)"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
        />
      </g>
    </animated.g>
  );
};

export default Dart;
