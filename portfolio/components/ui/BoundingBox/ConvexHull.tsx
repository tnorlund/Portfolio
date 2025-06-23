import React, { useEffect, useState } from "react";
import type { Point } from "../../../types/api";

interface ConvexHullProps {
  hullPoints: Point[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const ConvexHull: React.FC<ConvexHullProps> = ({
  hullPoints,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const [visiblePoints, setVisiblePoints] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      const interval = setInterval(() => {
        setVisiblePoints(prev => {
          if (prev >= hullPoints.length) {
            clearInterval(interval);
            return prev;
          }
          return prev + 1;
        });
      }, 200);
      return () => clearInterval(interval);
    }, delay);
    return () => clearTimeout(timer);
  }, [delay, hullPoints.length]);

  useEffect(() => {
    setVisiblePoints(0);
  }, [hullPoints]);

  if (hullPoints.length === 0) return null;

  const svgPoints = hullPoints.map(point => ({
    x: point.x * svgWidth,
    y: (1 - point.y) * svgHeight,
  }));
  const visibleSvgPoints = svgPoints.slice(0, visiblePoints);

  if (visibleSvgPoints.length < 2) {
    return (
      <>
        {visibleSvgPoints.map((point, index) => (
          <circle
            key={index}
            cx={point.x}
            cy={point.y}
            r={12}
            fill="var(--color-red)"
            opacity={1}
            strokeWidth={2}
          />
        ))}
      </>
    );
  }

  const pathData = visibleSvgPoints.reduce((acc, point, index) => {
    if (index === 0) return `M ${point.x} ${point.y}`;
    return `${acc} L ${point.x} ${point.y}`;
  }, "");
  const finalPath =
    visiblePoints >= hullPoints.length ? `${pathData} Z` : pathData;

  return (
    <>
      {visibleSvgPoints.map((point, index) => (
        <circle
          key={index}
          cx={point.x}
          cy={point.y}
          r={12}
          fill="var(--color-red)"
          opacity={1}
          strokeWidth={2}
        />
      ))}
      <path
        d={finalPath}
        fill="none"
        stroke="var(--color-red)"
        strokeWidth={4}
        opacity={1}
      />
      {visiblePoints >= hullPoints.length && (
        <path d={finalPath} fill="var(--color-red)" opacity={0.1} />
      )}
    </>
  );
};

export default ConvexHull;
