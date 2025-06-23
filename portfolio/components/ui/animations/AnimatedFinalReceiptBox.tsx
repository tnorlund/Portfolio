import React, { useMemo } from "react";
import { useTransition, animated } from "@react-spring/web";
import type { Point } from "../../../types/api";
import {
  computeReceiptBoxFromBoundaries,
  type BoundaryLine,
} from "../../../utils/receipt/boundingBox";

interface AnimatedFinalReceiptBoxProps {
  boundaries: {
    top: BoundaryLine | null;
    bottom: BoundaryLine | null;
    left: BoundaryLine | null;
    right: BoundaryLine | null;
  };
  fallbackCentroid: Point | null;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedFinalReceiptBox: React.FC<AnimatedFinalReceiptBoxProps> = ({
  boundaries,
  fallbackCentroid,
  svgWidth,
  svgHeight,
  delay,
}) => {
  // Step 9: Compute Final Receipt Quadrilateral from boundaries
  const finalReceiptBox = useMemo(() => {
    const { top, bottom, left, right } = boundaries;

    if (!top || !bottom || !left || !right || !fallbackCentroid) {
      return [];
    }

    return computeReceiptBoxFromBoundaries(
      top,
      bottom,
      left,
      right,
      fallbackCentroid
    );
  }, [boundaries, fallbackCentroid]);

  // Memoize corner calculations to avoid recalculating on every render
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
    const { top, bottom, left, right } = boundaries;
    const lines: Array<{
      x1: number;
      y1: number;
      x2: number;
      y2: number;
      color: string;
      key: string;
    }> = [];

    const margin = 100; // Extension beyond viewport

    // Top boundary (yellow)
    if (top) {
      if (top.isVertical && top.x !== undefined) {
        lines.push({
          x1: top.x * svgWidth,
          y1: -margin,
          x2: top.x * svgWidth,
          y2: svgHeight + margin,
          color: "var(--color-yellow)",
          key: "top",
        });
      } else if (top.isInverted) {
        // Top boundary is inverted: x = slope * y + intercept
        // We need to calculate X values for Y values at the extended viewport edges
        // Note: In normalized coords, y increases upward, but in SVG y increases downward
        const y1_normalized = 1 + margin / svgHeight;  // Above the top (in normalized space)
        const y2_normalized = -margin / svgHeight;      // Below the bottom (in normalized space)
        const x1 = top.slope * y1_normalized + top.intercept;
        const x2 = top.slope * y2_normalized + top.intercept;
        lines.push({
          x1: x1 * svgWidth,
          y1: -margin,
          x2: x2 * svgWidth,
          y2: svgHeight + margin,
          color: "var(--color-yellow)",
          key: "top",
        });
      } else if (!top.isVertical) {
        const y1 = top.slope * -margin + top.intercept;
        const y2 = top.slope * (svgWidth + margin) + top.intercept;
        lines.push({
          x1: -margin,
          y1: (1 - y1) * svgHeight,
          x2: svgWidth + margin,
          y2: (1 - y2) * svgHeight,
          color: "var(--color-yellow)",
          key: "top",
        });
      }
    }

    // Bottom boundary (yellow)
    if (bottom) {
      if (bottom.isVertical && bottom.x !== undefined) {
        lines.push({
          x1: bottom.x * svgWidth,
          y1: -margin,
          x2: bottom.x * svgWidth,
          y2: svgHeight + margin,
          color: "var(--color-yellow)",
          key: "bottom",
        });
      } else if (bottom.isInverted) {
        // Bottom boundary is inverted: x = slope * y + intercept
        // We need to calculate X values for Y values at the extended viewport edges
        // Note: In normalized coords, y increases upward, but in SVG y increases downward
        const y1_normalized = 1 + margin / svgHeight;  // Above the top (in normalized space)
        const y2_normalized = -margin / svgHeight;      // Below the bottom (in normalized space)
        const x1 = bottom.slope * y1_normalized + bottom.intercept;
        const x2 = bottom.slope * y2_normalized + bottom.intercept;
        lines.push({
          x1: x1 * svgWidth,
          y1: -margin,
          x2: x2 * svgWidth,
          y2: svgHeight + margin,
          color: "var(--color-yellow)",
          key: "bottom",
        });
      } else if (!bottom.isVertical) {
        const y1 = bottom.slope * -margin + bottom.intercept;
        const y2 = bottom.slope * (svgWidth + margin) + bottom.intercept;
        lines.push({
          x1: -margin,
          y1: (1 - y1) * svgHeight,
          x2: svgWidth + margin,
          y2: (1 - y2) * svgHeight,
          color: "var(--color-yellow)",
          key: "bottom",
        });
      }
    }

    // Left boundary (green)
    if (left) {
      if (left.isVertical && left.x !== undefined) {
        lines.push({
          x1: left.x * svgWidth,
          y1: -margin,
          x2: left.x * svgWidth,
          y2: svgHeight + margin,
          color: "var(--color-green)",
          key: "left",
        });
      } else if (!left.isVertical) {
        // Left boundary is a standard line: y = slope * x + intercept
        // Calculate Y values at the left and right edges of the extended viewport
        const x1 = -margin / svgWidth;
        const x2 = (svgWidth + margin) / svgWidth;
        const y1 = left.slope * x1 + left.intercept;
        const y2 = left.slope * x2 + left.intercept;
        lines.push({
          x1: -margin,
          y1: (1 - y1) * svgHeight,
          x2: svgWidth + margin,
          y2: (1 - y2) * svgHeight,
          color: "var(--color-green)",
          key: "left",
        });
      }
    }

    // Right boundary (green)
    if (right) {
      if (right.isVertical && right.x !== undefined) {
        lines.push({
          x1: right.x * svgWidth,
          y1: -margin,
          x2: right.x * svgWidth,
          y2: svgHeight + margin,
          color: "var(--color-green)",
          key: "right",
        });
      } else if (!right.isVertical) {
        // Right boundary is a standard line: y = slope * x + intercept
        // Calculate Y values at the left and right edges of the extended viewport
        const x1 = -margin / svgWidth;
        const x2 = (svgWidth + margin) / svgWidth;
        const y1 = right.slope * x1 + right.intercept;
        const y2 = right.slope * x2 + right.intercept;
        lines.push({
          x1: -margin,
          y1: (1 - y1) * svgHeight,
          x2: svgWidth + margin,
          y2: (1 - y2) * svgHeight,
          color: "var(--color-green)",
          key: "right",
        });
      }
    }

    return lines;
  }, [boundaries, svgWidth, svgHeight]);

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

  // Animation for boundary lines (show the intersecting lines first)
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
    keys: (corner: any) => `final-corner-${corner.index}`,
    from: { opacity: 0, scale: 0 },
    enter: (_item: any, index: number) => ({
      opacity: 1,
      scale: 1,
      delay: delay + 800 + index * 200, // After boundary lines
    }),
    config: { duration: 600 },
  });

  // Animation for the final polygon outline (appears after intersections)
  const polygonTransition = useTransition(
    polygonPoints ? [polygonPoints] : [],
    {
      keys: (points) => `final-polygon-${points}`,
      from: { opacity: 0 },
      enter: {
        opacity: 1,
        delay: delay + 1600, // After all intersections appear
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
      {/* Step 9a: Show boundary lines (yellow top/bottom, green left/right) */}
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

      {/* Step 9b: Show intersection points (where boundaries meet) */}
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

      {/* Step 9c: Final receipt quadrilateral (intersection result) */}
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
