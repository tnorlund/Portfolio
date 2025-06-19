import React, { useEffect, useState } from "react";
import type { Line, Point } from "../../../types/api";
import { findLineEdgesAtSecondaryExtremes } from "../../../utils/geometry";

interface AnimatedOrientedAxesProps {
  hull: Point[];
  centroid: Point;
  lines: Line[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedOrientedAxes: React.FC<AnimatedOrientedAxesProps> = ({
  hull,
  centroid,
  lines,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const [visibleElements, setVisibleElements] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      const interval = setInterval(() => {
        setVisibleElements((prev) => {
          if (prev >= 3) {
            // 0: primary axis, 1: secondary axis, 2: extent points
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
  }, [hull, lines]);

  if (hull.length === 0 || lines.length === 0) return null;

  const computedAngles = lines
    .map((line) => {
      const dx = line.bottom_right.x - line.bottom_left.x;
      const dy = line.bottom_right.y - line.bottom_left.y;
      return (Math.atan2(dy, dx) * 180) / Math.PI;
    })
    .filter((angle) => Math.abs(angle) > 1e-3);

  const avgAngle =
    computedAngles.length > 0
      ? computedAngles.reduce((sum, a) => sum + a, 0) / computedAngles.length
      : 0;

  const centroidX = centroid.x * svgWidth;
  const centroidY = (1 - centroid.y) * svgHeight;

  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad; // Along text lines
  const secondaryAxisAngle = angleRad + Math.PI / 2; // Perpendicular to text lines

  const axisLength = 200;

  // Primary axis: along text line direction (GREEN)
  const primaryAxis = {
    x1: centroidX,
    y1: centroidY,
    x2: centroidX + axisLength * Math.cos(primaryAxisAngle),
    y2: centroidY + axisLength * Math.sin(primaryAxisAngle),
  };

  // Secondary axis: perpendicular to text lines, pointing UP in SVG coords (YELLOW)
  // In SVG: negative Y is up, positive Y is down
  // We want the yellow axis to point up, so we subtract from Y
  const secondaryAxis = {
    x1: centroidX,
    y1: centroidY,
    x2: centroidX - axisLength * Math.sin(primaryAxisAngle), // Perpendicular X component
    y2: centroidY - axisLength * Math.cos(primaryAxisAngle), // Perpendicular Y component (negative = up)
  };

  // Find extremes along each axis using OCR coordinates (not SVG coordinates)
  let minPrimary = Infinity,
    maxPrimary = -Infinity;
  let minSecondary = Infinity,
    maxSecondary = -Infinity;
  let primaryMinPoint = hull[0],
    primaryMaxPoint = hull[0];
  let secondaryMinPoint = hull[0],
    secondaryMaxPoint = hull[0];

  const hullProjections = hull.map((point) => {
    // Work in OCR coordinate space for projections
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;

    // Project onto primary axis (along text lines)
    const primaryProjection =
      relX * Math.cos(primaryAxisAngle) + relY * Math.sin(primaryAxisAngle);

    // Project onto secondary axis (perpendicular to text lines)
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    return {
      point: { x: point.x * svgWidth, y: (1 - point.y) * svgHeight }, // Convert to SVG for rendering
      primaryProjection,
      secondaryProjection,
      originalPoint: point,
    };
  });

  hullProjections.forEach(
    ({ point, primaryProjection, secondaryProjection }) => {
      // Primary extremes: left/right along text line direction
      if (primaryProjection < minPrimary) {
        minPrimary = primaryProjection;
        primaryMinPoint = point; // Leftmost point
      }
      if (primaryProjection > maxPrimary) {
        maxPrimary = primaryProjection;
        primaryMaxPoint = point; // Rightmost point
      }

      // Secondary extremes: top/bottom perpendicular to text lines
      // Higher secondary projection = higher in OCR coords = lower in SVG coords = top
      if (secondaryProjection < minSecondary) {
        minSecondary = secondaryProjection;
        secondaryMinPoint = point; // Bottom point (lower in OCR coords)
      }
      if (secondaryProjection > maxSecondary) {
        maxSecondary = secondaryProjection;
        secondaryMaxPoint = point; // Top point (higher in OCR coords)
      }
    }
  );

  // Primary extremes are left/right points along text direction (GREEN)
  const primaryExtremePoints = [primaryMinPoint, primaryMaxPoint];

  // Get the secondary boundary points to exclude them from any dots
  // (since AnimatedSecondaryBoundaryLines handles top/bottom with YELLOW)
  const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
    lines,
    hull,
    centroid,
    avgAngle
  );

  // Convert boundary points to SVG coordinates for comparison
  const boundaryPointsInSvg = [...topEdge, ...bottomEdge].map((point) => ({
    x: point.x * svgWidth,
    y: (1 - point.y) * svgHeight,
  }));

  // Helper function to check if a point is a boundary point
  const isBoundaryPoint = (point: { x: number; y: number }) => {
    return boundaryPointsInSvg.some(
      (bp) => Math.abs(bp.x - point.x) < 1 && Math.abs(bp.y - point.y) < 1
    );
  };

  // Filter primary extremes to exclude any that are also boundary points
  const filteredPrimaryExtremes = primaryExtremePoints.filter(
    (point) => !isBoundaryPoint(point)
  );

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
      {/* Primary axis (along average line direction) */}
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

      {/* Secondary axis (perpendicular to average line direction) */}
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

      {/* Primary extremes: left/right points along text direction (green) */}
      {visibleElements >= 3 && (
        <>
          {filteredPrimaryExtremes.map((point, idx) => (
            <circle
              key={`primary-extreme-${idx}`}
              cx={point.x}
              cy={point.y}
              r={10}
              fill="var(--color-green)"
              stroke="white"
              strokeWidth="2"
              opacity={0.9}
            />
          ))}
        </>
      )}
    </>
  );
};

export default AnimatedOrientedAxes;
