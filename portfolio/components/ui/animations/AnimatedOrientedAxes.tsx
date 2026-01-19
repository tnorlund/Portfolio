import React, { useEffect, useState } from "react";
import type { Point } from "../../../types/api";
import type { CropViewBox } from "../Figures/utils/smartCrop";

interface AnimatedOrientedAxesProps {
  hull: Point[];
  centroid: Point;
  avgAngleRad: number;
  leftmostHullPoint: Point | null;
  rightmostHullPoint: Point | null;
  svgWidth: number;
  svgHeight: number;
  delay: number;
  cropInfo?: CropViewBox | null;
  fullImageWidth?: number;
  fullImageHeight?: number;
}

/**
 * Animated visualization of the average edge angle and hull extremes.
 *
 * Shows:
 * 1. The average angle direction (along receipt tilt) - GREEN
 * 2. The perpendicular direction (for left/right edges) - YELLOW
 * 3. The leftmost and rightmost hull points along the average angle
 */
const AnimatedOrientedAxes: React.FC<AnimatedOrientedAxesProps> = ({
  hull,
  centroid,
  avgAngleRad,
  leftmostHullPoint,
  rightmostHullPoint,
  svgWidth,
  svgHeight,
  delay,
  cropInfo,
  fullImageWidth,
  fullImageHeight,
}) => {
  const [visibleElements, setVisibleElements] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      const interval = setInterval(() => {
        setVisibleElements((prev) => {
          if (prev >= 3) {
            clearInterval(interval);
            return prev;
          }
          return prev + 1;
        });
      }, 400);
      return () => clearInterval(interval);
    }, delay);

    return () => clearTimeout(timer);
  }, [delay]);

  useEffect(() => {
    setVisibleElements(0);
  }, [hull, avgAngleRad]);

  if (hull.length === 0) return null;

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

  const centroidX = transformX(centroid.x);
  const centroidY = transformY(centroid.y);

  // Primary axis: along text line direction (GREEN)
  const primaryAxisAngle = avgAngleRad;
  // Secondary axis: perpendicular to text lines (YELLOW)
  const secondaryAxisAngle = avgAngleRad + Math.PI / 2;

  const axisLength = 200;

  // Primary axis pointing right along the receipt tilt
  const primaryAxis = {
    x1: centroidX,
    y1: centroidY,
    x2: centroidX + axisLength * Math.cos(primaryAxisAngle),
    y2: centroidY - axisLength * Math.sin(primaryAxisAngle), // Flip Y for SVG
  };

  // Secondary axis pointing perpendicular (for left/right edge direction)
  const secondaryAxis = {
    x1: centroidX,
    y1: centroidY,
    x2: centroidX + axisLength * Math.cos(secondaryAxisAngle),
    y2: centroidY - axisLength * Math.sin(secondaryAxisAngle), // Flip Y for SVG
  };

  return (
    <>
      <defs>
        <marker
          id="axis-arrow-primary"
          markerWidth="8"
          markerHeight="8"
          refX="0"
          refY="3"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <path d="M0,0 L0,6 L6,3 Z" fill="var(--color-green)" />
        </marker>
        <marker
          id="axis-arrow-secondary"
          markerWidth="8"
          markerHeight="8"
          refX="0"
          refY="3"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <path d="M0,0 L0,6 L6,3 Z" fill="var(--color-yellow)" />
        </marker>
      </defs>

      {/* Primary axis: along average edge direction (GREEN) */}
      {visibleElements >= 1 && (
        <line
          x1={primaryAxis.x1}
          y1={primaryAxis.y1}
          x2={primaryAxis.x2}
          y2={primaryAxis.y2}
          stroke="var(--color-green)"
          strokeWidth="10"
          opacity={0.8}
          markerEnd="url(#axis-arrow-primary)"
        />
      )}

      {/* Secondary axis: perpendicular direction for left/right edges (YELLOW) */}
      {visibleElements >= 2 && (
        <line
          x1={secondaryAxis.x1}
          y1={secondaryAxis.y1}
          x2={secondaryAxis.x2}
          y2={secondaryAxis.y2}
          stroke="var(--color-yellow)"
          strokeWidth="10"
          opacity={0.8}
          markerEnd="url(#axis-arrow-secondary)"
        />
      )}

      {/* Left/right extreme points along the average angle (GREEN) */}
      {visibleElements >= 3 && leftmostHullPoint && rightmostHullPoint && (
        <>
          <circle
            cx={transformX(leftmostHullPoint.x)}
            cy={transformY(leftmostHullPoint.y)}
            r={10}
            fill="var(--color-green)"
            stroke="white"
            strokeWidth="2"
            opacity={0.9}
          />
          <circle
            cx={transformX(rightmostHullPoint.x)}
            cy={transformY(rightmostHullPoint.y)}
            r={10}
            fill="var(--color-green)"
            stroke="white"
            strokeWidth="2"
            opacity={0.9}
          />
        </>
      )}
    </>
  );
};

export default AnimatedOrientedAxes;
