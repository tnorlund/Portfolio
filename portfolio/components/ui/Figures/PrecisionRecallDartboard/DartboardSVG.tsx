import React, { useMemo, type ReactElement } from "react";
import type { DartboardSVGProps } from "./types";
import {
  DARTBOARD_GEOMETRY,
  DARTBOARD_NUMBERS,
  DARTBOARD_COLORS,
  SEGMENT_ANGLE,
  START_ANGLE_OFFSET,
  calculateSegmentPath,
  getSegmentColor,
  polarToCartesian,
} from "./dartboardGeometry";
import Dart from "./Dart";

/**
 * Renders a single dartboard SVG with optional darts.
 */
const DartboardSVG: React.FC<DartboardSVGProps> = ({
  size = 200,
  darts = [],
  animateDarts = true,
  dartAnimationDelay = 0,
  dartSpreadDuration = 400,
}) => {
  const centerX = size / 2;
  const centerY = size / 2;
  // Scale factor to fit dartboard with number labels
  const scale = size / 2.4;

  // Memoize the dartboard segments for performance
  const segments = useMemo(() => {
    const result: ReactElement[] = [];

    DARTBOARD_NUMBERS.forEach((number, index) => {
      const startAngle = START_ANGLE_OFFSET + index * SEGMENT_ANGLE;
      const endAngle = startAngle + SEGMENT_ANGLE;

      // Double ring (outermost scoring area)
      result.push(
        <path
          key={`double-${index}`}
          d={calculateSegmentPath(
            centerX,
            centerY,
            DARTBOARD_GEOMETRY.INNER_DOUBLE_RADIUS,
            DARTBOARD_GEOMETRY.OUTER_DOUBLE_RADIUS,
            startAngle,
            endAngle,
            scale
          )}
          fill={getSegmentColor(index, "double")}
          stroke={DARTBOARD_COLORS.dark}
          strokeWidth="0.5"
        />
      );

      // Outer single (between double and triple)
      result.push(
        <path
          key={`outer-single-${index}`}
          d={calculateSegmentPath(
            centerX,
            centerY,
            DARTBOARD_GEOMETRY.OUTER_TRIPLE_RADIUS,
            DARTBOARD_GEOMETRY.INNER_DOUBLE_RADIUS,
            startAngle,
            endAngle,
            scale
          )}
          fill={getSegmentColor(index, "single_outer")}
          stroke={DARTBOARD_COLORS.dark}
          strokeWidth="0.5"
        />
      );

      // Triple ring
      result.push(
        <path
          key={`triple-${index}`}
          d={calculateSegmentPath(
            centerX,
            centerY,
            DARTBOARD_GEOMETRY.INNER_TRIPLE_RADIUS,
            DARTBOARD_GEOMETRY.OUTER_TRIPLE_RADIUS,
            startAngle,
            endAngle,
            scale
          )}
          fill={getSegmentColor(index, "triple")}
          stroke={DARTBOARD_COLORS.dark}
          strokeWidth="0.5"
        />
      );

      // Inner single (between outer bull and triple)
      result.push(
        <path
          key={`inner-single-${index}`}
          d={calculateSegmentPath(
            centerX,
            centerY,
            DARTBOARD_GEOMETRY.OUTER_BULL_RADIUS,
            DARTBOARD_GEOMETRY.INNER_TRIPLE_RADIUS,
            startAngle,
            endAngle,
            scale
          )}
          fill={getSegmentColor(index, "single_inner")}
          stroke={DARTBOARD_COLORS.dark}
          strokeWidth="0.5"
        />
      );
    });

    return result;
  }, [size]);

  // Number labels positioned around the board
  const numberLabels = useMemo(() => {
    return DARTBOARD_NUMBERS.map((number, index) => {
      // Angle for center of each segment
      const angle =
        START_ANGLE_OFFSET + index * SEGMENT_ANGLE + SEGMENT_ANGLE / 2;
      const pos = polarToCartesian(
        angle,
        DARTBOARD_GEOMETRY.NUMBER_RADIUS,
        centerX,
        centerY,
        scale
      );

      return (
        <text
          key={`number-${number}`}
          x={pos.x}
          y={pos.y}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fontSize: size * 0.055,
            fontWeight: "bold",
            fill: DARTBOARD_COLORS.light,
            userSelect: "none",
          }}
        >
          {number}
        </text>
      );
    });
  }, [size]);

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      style={{ overflow: "visible" }}
      role="img"
      aria-label="Dartboard"
    >
      {/* Outer ring band (drawn first, dartboard goes on top) */}
      <circle
        cx={centerX}
        cy={centerY}
        r={scale * 1.27}
        fill={DARTBOARD_COLORS.dark}
      />

      {/* Background circle (covers center of outer ring) */}
      <circle
        cx={centerX}
        cy={centerY}
        r={scale * 1.0}
        fill={DARTBOARD_COLORS.light}
      />

      {/* Dartboard segments */}
      {segments}

      {/* Outer bull (single bull) */}
      <circle
        cx={centerX}
        cy={centerY}
        r={DARTBOARD_GEOMETRY.OUTER_BULL_RADIUS * scale}
        fill={DARTBOARD_COLORS.light}
        stroke={DARTBOARD_COLORS.dark}
        strokeWidth="0.5"
      />

      {/* Inner bull (double bull / bullseye) */}
      <circle
        cx={centerX}
        cy={centerY}
        r={DARTBOARD_GEOMETRY.INNER_BULL_RADIUS * scale}
        fill={DARTBOARD_COLORS.dark}
        stroke={DARTBOARD_COLORS.dark}
        strokeWidth="0.5"
      />

      {/* Number labels (on top of outer ring) */}
      {numberLabels}

      {/* Darts - only render when animateDarts is true */}
      {animateDarts && darts.map((dart, index) => {
        // Spread dart animations evenly across dartSpreadDuration
        const perDartDelay = darts.length > 1
          ? dartSpreadDuration / (darts.length - 1)
          : 0;
        return (
          <Dart
            key={`dart-${index}`}
            x={dart.x * size}
            y={dart.y * size}
            angle={dart.angle}
            animationDelay={dartAnimationDelay + index * perDartDelay}
            size={size * 0.055}
          />
        );
      })}
    </svg>
  );
};

export default DartboardSVG;
