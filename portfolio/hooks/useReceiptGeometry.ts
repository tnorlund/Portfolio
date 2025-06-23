import { useMemo } from "react";
import type { Line, Point } from "../types/api";
import { convexHull, computeHullCentroid } from "../utils/geometry";
import { computeFinalReceiptTilt } from "../utils/receipt";
import {
  findHullExtremesAlongAngle,
  refineHullExtremesWithHullEdgeAlignment,
  computeReceiptBoxFromBoundaries,
  createBoundaryLineFromPoints,
  createBoundaryLineFromTheilSen,
  type BoundaryLine,
} from "../utils/receipt/boundingBox";
import { findLineEdgesAtSecondaryExtremes, theilSen } from "../utils/geometry";

interface ReceiptGeometryResult {
  convexHullPoints: Point[];
  hullCentroid: Point | null;
  finalAngle: number;
  hullExtremes: { leftPoint: Point; rightPoint: Point } | null;
  refinedSegments: {
    leftSegment: { extreme: Point; optimizedNeighbor: Point };
    rightSegment: { extreme: Point; optimizedNeighbor: Point };
  } | null;
  boundaries: {
    top: BoundaryLine | null;
    bottom: BoundaryLine | null;
    left: BoundaryLine | null;
    right: BoundaryLine | null;
  };
  finalReceiptBox: Point[];
}

export const useReceiptGeometry = (lines: Line[]): ReceiptGeometryResult => {
  // Step 1 & 2 – Collect line corners and compute convex hull
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const convexHullPoints = useMemo(() => {
    const allLineCorners: Point[] = [];
    lines.forEach((line) => {
      allLineCorners.push(
        { x: line.top_left.x, y: line.top_left.y },
        { x: line.top_right.x, y: line.top_right.y },
        { x: line.bottom_right.x, y: line.bottom_right.y },
        { x: line.bottom_left.x, y: line.bottom_left.y }
      );
    });
    return allLineCorners.length > 2 ? convexHull([...allLineCorners]) : [];
  }, [lines]);

  // Step 3 – Compute hull centroid
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const hullCentroid = useMemo(
    () =>
      convexHullPoints.length > 0
        ? computeHullCentroid(convexHullPoints)
        : null,
    [convexHullPoints]
  );

  // Step 4 – Estimate initial skew from OCR lines
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const avgAngle = useMemo(
    () =>
      lines.length > 0
        ? lines.reduce((sum, l) => sum + l.angle_degrees, 0) / lines.length
        : 0,
    [lines]
  );

  // Steps 5 & 6 – Find top/bottom edges and compute final tilt
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const finalAngle = useMemo(
    () =>
      hullCentroid && convexHullPoints.length > 0
        ? computeFinalReceiptTilt(
            lines,
            convexHullPoints,
            hullCentroid,
            avgAngle
          )
        : avgAngle,
    [lines, convexHullPoints, hullCentroid, avgAngle]
  );

  // Step 7 – Find left/right extremes along receipt tilt
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const hullExtremes = useMemo(
    () =>
      hullCentroid && convexHullPoints.length > 0
        ? findHullExtremesAlongAngle(convexHullPoints, hullCentroid, finalAngle)
        : null,
    [convexHullPoints, hullCentroid, finalAngle]
  );

  // Step 8 – Refine extremes using Hull Edge Alignment
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const refinedSegments = useMemo(
    () =>
      hullExtremes && convexHullPoints.length > 0
        ? refineHullExtremesWithHullEdgeAlignment(
            convexHullPoints,
            hullExtremes.leftPoint,
            hullExtremes.rightPoint,
            finalAngle
          )
        : null,
    [convexHullPoints, hullExtremes, finalAngle]
  );

  // Step 5 Data – Compute top and bottom boundaries from preliminary angle
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const topBottomBoundaries = useMemo(() => {
    if (!hullCentroid || convexHullPoints.length === 0 || lines.length === 0) {
      return { top: null, bottom: null };
    }

    // Use avgAngle (preliminary) for Step 5, not finalAngle!
    // finalAngle is computed FROM these boundaries in Step 6
    const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
      lines,
      convexHullPoints,
      hullCentroid,
      avgAngle
    );

    if (topEdge.length < 2 || bottomEdge.length < 2) {
      return { top: null, bottom: null };
    }

    return {
      top: createBoundaryLineFromTheilSen(theilSen(topEdge)),
      bottom: createBoundaryLineFromTheilSen(theilSen(bottomEdge)),
    };
  }, [lines, convexHullPoints, hullCentroid, avgAngle]);

  // Step 8 Data – Compute left and right boundaries from refined segments
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const leftRightBoundaries = useMemo(() => {
    if (!refinedSegments) {
      return { left: null, right: null };
    }

    return {
      left: createBoundaryLineFromPoints(
        refinedSegments.leftSegment.extreme,
        refinedSegments.leftSegment.optimizedNeighbor
      ),
      right: createBoundaryLineFromPoints(
        refinedSegments.rightSegment.extreme,
        refinedSegments.rightSegment.optimizedNeighbor
      ),
    };
  }, [refinedSegments]);

  // Combine all boundaries
  const boundaries = useMemo(
    () => ({
      top: topBottomBoundaries.top,
      bottom: topBottomBoundaries.bottom,
      left: leftRightBoundaries.left,
      right: leftRightBoundaries.right,
    }),
    [topBottomBoundaries, leftRightBoundaries]
  );

  // Step 9 – Compute Final Receipt Quadrilateral from boundaries
  // See components/ui/Figures/PhotoReceiptBoundingBox.md
  const finalReceiptBox = useMemo(() => {
    const { top, bottom, left, right } = boundaries;

    if (!top || !bottom || !left || !right || !hullCentroid) {
      return [];
    }

    return computeReceiptBoxFromBoundaries(
      top,
      bottom,
      left,
      right,
      hullCentroid
    );
  }, [boundaries, hullCentroid]);

  return useMemo(
    () => ({
      convexHullPoints,
      hullCentroid,
      finalAngle,
      hullExtremes,
      refinedSegments,
      boundaries,
      finalReceiptBox,
    }),
    [
      convexHullPoints,
      hullCentroid,
      finalAngle,
      hullExtremes,
      refinedSegments,
      boundaries,
      finalReceiptBox,
    ]
  );
};

export default useReceiptGeometry;
