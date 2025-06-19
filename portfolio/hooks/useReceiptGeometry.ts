import { useMemo } from "react";
import type { Line, Point } from "../types/api";
import {
  convexHull,
  computeHullCentroid,
} from "../utils/geometry";
import { computeFinalReceiptTilt } from "../utils/receipt";
import {
  findHullExtremesAlongAngle,
  refineHullExtremesWithHullEdgeAlignment,
} from "../utils/receipt/boundingBox";

interface ReceiptGeometryResult {
  convexHullPoints: Point[];
  hullCentroid: Point | null;
  finalAngle: number;
  hullExtremes: { leftPoint: Point; rightPoint: Point } | null;
  refinedSegments:
    | {
        leftSegment: { extreme: Point; optimizedNeighbor: Point };
        rightSegment: { extreme: Point; optimizedNeighbor: Point };
      }
    | null;
}

export const useReceiptGeometry = (lines: Line[]): ReceiptGeometryResult => {
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

  const hullCentroid = useMemo(
    () =>
      convexHullPoints.length > 0 ? computeHullCentroid(convexHullPoints) : null,
    [convexHullPoints]
  );

  const avgAngle = useMemo(
    () =>
      lines.length > 0
        ? lines.reduce((sum, l) => sum + l.angle_degrees, 0) / lines.length
        : 0,
    [lines]
  );

  const finalAngle = useMemo(
    () =>
      hullCentroid && convexHullPoints.length > 0
        ? computeFinalReceiptTilt(lines, convexHullPoints, hullCentroid, avgAngle)
        : avgAngle,
    [lines, convexHullPoints, hullCentroid, avgAngle]
  );

  const hullExtremes = useMemo(
    () =>
      hullCentroid && convexHullPoints.length > 0
        ? findHullExtremesAlongAngle(convexHullPoints, hullCentroid, finalAngle)
        : null,
    [convexHullPoints, hullCentroid, finalAngle]
  );

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

  return useMemo(
    () => ({
      convexHullPoints,
      hullCentroid,
      finalAngle,
      hullExtremes,
      refinedSegments,
    }),
    [convexHullPoints, hullCentroid, finalAngle, hullExtremes, refinedSegments]
  );
};

export default useReceiptGeometry;
