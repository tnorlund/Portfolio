import { useMemo } from "react";
import type { Line, Point } from "../types/api";
import {
  convexHull,
  computeHullCentroid,
  computeRotatedBoundingBoxCorners,
  circularMeanAngle,
} from "../utils/geometry";

interface ReceiptGeometryResult {
  convexHullPoints: Point[];
  hullCentroid: Point | null;
  topLine: Line | null;
  bottomLine: Line | null;
  topLineCorners: Point[];
  bottomLineCorners: Point[];
  avgAngleRad: number;
  leftmostHullPoint: Point | null;
  rightmostHullPoint: Point | null;
  finalReceiptBox: Point[];
}

export const useReceiptGeometry = (lines: Line[]): ReceiptGeometryResult => {
  // Step 1 & 2 – Collect line corners and compute convex hull
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

  // Compute hull centroid
  const hullCentroid = useMemo(
    () =>
      convexHullPoints.length > 0
        ? computeHullCentroid(convexHullPoints)
        : null,
    [convexHullPoints]
  );

  // Step 3 – Find top and bottom lines by Y position
  const { topLine, bottomLine } = useMemo(() => {
    if (lines.length === 0) {
      return { topLine: null, bottomLine: null };
    }

    // Helper to compute line centroid Y (average of all 4 corners)
    // This is more robust than using a single corner for tilted lines
    const getLineCentroidY = (line: Line) =>
      (line.top_left.y + line.top_right.y + line.bottom_left.y + line.bottom_right.y) / 4;

    // Sort lines by centroid Y position (higher Y = top of receipt in normalized coords)
    const sortedLines = [...lines].sort(
      (a, b) => getLineCentroidY(b) - getLineCentroidY(a)
    );

    const top = sortedLines[0];
    const bottom = sortedLines[sortedLines.length - 1];

    // Handle upside-down receipts (avg angle > 90 degrees)
    const avgAngleDeg =
      lines.reduce((sum, l) => sum + l.angle_degrees, 0) / lines.length;

    if (Math.abs(avgAngleDeg) > 90) {
      return { topLine: bottom, bottomLine: top };
    }

    return { topLine: top, bottomLine: bottom };
  }, [lines]);

  // Step 4 – Get corners from top and bottom lines
  // Corner array ordering: [top-left, top-right, bottom-left, bottom-right]
  // This matches the Line entity structure and is used for edge angle calculations
  const topLineCorners = useMemo(() => {
    if (!topLine) return [];
    return [
      { x: topLine.top_left.x, y: topLine.top_left.y },
      { x: topLine.top_right.x, y: topLine.top_right.y },
      { x: topLine.bottom_left.x, y: topLine.bottom_left.y },
      { x: topLine.bottom_right.x, y: topLine.bottom_right.y },
    ];
  }, [topLine]);

  const bottomLineCorners = useMemo(() => {
    if (!bottomLine) return [];
    return [
      { x: bottomLine.top_left.x, y: bottomLine.top_left.y },
      { x: bottomLine.top_right.x, y: bottomLine.top_right.y },
      { x: bottomLine.bottom_left.x, y: bottomLine.bottom_left.y },
      { x: bottomLine.bottom_right.x, y: bottomLine.bottom_right.y },
    ];
  }, [bottomLine]);

  // Step 5 – Compute average edge angle using circular mean
  const avgAngleRad = useMemo(() => {
    if (topLineCorners.length < 4 || bottomLineCorners.length < 4) {
      return 0;
    }

    const topLeftPt = topLineCorners[0];
    const topRightPt = topLineCorners[1];
    const bottomLeftPt = bottomLineCorners[2];
    const bottomRightPt = bottomLineCorners[3];

    const topAngle = Math.atan2(
      topRightPt.y - topLeftPt.y,
      topRightPt.x - topLeftPt.x
    );
    const bottomAngle = Math.atan2(
      bottomRightPt.y - bottomLeftPt.y,
      bottomRightPt.x - bottomLeftPt.x
    );

    return circularMeanAngle(topAngle, bottomAngle);
  }, [topLineCorners, bottomLineCorners]);

  // Step 6 – Find left/right hull extremes along the average angle
  const { leftmostHullPoint, rightmostHullPoint } = useMemo(() => {
    if (convexHullPoints.length === 0 || !hullCentroid) {
      return { leftmostHullPoint: null, rightmostHullPoint: null };
    }

    const horizDir = { x: Math.cos(avgAngleRad), y: Math.sin(avgAngleRad) };

    const perpProjections: Array<{ proj: number; point: Point }> = [];
    for (const p of convexHullPoints) {
      const rel = { x: p.x - hullCentroid.x, y: p.y - hullCentroid.y };
      const proj = rel.x * horizDir.x + rel.y * horizDir.y;
      perpProjections.push({ proj, point: p });
    }

    perpProjections.sort((a, b) => a.proj - b.proj);

    return {
      leftmostHullPoint: perpProjections[0].point,
      rightmostHullPoint: perpProjections[perpProjections.length - 1].point,
    };
  }, [convexHullPoints, hullCentroid, avgAngleRad]);

  // Step 7 – Compute final receipt box using rotated bounding box approach
  const finalReceiptBox = useMemo(() => {
    if (
      convexHullPoints.length < 3 ||
      topLineCorners.length < 4 ||
      bottomLineCorners.length < 4
    ) {
      return [];
    }

    return computeRotatedBoundingBoxCorners(
      convexHullPoints,
      topLineCorners,
      bottomLineCorners
    );
  }, [convexHullPoints, topLineCorners, bottomLineCorners]);

  return useMemo(
    () => ({
      convexHullPoints,
      hullCentroid,
      topLine,
      bottomLine,
      topLineCorners,
      bottomLineCorners,
      avgAngleRad,
      leftmostHullPoint,
      rightmostHullPoint,
      finalReceiptBox,
    }),
    [
      convexHullPoints,
      hullCentroid,
      topLine,
      bottomLine,
      topLineCorners,
      bottomLineCorners,
      avgAngleRad,
      leftmostHullPoint,
      rightmostHullPoint,
      finalReceiptBox,
    ]
  );
};

export default useReceiptGeometry;
