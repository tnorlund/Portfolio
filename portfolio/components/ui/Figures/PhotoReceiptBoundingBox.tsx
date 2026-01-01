import React, { useEffect, useState } from "react";

import { useTransition, animated } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import {
  AnimatedConvexHull,
  AnimatedHullCentroid,
  AnimatedOrientedAxes,
  AnimatedTopAndBottom,
  AnimatedFinalReceiptBox,
} from "../animations";
import { getBestImageUrl } from "../../../utils/imageFormat";
import useImageDetails from "../../../hooks/useImageDetails";
import { estimateReceiptPolygonFromLines } from "../../../utils/receipt";
import useReceiptGeometry from "../../../hooks/useReceiptGeometry";
import useReceiptClustering from "../../../hooks/useReceiptClustering";
import { getAnimationConfig } from "./animationConfig";

const isDevelopment = process.env.NODE_ENV === "development";

/**
 * Display a random photo image with animated overlays that illustrate
 * how the receipt bounding box is derived from OCR line data.
 *
 * Uses the simplified rotated bounding box algorithm:
 * 1. Compute convex hull of all line corners
 * 2. Find top/bottom lines by Y position
 * 3. Compute average edge angle from top/bottom line corners
 * 4. Project hull onto avg_angle to find left/right extremes
 * 5. Intersect boundary lines to get final corners
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
  const allLines = imageDetails?.lines ?? [];

  // Use DBSCAN clustering to filter out noise lines
  const { clusters, noiseLines } = useReceiptClustering(allLines, {
    minPoints: 10,
    imageWidth: imageDetails?.image?.width,
    imageHeight: imageDetails?.image?.height,
  });

  // Use only the lines from the largest cluster (main receipt)
  const lines =
    clusters.length > 0
      ? clusters.sort((a, b) => b.lines.length - a.lines.length)[0].lines
      : [];

  const computedReceipt = estimateReceiptPolygonFromLines(lines);
  const receipts = computedReceipt
    ? [computedReceipt]
    : imageDetails?.receipts ?? [];

  // Use the simplified geometry hook
  const {
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
  } = useReceiptGeometry(lines);

  // Step 1 – Display OCR line boxes
  const lineTransitions = useTransition(inView ? lines : [], {
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
  const {
    totalDelayForLines,
    convexHullDelay,
    convexHullDuration,
    centroidDelay,
    extentsDelay,
    extentsDuration,
    hullEdgeAlignmentDuration,
    receiptDelay,
  } = getAnimationConfig(lines.length, convexHullPoints.length);

  // Use the first image from the API.
  const firstImage = imageDetails?.image;

  // Get the optimal image URL based on browser support and available formats
  const cdnUrl =
    firstImage && formatSupport && isClient
      ? getBestImageUrl(firstImage, formatSupport, "medium")
      : firstImage
      ? `${
          isDevelopment
            ? "https://dev.tylernorlund.com"
            : "https://www.tylernorlund.com"
        }/${firstImage.cdn_s3_key}`
      : "";

  // When imageDetails is loaded, compute these values
  const svgWidth = firstImage ? firstImage.width : defaultSvgWidth;
  const svgHeight = firstImage ? firstImage.height : defaultSvgHeight;

  // Scale the displayed SVG
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
              <defs>
                <style>
                  {`
                    @keyframes fadeIn {
                      from { opacity: 0; transform: scale(0.5); }
                      to { opacity: 1; transform: scale(1); }
                    }
                  `}
                </style>
              </defs>
              <image
                href={cdnUrl}
                x="0"
                y="0"
                width={svgWidth}
                height={svgHeight}
              />

              {/* Step 1: Render animated word bounding boxes */}
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

              {/* Show noise lines in a subtle way */}
              {inView &&
                noiseLines.map((line) => {
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
                    <polygon
                      key={`noise-${line.line_id}`}
                      points={points}
                      fill="none"
                      stroke="gray"
                      strokeWidth="1"
                      opacity="0.3"
                      strokeDasharray="2,2"
                    />
                  );
                })}

              {/* Step 2: Compute the Convex Hull */}
              {inView && convexHullPoints.length > 0 && (
                <AnimatedConvexHull
                  key={`convex-hull-${resetKey}`}
                  hullPoints={convexHullPoints}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={convexHullDelay}
                  showIndices
                />
              )}

              {/* Step 3: Compute Hull Centroid */}
              {inView && hullCentroid && (
                <AnimatedHullCentroid
                  key={`hull-centroid-${resetKey}`}
                  centroid={hullCentroid}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={centroidDelay}
                />
              )}

              {/* Step 4: Show Top/Bottom Line Selection */}
              {inView && topLine && bottomLine && (
                <AnimatedTopAndBottom
                  key={`top-and-bottom-${resetKey}`}
                  topLine={topLine}
                  bottomLine={bottomLine}
                  topLineCorners={topLineCorners}
                  bottomLineCorners={bottomLineCorners}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay}
                />
              )}

              {/* Step 5: Show Average Angle and Hull Extremes */}
              {inView && convexHullPoints.length > 0 && hullCentroid && (
                <AnimatedOrientedAxes
                  key={`oriented-axes-${resetKey}`}
                  hull={convexHullPoints}
                  centroid={hullCentroid}
                  avgAngleRad={avgAngleRad}
                  leftmostHullPoint={leftmostHullPoint}
                  rightmostHullPoint={rightmostHullPoint}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay + 1500}
                />
              )}

              {/* Step 6: Compute Final Receipt Quadrilateral */}
              {inView && finalReceiptBox.length === 4 && (
                <AnimatedFinalReceiptBox
                  key={`final-receipt-box-${resetKey}`}
                  finalReceiptBox={finalReceiptBox}
                  topLineCorners={topLineCorners}
                  bottomLineCorners={bottomLineCorners}
                  leftmostHullPoint={leftmostHullPoint}
                  rightmostHullPoint={rightmostHullPoint}
                  avgAngleRad={avgAngleRad}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={receiptDelay}
                />
              )}
            </svg>
          ) : (
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
