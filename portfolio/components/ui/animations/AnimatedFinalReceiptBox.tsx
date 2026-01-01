import React, { useMemo } from "react";
import { useTransition, animated } from "@react-spring/web";
import type { Point } from "../../../types/api";

interface AnimatedFinalReceiptBoxProps {
  finalReceiptBox: Point[];
  topLineCorners: Point[];
  bottomLineCorners: Point[];
  leftmostHullPoint: Point | null;
  rightmostHullPoint: Point | null;
  avgAngleRad: number;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

/**
 * Animated visualization of the final receipt bounding box.
 *
 * Shows:
 * 1. Extended boundary lines (top/bottom from line edges, left/right perpendicular)
 * 2. Intersection points (the 4 corners)
 * 3. Final receipt quadrilateral
 */
const AnimatedFinalReceiptBox: React.FC<AnimatedFinalReceiptBoxProps> = ({
  finalReceiptBox,
  topLineCorners,
  bottomLineCorners,
  leftmostHullPoint,
  rightmostHullPoint,
  avgAngleRad,
  svgWidth,
  svgHeight,
  delay,
}) => {
  // Helper to convert normalized coords to SVG coords
  const toSvg = (p: Point) => ({
    x: p.x * svgWidth,
    y: (1 - p.y) * svgHeight,
  });

  // Memoize corner calculations
  const corners = useMemo(() => {
    const cornerLabels = [
      "Top-Left",
      "Top-Right",
      "Bottom-Right",
      "Bottom-Left",
    ];
    return finalReceiptBox.length === 4
      ? finalReceiptBox.map((corner: Point, index: number) => ({
          ...corner,
          svgX: corner.x * svgWidth,
          svgY: (1 - corner.y) * svgHeight,
          label: cornerLabels[index],
          index,
        }))
      : [];
  }, [finalReceiptBox, svgWidth, svgHeight]);

  // Create visual boundary lines for animation
  const boundaryLines = useMemo(() => {
    if (
      topLineCorners.length < 4 ||
      bottomLineCorners.length < 4 ||
      !leftmostHullPoint ||
      !rightmostHullPoint
    ) {
      return [];
    }

    const lines: Array<{
      x1: number;
      y1: number;
      x2: number;
      y2: number;
      color: string;
      key: string;
    }> = [];

    const margin = 200; // Extension beyond viewport

    // Top edge direction
    const topLeft = toSvg(topLineCorners[0]);
    const topRight = toSvg(topLineCorners[1]);
    const topDx = topRight.x - topLeft.x;
    const topDy = topRight.y - topLeft.y;
    const topLen = Math.sqrt(topDx * topDx + topDy * topDy);
    const topUnitX = topDx / topLen;
    const topUnitY = topDy / topLen;

    // Top boundary line (extended)
    lines.push({
      x1: topLeft.x - topUnitX * margin,
      y1: topLeft.y - topUnitY * margin,
      x2: topRight.x + topUnitX * margin,
      y2: topRight.y + topUnitY * margin,
      color: "var(--color-yellow)",
      key: "top",
    });

    // Bottom edge direction
    const bottomLeft = toSvg(bottomLineCorners[2]);
    const bottomRight = toSvg(bottomLineCorners[3]);
    const bottomDx = bottomRight.x - bottomLeft.x;
    const bottomDy = bottomRight.y - bottomLeft.y;
    const bottomLen = Math.sqrt(bottomDx * bottomDx + bottomDy * bottomDy);
    const bottomUnitX = bottomDx / bottomLen;
    const bottomUnitY = bottomDy / bottomLen;

    // Bottom boundary line (extended)
    lines.push({
      x1: bottomLeft.x - bottomUnitX * margin,
      y1: bottomLeft.y - bottomUnitY * margin,
      x2: bottomRight.x + bottomUnitX * margin,
      y2: bottomRight.y + bottomUnitY * margin,
      color: "var(--color-yellow)",
      key: "bottom",
    });

    // Left/right edge direction (perpendicular to average angle)
    const leftEdgeAngle = avgAngleRad + Math.PI / 2;
    const leftDirX = Math.cos(leftEdgeAngle);
    const leftDirY = -Math.sin(leftEdgeAngle); // Flip for SVG

    // Left boundary line through leftmost hull point
    const leftHullSvg = toSvg(leftmostHullPoint);
    lines.push({
      x1: leftHullSvg.x - leftDirX * margin,
      y1: leftHullSvg.y - leftDirY * margin,
      x2: leftHullSvg.x + leftDirX * margin,
      y2: leftHullSvg.y + leftDirY * margin,
      color: "var(--color-green)",
      key: "left",
    });

    // Right boundary line through rightmost hull point
    const rightHullSvg = toSvg(rightmostHullPoint);
    lines.push({
      x1: rightHullSvg.x - leftDirX * margin,
      y1: rightHullSvg.y - leftDirY * margin,
      x2: rightHullSvg.x + leftDirX * margin,
      y2: rightHullSvg.y + leftDirY * margin,
      color: "var(--color-green)",
      key: "right",
    });

    return lines;
  }, [
    topLineCorners,
    bottomLineCorners,
    leftmostHullPoint,
    rightmostHullPoint,
    avgAngleRad,
    svgWidth,
    svgHeight,
  ]);

  // Memoize polygon points string for the final quadrilateral
  const polygonPoints = useMemo(
    () =>
      corners.length === 4
        ? corners.map((corner) => `${corner.svgX},${corner.svgY}`).join(" ")
        : "",
    [corners]
  );

  // Animation sequence:
  // 1. Show boundary lines (delay)
  // 2. Show intersection points (delay + 800)
  // 3. Show final quadrilateral (delay + 1600)

  // Animation for boundary lines
  const boundaryTransitions = useTransition(boundaryLines, {
    keys: (line) => `boundary-${line.key}`,
    from: { opacity: 0, strokeDasharray: "20,10", strokeDashoffset: 40 },
    enter: (_item, index) => ({
      opacity: 0.7,
      strokeDashoffset: 0,
      delay: delay + index * 200,
    }),
    config: { duration: 800 },
  });

  // Animation for corner points (intersection points)
  const cornerTransitions = useTransition(corners, {
    keys: (corner: (typeof corners)[0]) => `final-corner-${corner.index}`,
    from: { opacity: 0, scale: 0 },
    enter: (_item, index) => ({
      opacity: 1,
      scale: 1,
      delay: delay + 800 + index * 200,
    }),
    config: { duration: 600 },
  });

  // Animation for the final polygon outline
  const polygonTransition = useTransition(
    polygonPoints ? [polygonPoints] : [],
    {
      keys: (points) => `final-polygon-${points}`,
      from: { opacity: 0 },
      enter: {
        opacity: 1,
        delay: delay + 1600,
      },
      config: { duration: 800 },
    }
  );

  // Don't render if we don't have a valid quadrilateral
  if (finalReceiptBox.length !== 4) {
    return null;
  }

  return (
    <g>
      {/* Show boundary lines (yellow top/bottom, green left/right) */}
      {boundaryTransitions((style, line) => (
        <animated.line
          key={`boundary-${line.key}`}
          style={style}
          x1={line.x1}
          y1={line.y1}
          x2={line.x2}
          y2={line.y2}
          stroke={line.color}
          strokeWidth="6"
          strokeDasharray="10,5"
        />
      ))}

      {/* Show intersection points (where boundaries meet) */}
      {cornerTransitions((style, corner) => (
        <animated.g key={`corner-${corner.index}`} style={style}>
          <circle
            cx={corner.svgX}
            cy={corner.svgY}
            r={8}
            fill="var(--color-blue)"
            stroke="white"
            strokeWidth="2"
          />
        </animated.g>
      ))}

      {/* Final receipt quadrilateral */}
      {polygonTransition((style, points) => (
        <animated.polygon
          key="final-receipt-polygon"
          style={style}
          points={points}
          fill="var(--color-blue)"
          fillOpacity={0.2}
          stroke="var(--color-blue)"
          strokeWidth="6"
          opacity={1}
        />
      ))}
    </g>
  );
};

export default AnimatedFinalReceiptBox;
