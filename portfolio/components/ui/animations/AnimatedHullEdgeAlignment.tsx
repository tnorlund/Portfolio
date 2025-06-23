import React from "react";
import { useTransition, animated, useSpring } from "@react-spring/web";
import type { Point } from "../../../types/api";

interface AnimatedHullEdgeAlignmentProps {
  hull: Point[];
  refinedSegments: {
    leftSegment: { extreme: Point; optimizedNeighbor: Point };
    rightSegment: { extreme: Point; optimizedNeighbor: Point };
  };
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedHullEdgeAlignment: React.FC<AnimatedHullEdgeAlignmentProps> = ({
  hull,
  refinedSegments,
  svgWidth,
  svgHeight,
  delay,
}) => {
  // Find hull indices for extreme points and their neighbors
  const findHullIndex = (point: Point): number => {
    return hull.findIndex(
      (p) => Math.abs(p.x - point.x) < 1e-10 && Math.abs(p.y - point.y) < 1e-10
    );
  };

  const leftExtremeIndex = findHullIndex(refinedSegments.leftSegment.extreme);
  const rightExtremeIndex = findHullIndex(refinedSegments.rightSegment.extreme);
  const leftChosenIndex = findHullIndex(
    refinedSegments.leftSegment.optimizedNeighbor
  );
  const rightChosenIndex = findHullIndex(
    refinedSegments.rightSegment.optimizedNeighbor
  );

  // Calculate CW/CCW neighbors for each extreme
  const leftCWIndex = (leftExtremeIndex + 1) % hull.length;
  const leftCCWIndex = (leftExtremeIndex - 1 + hull.length) % hull.length;
  const rightCWIndex = (rightExtremeIndex + 1) % hull.length;
  const rightCCWIndex = (rightExtremeIndex - 1 + hull.length) % hull.length;

  // Determine which neighbor was chosen
  const leftChoseCW = leftChosenIndex === leftCWIndex;
  const rightChoseCW = rightChosenIndex === rightCWIndex;

  // Animation timing - simplified to show extremes then immediate decision
  const extremesDelay = delay;
  const decisionDelay = delay + 800; // Show decision and boundaries together

  // Convert points to screen coordinates
  const toScreen = (point: Point) => ({
    x: point.x * svgWidth,
    y: (1 - point.y) * svgHeight,
  });

  // Create boundary line segments
  const createBoundaryLine = (extreme: Point, neighbor: Point) => {
    const extremeScreen = toScreen(extreme);
    const neighborScreen = toScreen(neighbor);

    const dx = neighborScreen.x - extremeScreen.x;
    const dy = neighborScreen.y - extremeScreen.y;

    // Handle nearly horizontal lines
    if (Math.abs(dy) < 1e-6) {
      const avgY = (extremeScreen.y + neighborScreen.y) / 2;
      return { x1: 0, y1: avgY, x2: svgWidth, y2: avgY };
    }

    // Handle nearly vertical lines
    if (Math.abs(dx) < 1e-6) {
      const avgX = (extremeScreen.x + neighborScreen.x) / 2;
      return { x1: avgX, y1: 0, x2: avgX, y2: svgHeight };
    }

    // Calculate line equation: y = mx + b
    const slope = dy / dx;
    const intercept = extremeScreen.y - slope * extremeScreen.x;

    // Calculate intersection points with viewport boundaries
    const leftY = intercept; // x = 0
    const rightY = slope * svgWidth + intercept; // x = svgWidth
    const topX = (0 - intercept) / slope; // y = 0
    const bottomX = (svgHeight - intercept) / slope; // y = svgHeight

    // Find which viewport edges the line intersects
    const intersections = [];

    // Check left edge (x = 0)
    if (leftY >= 0 && leftY <= svgHeight) {
      intersections.push({ x: 0, y: leftY });
    }

    // Check right edge (x = svgWidth)
    if (rightY >= 0 && rightY <= svgHeight) {
      intersections.push({ x: svgWidth, y: rightY });
    }

    // Check top edge (y = 0)
    if (topX >= 0 && topX <= svgWidth) {
      intersections.push({ x: topX, y: 0 });
    }

    // Check bottom edge (y = svgHeight)
    if (bottomX >= 0 && bottomX <= svgWidth) {
      intersections.push({ x: bottomX, y: svgHeight });
    }

    // Use the first two valid intersections, or fallback to viewport edges
    if (intersections.length >= 2) {
      return {
        x1: intersections[0].x,
        y1: intersections[0].y,
        x2: intersections[1].x,
        y2: intersections[1].y,
      };
    }

    // Fallback: extend in direction from extreme to neighbor
    const length = Math.hypot(dx, dy);
    const unitX = dx / length;
    const unitY = dy / length;
    const extensionLength = Math.max(svgWidth, svgHeight);

    return {
      x1: extremeScreen.x - unitX * extensionLength,
      y1: extremeScreen.y - unitY * extensionLength,
      x2: extremeScreen.x + unitX * extensionLength,
      y2: extremeScreen.y + unitY * extensionLength,
    };
  };

  const leftBoundaryLine = createBoundaryLine(
    refinedSegments.leftSegment.extreme,
    refinedSegments.leftSegment.optimizedNeighbor
  );

  const rightBoundaryLine = createBoundaryLine(
    refinedSegments.rightSegment.extreme,
    refinedSegments.rightSegment.optimizedNeighbor
  );

  // Debug logging
  console.log("AnimatedHullEdgeAlignment Debug:", {
    leftBoundaryLine,
    rightBoundaryLine,
    svgWidth,
    svgHeight,
    leftExtreme: refinedSegments.leftSegment.extreme,
    rightExtreme: refinedSegments.rightSegment.extreme,
    leftNeighbor: refinedSegments.leftSegment.optimizedNeighbor,
    rightNeighbor: refinedSegments.rightSegment.optimizedNeighbor,
  });

  // Animation springs for different phases
  const extremePointsSpring = useSpring({
    opacity: 1,
    scale: 1,
    delay: extremesDelay,
    from: { opacity: 0, scale: 0.5 },
    config: { duration: 600 },
  });

  const decisionAndBoundarySpring = useSpring({
    opacity: 1,
    scale: 1,
    delay: decisionDelay,
    from: { opacity: 0, scale: 0.8 },
    config: { duration: 1000 },
  });

  return (
    <g>
      {/* Step 1: Highlight extreme points */}
      <animated.g style={extremePointsSpring}>
        {/* Left extreme */}
        <circle
          cx={toScreen(refinedSegments.leftSegment.extreme).x}
          cy={toScreen(refinedSegments.leftSegment.extreme).y}
          r={10}
          fill="var(--color-green)"
          strokeWidth="2"
        />

        {/* Right extreme */}
        <circle
          cx={toScreen(refinedSegments.rightSegment.extreme).x}
          cy={toScreen(refinedSegments.rightSegment.extreme).y}
          r={10}
          fill="var(--color-green)"
          strokeWidth="2"
        />
      </animated.g>

      {/* Step 2: Show chosen neighbors with decision indicators and boundary lines */}
      <animated.g style={decisionAndBoundarySpring}>
        {/* Left chosen neighbor */}
        <circle
          cx={toScreen(refinedSegments.leftSegment.optimizedNeighbor).x}
          cy={toScreen(refinedSegments.leftSegment.optimizedNeighbor).y}
          r={8}
          fill="var(--color-green)"
          strokeWidth="2"
        />

        {/* Right chosen neighbor */}
        <circle
          cx={toScreen(refinedSegments.rightSegment.optimizedNeighbor).x}
          cy={toScreen(refinedSegments.rightSegment.optimizedNeighbor).y}
          r={8}
          fill="var(--color-green)"
          strokeWidth="2"
        />

        {/* Draw final boundary lines */}
        <line
          x1={leftBoundaryLine.x1}
          y1={leftBoundaryLine.y1}
          x2={leftBoundaryLine.x2}
          y2={leftBoundaryLine.y2}
          stroke="var(--color-green)"
          strokeWidth="6"
          strokeDasharray="10,10"
        />
        <line
          x1={rightBoundaryLine.x1}
          y1={rightBoundaryLine.y1}
          x2={rightBoundaryLine.x2}
          y2={rightBoundaryLine.y2}
          stroke="var(--color-green)"
          strokeWidth="6"
          strokeDasharray="10,10"
        />
      </animated.g>
    </g>
  );
};

export default AnimatedHullEdgeAlignment;
