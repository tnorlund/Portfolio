import React, { useEffect, useState } from "react";
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
  const [shouldAnimate, setShouldAnimate] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setShouldAnimate(true);
    }, animationDelay);
    return () => clearTimeout(timer);
  }, [animationDelay]);

  const springProps = useSpring({
    opacity: shouldAnimate ? 1 : 0,
    scale: shouldAnimate ? 1 : 0,
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
        {/* Red circle around the X */}
        <circle
          cx={0}
          cy={0}
          r={size * 0.75}
          fill="none"
          stroke="var(--color-red)"
          strokeWidth={strokeWidth}
        />
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
