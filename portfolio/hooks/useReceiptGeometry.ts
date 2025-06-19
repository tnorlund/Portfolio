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
  return useMemo(() => {
    const allLineCorners: Point[] = [];
    lines.forEach((line) => {
      allLineCorners.push(
        { x: line.top_left.x, y: line.top_left.y },
        { x: line.top_right.x, y: line.top_right.y },
        { x: line.bottom_right.x, y: line.bottom_right.y },
        { x: line.bottom_left.x, y: line.bottom_left.y }
      );
    });
    const convexHullPoints =
      allLineCorners.length > 2 ? convexHull([...allLineCorners]) : [];

    const hullCentroid =
      convexHullPoints.length > 0 ? computeHullCentroid(convexHullPoints) : null;

    const avgAngle =
      lines.length > 0
        ? lines.reduce((sum, l) => sum + l.angle_degrees, 0) / lines.length
        : 0;

    const finalAngle =
      hullCentroid && convexHullPoints.length > 0
        ? computeFinalReceiptTilt(lines, convexHullPoints, hullCentroid, avgAngle)
        : avgAngle;

    const hullExtremes =
      hullCentroid && convexHullPoints.length > 0
        ? findHullExtremesAlongAngle(convexHullPoints, hullCentroid, finalAngle)
        : null;

    const refinedSegments =
      hullExtremes && convexHullPoints.length > 0
        ? refineHullExtremesWithHullEdgeAlignment(
            convexHullPoints,
            hullExtremes.leftPoint,
            hullExtremes.rightPoint,
            finalAngle
          )
        : null;

    return {
      convexHullPoints,
      hullCentroid,
      finalAngle,
      hullExtremes,
      refinedSegments,
    };
  }, [lines]);
};

export default useReceiptGeometry;
