import type { Line } from "../types/api";
import { computeEdge, Point } from "./geometry";

export const findBoundaryLinesWithSkew = (
  lines: Line[],
  _hull: Point[],
  centroid: Point,
  avgAngle: number
): {
  leftEdgePoints: Point[];
  rightEdgePoints: Point[];
  leftBoundaryAngle: number;
  rightBoundaryAngle: number;
} => {
  if (lines.length === 0) {
    return {
      leftEdgePoints: [],
      rightEdgePoints: [],
      leftBoundaryAngle: avgAngle,
      rightBoundaryAngle: avgAngle,
    };
  }

  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;
  const secondaryAxisAngle = primaryAxisAngle + Math.PI / 2;

  const lineProjections = lines.map(line => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    const lineCenterY =
      (line.top_left.y +
        line.top_right.y +
        line.bottom_left.y +
        line.bottom_right.y) /
      4;

    const relX = lineCenterX - centroid.x;
    const relY = lineCenterY - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    return {
      line,
      projection: secondaryProjection,
    };
  });

  lineProjections.sort((a, b) => a.projection - b.projection);

  const boundaryCount = Math.max(1, Math.ceil(lines.length * 0.2));
  const leftBoundaryLines = lineProjections
    .slice(0, boundaryCount)
    .map(p => p.line);
  const rightBoundaryLines = lineProjections
    .slice(-boundaryCount)
    .map(p => p.line);

  const leftBoundaryAngle =
    leftBoundaryLines.reduce((sum, line) => sum + line.angle_degrees, 0) /
    leftBoundaryLines.length;
  const rightBoundaryAngle =
    rightBoundaryLines.reduce((sum, line) => sum + line.angle_degrees, 0) /
    rightBoundaryLines.length;

  const leftEdgePoints: Point[] = [];
  const rightEdgePoints: Point[] = [];
  leftBoundaryLines.forEach(line => {
    const leftX = Math.min(line.top_left.x, line.bottom_left.x);
    leftEdgePoints.push(
      { x: leftX, y: line.top_left.y },
      { x: leftX, y: line.bottom_left.y }
    );
  });

  rightBoundaryLines.forEach(line => {
    const rightX = Math.max(line.top_right.x, line.bottom_right.x);
    rightEdgePoints.push(
      { x: rightX, y: line.top_right.y },
      { x: rightX, y: line.bottom_right.y }
    );
  });

  return {
    leftEdgePoints,
    rightEdgePoints,
    leftBoundaryAngle,
    rightBoundaryAngle,
  };
};

export const estimateReceiptPolygonFromLines = (lines: Line[]) => {
  const left = computeEdge(lines, "left");
  const right = computeEdge(lines, "right");
  if (!left || !right) return null;

  return {
    receipt_id: "computed",
    top_left: left.top,
    top_right: right.top,
    bottom_right: right.bottom,
    bottom_left: left.bottom,
  };
};
