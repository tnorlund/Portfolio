import React, { useEffect, useState } from "react";

import { type Line, type Point as ApiPoint } from "../../../types/api";
import { useTransition, animated } from "@react-spring/web";
import AnimatedLineBox from "../animations/AnimatedLineBox";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import {
  AnimatedConvexHull,
  AnimatedHullCentroid,
  AnimatedOrientedAxes,
  AnimatedPrimaryEdges,
  AnimatedSecondaryBoundaryLines,
  AnimatedPrimaryBoundaryLines,
  AnimatedReceiptFromHull,
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
import {
  findHullExtentsRelativeToCentroid,
  computeReceiptBoxFromHull,
  findLineEdgesAtPrimaryExtremes,
} from "../../../utils/receiptBoundingBox";

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
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });

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
  const lineTransitions = useTransition(inView ? lines : [], {
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

  useEffect(() => {
    if (!inView) {
      setResetKey((k) => k + 1);
    }
  }, [inView]);

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
    <div ref={ref}>
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
              {inView && convexHullPoints.length > 0 && (
                <AnimatedConvexHull
                  key={`convex-hull-${resetKey}`}
                  hullPoints={convexHullPoints}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={convexHullDelay}
                />
              )}

              {/* Render animated hull centroid */}
              {inView && hullCentroid && (
                <AnimatedHullCentroid
                  key={`hull-centroid-${resetKey}`}
                  centroid={hullCentroid}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={centroidDelay}
                />
              )}

              {/* Render animated oriented axes */}
              {inView && convexHullPoints.length > 0 && hullCentroid && (
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
              {inView &&
                convexHullPoints.length > 0 &&
                hullCentroid &&
                lines.length > 0 && (
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
              {inView &&
                convexHullPoints.length > 0 &&
                hullCentroid &&
                lines.length > 0 && (
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
              {inView &&
                convexHullPoints.length > 0 &&
                hullCentroid &&
                lines.length > 0 && (
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
