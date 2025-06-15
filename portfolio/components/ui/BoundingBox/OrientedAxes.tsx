import React, { useEffect, useState } from "react";
import type { Line, Point } from "../../../types/api";

interface OrientedAxesProps {
  hull: Point[];
  centroid: Point;
  lines: Line[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const OrientedAxes: React.FC<OrientedAxesProps> = ({
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
        setVisibleElements(prev => {
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
  }, [hull, lines]);

  if (hull.length === 0 || lines.length === 0) return null;

  const computedAngles = lines
    .map(line => {
      const dx = line.bottom_right.x - line.bottom_left.x;
      const dy = line.bottom_right.y - line.bottom_left.y;
      return (Math.atan2(dy, dx) * 180) / Math.PI;
    })
    .filter(angle => Math.abs(angle) > 1e-3);

  const avgAngle =
    computedAngles.length > 0
      ? computedAngles.reduce((sum, a) => sum + a, 0) / computedAngles.length
      : 0;

  const centroidX = centroid.x * svgWidth;
  const centroidY = (1 - centroid.y) * svgHeight;

  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;
  const secondaryAxisAngle = angleRad + Math.PI / 2;

  const axisLength = 200;

  const primaryAxis = {
    x1: centroidX,
    y1: centroidY,
    x2: centroidX + axisLength * Math.cos(primaryAxisAngle),
    y2: centroidY + axisLength * Math.sin(primaryAxisAngle),
  };

  const secondaryAxis = {
    x1: centroidX,
    y1: centroidY,
    x2: centroidX,
    y2: centroidY - axisLength,
  };

  let minPrimary = Infinity,
    maxPrimary = -Infinity;
  let minSecondary = Infinity,
    maxSecondary = -Infinity;
  let primaryMinPoint = hull[0],
    primaryMaxPoint = hull[0];
  let secondaryMinPoint = hull[0],
    secondaryMaxPoint = hull[0];

  const hullProjections = hull.map(point => {
    const px = point.x * svgWidth;
    const py = (1 - point.y) * svgHeight;
    const relX = px - centroidX;
    const relY = py - centroidY;
    const primaryProjection = relX * Math.cos(primaryAxisAngle) + relY * Math.sin(primaryAxisAngle);
    const secondaryProjection = relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    return { point: { x: px, y: py }, primaryProjection, secondaryProjection };
  });

  hullProjections.forEach(({ point, primaryProjection, secondaryProjection }) => {
    if (primaryProjection < minPrimary) {
      minPrimary = primaryProjection;
      primaryMinPoint = point;
    }
    if (primaryProjection > maxPrimary) {
      maxPrimary = primaryProjection;
      primaryMaxPoint = point;
    }
    if (secondaryProjection < minSecondary) {
      minSecondary = secondaryProjection;
      secondaryMinPoint = point;
    }
    if (secondaryProjection > maxSecondary) {
      maxSecondary = secondaryProjection;
      secondaryMaxPoint = point;
    }
  });

  return (
    <>
      {visibleElements >= 1 && (
        <line
          x1={primaryAxis.x1}
          y1={primaryAxis.y1}
          x2={primaryAxis.x2}
          y2={primaryAxis.y2}
          stroke="var(--color-green)"
          strokeWidth={10}
          opacity={0.8}
        />
      )}
      {visibleElements >= 2 && (
        <line
          x1={secondaryAxis.x1}
          y1={secondaryAxis.y1}
          x2={secondaryAxis.x2}
          y2={secondaryAxis.y2}
          stroke="var(--color-yellow)"
          strokeWidth={10}
          opacity={0.8}
        />
      )}
      {visibleElements >= 3 && [primaryMinPoint, primaryMaxPoint].map((point, index) => (
        <circle
          key={`primary-${index}`}
          cx={point.x}
          cy={point.y}
          r={10}
          fill="var(--color-green)"
          stroke="white"
          strokeWidth={2}
          opacity={0.9}
        />
      ))}
      {visibleElements >= 3 && [secondaryMinPoint, secondaryMaxPoint].map((point, idx) => (
        <circle
          key={`secondary-${idx}`}
          cx={point.x}
          cy={point.y}
          r={10}
          fill="var(--color-green)"
          stroke="white"
          strokeWidth={2}
          opacity={0.9}
        />
      ))}
    </>
  );
};

export default OrientedAxes;
