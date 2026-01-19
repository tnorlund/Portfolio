import React, { useEffect, useState } from "react";
import type { Point } from "../../../types/api";
import type { CropViewBox } from "../Figures/utils/smartCrop";

interface AnimatedConvexHullProps {
  hullPoints: Point[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
  showIndices?: boolean;
  cropInfo?: CropViewBox | null;
  fullImageWidth?: number;
  fullImageHeight?: number;
}

const AnimatedConvexHull: React.FC<AnimatedConvexHullProps> = ({
  hullPoints,
  svgWidth,
  svgHeight,
  delay,
  showIndices,
  cropInfo,
  fullImageWidth,
  fullImageHeight,
}) => {
  const [visiblePoints, setVisiblePoints] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      const interval = setInterval(() => {
        setVisiblePoints((prev) => {
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

  // Transform normalized coordinates to SVG coordinates
  const transformX = (normX: number) => {
    if (cropInfo && fullImageWidth) {
      return normX * fullImageWidth - cropInfo.x;
    }
    return normX * svgWidth;
  };

  const transformY = (normY: number) => {
    if (cropInfo && fullImageHeight) {
      return (1 - normY) * fullImageHeight - cropInfo.y;
    }
    return (1 - normY) * svgHeight;
  };

  const svgPoints = hullPoints.map((point) => ({
    x: transformX(point.x),
    y: transformY(point.y),
  }));

  const visibleSvgPoints = svgPoints.slice(0, visiblePoints);

  if (visibleSvgPoints.length < 2) {
    return (
      <>
        {visibleSvgPoints.map((point, index) => (
          <g key={index}>
            <circle
              cx={point.x}
              cy={point.y}
              r={12}
              fill="var(--color-red)"
              opacity={1}
              strokeWidth="2"
            />
            {showIndices && (
              <text
                x={point.x}
                y={point.y - 15}
                fill="white"
                stroke="black"
                strokeWidth="0.5"
                fontSize="14"
                fontWeight="bold"
                textAnchor="middle"
                style={{
                  opacity: 1,
                  transition: "opacity 200ms ease-in-out",
                }}
              >
                {index}
              </text>
            )}
          </g>
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
      {/* Hull vertices */}
      {visibleSvgPoints.map((point, index) => (
        <g key={index}>
          <circle
            cx={point.x}
            cy={point.y}
            r={12}
            fill="var(--color-red)"
            opacity={1}
            strokeWidth="2"
          />
          {showIndices && (
            <text
              x={point.x}
              y={point.y - 15}
              fill="white"
              stroke="black"
              strokeWidth="0.5"
              fontSize="14"
              fontWeight="bold"
              textAnchor="middle"
              style={{
                opacity: 1,
                transition: "opacity 200ms ease-in-out",
              }}
            >
              {index}
            </text>
          )}
        </g>
      ))}
      {/* Hull edges */}
      <path
        d={finalPath}
        fill="none"
        stroke="var(--color-red)"
        strokeWidth="4"
        opacity={1}
      />
    </>
  );
};

export default AnimatedConvexHull;
