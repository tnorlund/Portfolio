import React from "react";
import { useSpring, animated } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";

/**
 * Simple SVG coordinate system that plots points A, B, and C.
 * Fades / scales in once scrolled into view.
 */
const EmbeddingCoordinate: React.FC = () => {
  // Raw point coordinates
  const points: Record<string, [number, number]> = {
    A: [3, 2],
    B: [1, 1],
    C: [-2, -2],
  };

  // SVG sizing and helpers
  const width = 300;
  const height = 300;
  const padding = 40;

  const minX = -3;
  const maxX = 4;
  const minY = -3;
  const maxY = 3;

  // Map logical coordinates to SVG space
  const mapX = (x: number) =>
    padding + ((x - minX) / (maxX - minX)) * (width - 2 * padding);

  const mapY = (y: number) =>
    height - padding - ((y - minY) / (maxY - minY)) * (height - 2 * padding);

  // Fade / scale animation when in view
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });
  const [spring, api] = useSpring(() => ({
    opacity: 0,
    transform: "scale(0.8)",
  }));

  React.useEffect(() => {
    if (inView) {
      api.start({ opacity: 1, transform: "scale(1)" });
    }
  }, [inView, api]);

  return (
    <div style={{ display: "flex", justifyContent: "center" }} ref={ref}>
      <animated.svg width={width} height={height} style={spring}>
        {/* Axes */}
        <line
          x1={padding}
          y1={height / 2}
          x2={width - padding}
          y2={height / 2}
          stroke="var(--text-color)"
          strokeWidth={1}
        />
        <line
          x1={width / 2}
          y1={padding}
          x2={width / 2}
          y2={height - padding}
          stroke="var(--text-color)"
          strokeWidth={1}
        />

        {/* Points */}
        {Object.entries(points).map(([label, [x, y]]) => (
          <g key={label}>
            <circle cx={mapX(x)} cy={mapY(y)} r={5} fill="var(--text-color)" />
            <text
              x={mapX(x) + 8}
              y={mapY(y) - 8}
              fontSize={12}
              fill="var(--text-color)"
              alignmentBaseline="middle"
            >
              {label}
            </text>
          </g>
        ))}
      </animated.svg>
    </div>
  );
};

export default EmbeddingCoordinate;
