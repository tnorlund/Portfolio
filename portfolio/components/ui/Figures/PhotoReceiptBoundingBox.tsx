import React, { useEffect, useState } from "react";

import {
  type Line,
  type Point as ApiPoint,
} from "../../../types/api";
import { useTransition, animated } from "@react-spring/web";
import AnimatedLineBox from "../animations/AnimatedLineBox";
import {
  AnimatedConvexHull,
  AnimatedHullCentroid,
  AnimatedOrientedAxes,
  AnimatedPrimaryEdges,
  AnimatedSecondaryBoundaryLines,
  AnimatedPrimaryBoundaryLines,
  AnimatedReceiptFromHull
} from "../animations";
import { getBestImageUrl } from "../../../utils/imageFormat";
import useImageDetails from "../../../hooks/useImageDetails";
import {
  computeHullCentroid,
  computeReceiptBoxFromLineEdges,
  computeEdge,
  convexHull,
} from "../../../utils/geometry";
import {
  findBoundaryLinesWithSkew,
  estimateReceiptPolygonFromLines,
} from "../../../utils/receiptGeometry";

// Define simple point and line-segment shapes
const isDevelopment = process.env.NODE_ENV === "development";
type Point = ApiPoint;
type LineSegment = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  key: string;
};


/**
 * Find hull extents relative to centroid (matching Python implementation)
 */
const findHullExtentsRelativeToCentroid = (
  hull: Point[],
  centroid: Point
): {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  leftPoint: Point;
  rightPoint: Point;
  topPoint: Point;
  bottomPoint: Point;
} => {
  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;
  let leftPoint = hull[0],
    rightPoint = hull[0];
  let topPoint = hull[0],
    bottomPoint = hull[0];

  hull.forEach((point) => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;

    if (relX < minX) {
      minX = relX;
      leftPoint = point;
    }
    if (relX > maxX) {
      maxX = relX;
      rightPoint = point;
    }
    if (relY < minY) {
      minY = relY;
      bottomPoint = point;
    }
    if (relY > maxY) {
      maxY = relY;
      topPoint = point;
    }
  });

  return {
    minX,
    maxX,
    minY,
    maxY,
    leftPoint,
    rightPoint,
    topPoint,
    bottomPoint,
  };
};

/**
 * Compute receipt box from hull using the same algorithm as Python
 */
const computeReceiptBoxFromHull = (
  hull: Point[],
  centroid: Point,
  avgAngle: number
): Point[] => {
  if (hull.length < 3) return [];

  // Find extents of hull relative to centroid
  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;

  // Rotate points by negative average angle to align with receipt orientation
  const angleRad = (-avgAngle * Math.PI) / 180;
  const cosA = Math.cos(angleRad);
  const sinA = Math.sin(angleRad);

  hull.forEach((point) => {
    // Translate to centroid origin
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;

    // Rotate
    const rotX = relX * cosA - relY * sinA;
    const rotY = relX * sinA + relY * cosA;

    minX = Math.min(minX, rotX);
    maxX = Math.max(maxX, rotX);
    minY = Math.min(minY, rotY);
    maxY = Math.max(maxY, rotY);
  });

  // Create bounding box corners in rotated space
  const corners = [
    { x: minX, y: maxY }, // top-left
    { x: maxX, y: maxY }, // top-right
    { x: maxX, y: minY }, // bottom-right
    { x: minX, y: minY }, // bottom-left
  ];

  // Rotate back and translate to world coordinates
  const reverseAngleRad = -angleRad;
  const cosRA = Math.cos(reverseAngleRad);
  const sinRA = Math.sin(reverseAngleRad);

  return corners.map((corner) => ({
    x: corner.x * cosRA - corner.y * sinRA + centroid.x,
    y: corner.x * sinRA + corner.y * cosRA + centroid.y,
  }));
};

/**
 * Find the left and right boundary lines using perpendicular projection
 */
const findLineEdgesAtPrimaryExtremes = (
  lines: Line[],
  hull: Point[],
  centroid: Point,
  avgAngle: number
): {
  leftEdge: Point[];
  rightEdge: Point[];
} => {
  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;

  const secondaryAxisAngle = primaryAxisAngle + Math.PI / 2; // Perpendicular to text direction

  // Step 1: Find boundary lines using perpendicular projection
  // Project each line's center onto the secondary axis (perpendicular to text direction)
  const lineProjections = lines.map((line) => {
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
      centerX: lineCenterX,
      centerY: lineCenterY,
    };
  });

  // Sort by projection to find extremes
  lineProjections.sort((a, b) => a.projection - b.projection);

  // Get boundary lines (use top 20% for each boundary to handle noise)
  const boundaryCount = Math.max(1, Math.ceil(lines.length * 0.2));
  const leftBoundaryLines = lineProjections
    .slice(0, boundaryCount)
    .map((p) => p.line);
  const rightBoundaryLines = lineProjections
    .slice(-boundaryCount)
    .map((p) => p.line);

  // Step 2: Get edge points from boundary lines
  let leftEdgePoints: Point[] = [];
  let rightEdgePoints: Point[] = [];

  // For left boundary lines, get their leftmost edges
  leftBoundaryLines.forEach((line) => {
    const leftX = Math.min(line.top_left.x, line.bottom_left.x);
    leftEdgePoints.push(
      { x: leftX, y: line.top_left.y },
      { x: leftX, y: line.bottom_left.y }
    );
  });

  // For right boundary lines, get their rightmost edges
  rightBoundaryLines.forEach((line) => {
    const rightX = Math.max(line.top_right.x, line.bottom_right.x);
    rightEdgePoints.push(
      { x: rightX, y: line.top_right.y },
      { x: rightX, y: line.bottom_right.y }
    );
  });

  return {
    leftEdge: leftEdgePoints,
    rightEdge: rightEdgePoints,
  };
};

/**
 * Find the top and bottom edges of lines at the secondary axis extremes
 */


/**
 * Display a random photo image with animated overlays that illustrate
 * how the receipt bounding box is derived from OCR line data.
 */
const PhotoReceiptBoundingBox: React.FC = () => {
  const { imageDetails, formatSupport, error } = useImageDetails("PHOTO");
  const [isClient, setIsClient] = useState(false);
  const [resetKey, setResetKey] = useState(0);

  // Ensure client-side hydration consistency
  useEffect(() => {
    setIsClient(true);
  }, []);


  // Reserve default dimensions while waiting for the API.
  const defaultSvgWidth = 400;
  const defaultSvgHeight = 565.806;

  // Extract lines and receipts
  const lines = imageDetails?.lines ?? [];
  const computedReceipt = estimateReceiptPolygonFromLines(lines);
  const receipts = computedReceipt
    ? [computedReceipt]
    : imageDetails?.receipts ?? [];

  // Compute convex hull from all line corners (matching the backend process_photo function)
  const allLineCorners: Point[] = [];
  lines.forEach((line) => {
    // Add all four corners of each line bounding box
    allLineCorners.push(
      { x: line.top_left.x, y: line.top_left.y },
      { x: line.top_right.x, y: line.top_right.y },
      { x: line.bottom_right.x, y: line.bottom_right.y },
      { x: line.bottom_left.x, y: line.bottom_left.y }
    );
  });
  const convexHullPoints =
    allLineCorners.length > 2 ? convexHull([...allLineCorners]) : [];

  // Compute hull centroid for animation
  const hullCentroid =
    convexHullPoints.length > 0 ? computeHullCentroid(convexHullPoints) : null;

  // Animate line bounding boxes using a transition.
  const lineTransitions = useTransition(lines, {
    // Include resetKey in the key so that each item gets a new key on reset.
    keys: (line) => `${resetKey}-${line.line_id}`,
    from: { opacity: 0, transform: "scale(0.8)" },
    enter: (item, index) => ({
      opacity: 1,
      transform: "scale(1)",
      delay: index * 30,
    }),
    config: { duration: 800 },
  });

  // Compute animation timing
  const totalDelayForLines =
    lines.length > 0 ? (lines.length - 1) * 30 + 800 : 0;
  const convexHullDelay = totalDelayForLines + 300; // Start convex hull after lines
  const convexHullDuration = convexHullPoints.length * 200 + 500;
  const centroidDelay = convexHullDelay + convexHullDuration + 200; // Hull centroid after convex hull
  const extentsDelay = centroidDelay + 600; // Extents after centroid
  const extentsDuration = 4 * 300 + 500; // 4 extent lines * 300ms + buffer
  const receiptDelay = extentsDelay + extentsDuration + 300; // Receipt after extents

  // Use the first image from the API.
  const firstImage = imageDetails?.image;

  // Get the optimal image URL based on browser support and available formats
  // Use fallback URL during SSR/initial render to prevent hydration mismatch
  const cdnUrl =
    firstImage && formatSupport && isClient
      ? getBestImageUrl(firstImage, formatSupport)
      : firstImage
      ? `${
          isDevelopment
            ? "https://dev.tylernorlund.com"
            : "https://www.tylernorlund.com"
        }/${firstImage.cdn_s3_key}`
      : "";

  // When imageDetails is loaded, compute these values;
  // otherwise, fall back on default dimensions.
  const svgWidth = firstImage ? firstImage.width : defaultSvgWidth;
  const svgHeight = firstImage ? firstImage.height : defaultSvgHeight;

  // Scale the displayed SVG (using the API data if available).
  const maxDisplayWidth = 400;
  const scaleFactor = Math.min(1, maxDisplayWidth / svgWidth);
  const displayWidth = svgWidth * scaleFactor;
  const displayHeight = svgHeight * scaleFactor;

  if (error) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          minHeight: displayHeight,
          alignItems: "center",
        }}
      >
        Error loading image details
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          minHeight: displayHeight,
          alignItems: "center",
        }}
      >
        <div
          style={{
            height: displayHeight,
            width: displayWidth,
            borderRadius: "15px",
            overflow: "hidden",
          }}
        >
          {imageDetails && formatSupport ? (
            <svg
              key={resetKey}
              onClick={() => setResetKey((k) => k + 1)}
              viewBox={`0 0 ${svgWidth} ${svgHeight}`}
              width={displayWidth}
              height={displayHeight}
            >
              <image
                href={cdnUrl}
                x="0"
                y="0"
                width={svgWidth}
                height={svgHeight}
              />

              {/* Render animated word bounding boxes (via transition) */}
              {lineTransitions((style, line) => {
                const x1 = line.top_left.x * svgWidth;
                const y1 = (1 - line.top_left.y) * svgHeight;
                const x2 = line.top_right.x * svgWidth;
                const y2 = (1 - line.top_right.y) * svgHeight;
                const x3 = line.bottom_right.x * svgWidth;
                const y3 = (1 - line.bottom_right.y) * svgHeight;
                const x4 = line.bottom_left.x * svgWidth;
                const y4 = (1 - line.bottom_left.y) * svgHeight;
                const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;
                return (
                  <animated.polygon
                    key={`${line.line_id}`}
                    style={style}
                    points={points}
                    fill="none"
                    stroke="var(--color-red)"
                    strokeWidth="2"
                  />
                );
              })}

              {/* Render animated convex hull */}
              {convexHullPoints.length > 0 && (
                <AnimatedConvexHull
                  key={`convex-hull-${resetKey}`}
                  hullPoints={convexHullPoints}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={convexHullDelay}
                />
              )}

              {/* Render animated hull centroid */}
              {hullCentroid && (
                <AnimatedHullCentroid
                  key={`hull-centroid-${resetKey}`}
                  centroid={hullCentroid}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={centroidDelay}
                />
              )}

              {/* Render animated oriented axes */}
              {convexHullPoints.length > 0 && hullCentroid && (
                <AnimatedOrientedAxes
                  key={`oriented-axes-${resetKey}`}
                  hull={convexHullPoints}
                  centroid={hullCentroid}
                  lines={lines}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay}
                />
              )}

              {/* Render line edges at primary extremes */}
              {convexHullPoints.length > 0 && hullCentroid && lines.length > 0 && (
                <AnimatedPrimaryEdges
                  key={`primary-edges-${resetKey}`}
                  lines={lines}
                  hull={convexHullPoints}
                  centroid={hullCentroid}
                  avgAngle={
                    lines.reduce((sum, line) => sum + line.angle_degrees, 0) /
                    lines.length
                  }
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay + 1000}
                />
              )}

              {/* Render extended yellow boundary lines */}
              {convexHullPoints.length > 0 && hullCentroid && lines.length > 0 && (
                <AnimatedSecondaryBoundaryLines
                  key={`secondary-boundary-lines-${resetKey}`}
                  lines={lines}
                  hull={convexHullPoints}
                  centroid={hullCentroid}
                  avgAngle={
                    lines.reduce((sum, line) => sum + line.angle_degrees, 0) /
                    lines.length
                  }
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay + 1500}
                />
              )}

              {/* Render green left/right boundary lines using perpendicular projection */}
              {convexHullPoints.length > 0 && hullCentroid && lines.length > 0 && (
                <AnimatedPrimaryBoundaryLines
                  key={`primary-boundary-lines-${resetKey}`}
                  hull={convexHullPoints}
                  centroid={hullCentroid}
                  avgAngle={
                    lines.reduce((sum, line) => sum + line.angle_degrees, 0) /
                    lines.length
                  }
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay + 2000}
                />
              )}

              {/* Render animated receipt using proper algorithm */}
              {/* {convexHullPoints.length > 0 && lines.length > 0 && (
                <AnimatedReceiptFromHull
                  key={`receipt-from-hull-${resetKey}`}
                  hull={convexHullPoints}
                  lines={lines}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={receiptDelay}
                />
              )} */}
            </svg>
          ) : (
            // While loading, show a "Loading" message centered in the reserved space.
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                width: "100%",
                height: "100%",
              }}
            >
              Loading...
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PhotoReceiptBoundingBox;
