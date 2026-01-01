import React from "react";
import { useTransition, animated } from "@react-spring/web";
import type { Line, Point } from "../../../types/api";

interface AnimatedTopAndBottomProps {
  topLine: Line | null;
  bottomLine: Line | null;
  topLineCorners: Point[];
  bottomLineCorners: Point[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

/**
 * Animated visualization of top/bottom line selection.
 *
 * Shows:
 * 1. The top line highlighted (highest Y position) - GREEN
 * 2. The bottom line highlighted (lowest Y position) - YELLOW
 * 3. The corner points used for edge angle computation
 */
const AnimatedTopAndBottom: React.FC<AnimatedTopAndBottomProps> = ({
  topLine,
  bottomLine,
  topLineCorners,
  bottomLineCorners,
  svgWidth,
  svgHeight,
  delay,
}) => {
  // Convert line corners to SVG coordinates
  const toSvg = (p: Point) => ({
    x: p.x * svgWidth,
    y: (1 - p.y) * svgHeight,
  });

  // Compute polygon strings and key corners only when lines exist
  const { topPolygonStr, bottomPolygonStr, keyCorners, lineItems } =
    React.useMemo(() => {
      if (!topLine || !bottomLine) {
        return {
          topPolygonStr: "",
          bottomPolygonStr: "",
          keyCorners: [] as Point[],
          lineItems: [] as Array<{
            key: string;
            polygon: string;
            color: string;
          }>,
        };
      }

      // Top line polygon points
      const topLinePoints = [
        toSvg({ x: topLine.top_left.x, y: topLine.top_left.y }),
        toSvg({ x: topLine.top_right.x, y: topLine.top_right.y }),
        toSvg({ x: topLine.bottom_right.x, y: topLine.bottom_right.y }),
        toSvg({ x: topLine.bottom_left.x, y: topLine.bottom_left.y }),
      ];
      const topStr = topLinePoints.map((p) => `${p.x},${p.y}`).join(" ");

      // Bottom line polygon points
      const bottomLinePoints = [
        toSvg({ x: bottomLine.top_left.x, y: bottomLine.top_left.y }),
        toSvg({ x: bottomLine.top_right.x, y: bottomLine.top_right.y }),
        toSvg({ x: bottomLine.bottom_right.x, y: bottomLine.bottom_right.y }),
        toSvg({ x: bottomLine.bottom_left.x, y: bottomLine.bottom_left.y }),
      ];
      const bottomStr = bottomLinePoints.map((p) => `${p.x},${p.y}`).join(" ");

      // Key corner points for edge angle computation
      const corners = [
        // Top line: TL and TR (indices 0, 1)
        ...(topLineCorners.length >= 2
          ? [topLineCorners[0], topLineCorners[1]]
          : []),
        // Bottom line: BL and BR (indices 2, 3)
        ...(bottomLineCorners.length >= 4
          ? [bottomLineCorners[2], bottomLineCorners[3]]
          : []),
      ];

      return {
        topPolygonStr: topStr,
        bottomPolygonStr: bottomStr,
        keyCorners: corners,
        lineItems: [
          { key: "top", polygon: topStr, color: "var(--color-green)" },
          { key: "bottom", polygon: bottomStr, color: "var(--color-yellow)" },
        ],
      };
    }, [
      topLine,
      bottomLine,
      topLineCorners,
      bottomLineCorners,
      svgWidth,
      svgHeight,
    ]);

  const lineTransitions = useTransition(lineItems, {
    keys: (item) => item.key,
    from: { opacity: 0, strokeDasharray: "10,10", strokeDashoffset: 20 },
    enter: (_item, index) => ({
      opacity: 1,
      strokeDashoffset: 0,
      delay: delay + index * 400,
    }),
    config: { duration: 600 },
  });

  const cornerTransitions = useTransition(keyCorners, {
    keys: (point: Point) => `corner-${point.x}-${point.y}`,
    from: { opacity: 0, scale: 0 },
    enter: (_pt: Point, index: number) => ({
      opacity: 1,
      scale: 1,
      delay: delay + 800 + index * 150,
    }),
    config: { duration: 600 },
  });

  // Early return after all hooks have been called
  if (!topLine || !bottomLine) return null;

  return (
    <g>
      {/* Animated line highlights */}
      {lineTransitions((style, item) => (
        <animated.polygon
          key={item.key}
          style={style}
          points={item.polygon}
          fill={item.color}
          fillOpacity={0.3}
          stroke={item.color}
          strokeWidth="4"
        />
      ))}

      {/* Animated corner points */}
      {cornerTransitions((style, pt, _transition, idx) => {
        const svgPt = toSvg(pt);
        // First two are from top line (green), last two from bottom line (yellow)
        const color = idx < 2 ? "var(--color-green)" : "var(--color-yellow)";
        return (
          <animated.circle
            key={`corner-${pt.x}-${pt.y}`}
            style={style}
            cx={svgPt.x}
            cy={svgPt.y}
            r={8}
            fill={color}
            stroke="white"
            strokeWidth="2"
          />
        );
      })}
    </g>
  );
};

export default AnimatedTopAndBottom;
