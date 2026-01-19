import React, { useEffect, useState } from "react";

import { animated, useTransition } from "@react-spring/web";
import useImageDetails from "../../../hooks/useImageDetails";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import useReceiptClustering from "../../../hooks/useReceiptClustering";
import useReceiptGeometry from "../../../hooks/useReceiptGeometry";
import { getBestImageUrl } from "../../../utils/imageFormat";
import { estimateReceiptPolygonFromLines } from "../../../utils/receipt";
import {
  AnimatedConvexHull,
  AnimatedFinalReceiptBox,
  AnimatedHullCentroid,
  AnimatedOrientedAxes,
  AnimatedTopAndBottom,
} from "../animations";
import { getAnimationConfig } from "./animationConfig";
import ReceiptBoundingBoxFrame from "./ReceiptBoundingBoxFrame";

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
  // Spread to avoid mutating the original clusters array from the hook
  const lines =
    clusters.length > 0
      ? [...clusters].sort((a, b) => b.lines.length - a.lines.length)[0].lines
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
    convexHullDelay,
    centroidDelay,
    extentsDelay,
    receiptDelay,
  } = getAnimationConfig(lines.length, convexHullPoints.length);

  // Use the first image from the API.
  const firstImage = imageDetails?.image;

  // Get the optimal image URL based on browser support and available formats
  const cdnUrl =
    firstImage && formatSupport && isClient
      ? getBestImageUrl(firstImage, formatSupport, "medium")
      : firstImage
        ? `${isDevelopment
          ? "https://dev.tylernorlund.com"
          : "https://www.tylernorlund.com"
        }/${firstImage.cdn_s3_key}`
        : "";

  // When imageDetails is loaded, compute these values
  const svgWidth = firstImage ? firstImage.width : defaultSvgWidth;
  const svgHeight = firstImage ? firstImage.height : defaultSvgHeight;

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
          minHeight: 0,
          alignItems: "center",
        }}
      >
        Error loading image details
      </div>
    );
  }

  return (
    <div ref={ref} style={{ width: "100%" }}>
      <ReceiptBoundingBoxFrame
        lines={lines}
        receipts={receipts}
        imageWidth={svgWidth}
        imageHeight={svgHeight}
      >
        {(cropInfo, fullImageWidth, fullImageHeight) => {
          // Use crop viewBox if available, otherwise use full image
          const viewBox = cropInfo
            ? `${cropInfo.x} ${cropInfo.y} ${cropInfo.width} ${cropInfo.height}`
            : `0 0 ${fullImageWidth} ${fullImageHeight}`;

          // Use crop dimensions for animation components if cropped
          const effectiveSvgWidth = cropInfo ? cropInfo.width : fullImageWidth;
          const effectiveSvgHeight = cropInfo ? cropInfo.height : fullImageHeight;

          // Transform normalized coordinates to SVG pixel coordinates
          // The viewBox handles the crop offset, so we just convert to full image coordinates
          const transformX = (normX: number) => normX * fullImageWidth;
          const transformY = (normY: number) => (1 - normY) * fullImageHeight;

          return imageDetails && formatSupport ? (
            <svg
              key={resetKey}
              onClick={() => setResetKey((k) => k + 1)}
              viewBox={viewBox}
              style={{ 
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%", 
                height: "100%", 
                display: "block",
              }}
              preserveAspectRatio="xMidYMid slice"
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
              {/* Image positioned to account for crop */}
              {/* Full image - viewBox handles the cropping */}
              <image
                href={cdnUrl}
                x={0}
                y={0}
                width={fullImageWidth}
                height={fullImageHeight}
              />

              {/* Step 1: Render animated word bounding boxes */}
              {lineTransitions((style, line) => {
                const x1 = transformX(line.top_left.x);
                const y1 = transformY(line.top_left.y);
                const x2 = transformX(line.top_right.x);
                const y2 = transformY(line.top_right.y);
                const x3 = transformX(line.bottom_right.x);
                const y3 = transformY(line.bottom_right.y);
                const x4 = transformX(line.bottom_left.x);
                const y4 = transformY(line.bottom_left.y);
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
                  const x1 = transformX(line.top_left.x);
                  const y1 = transformY(line.top_left.y);
                  const x2 = transformX(line.top_right.x);
                  const y2 = transformY(line.top_right.y);
                  const x3 = transformX(line.bottom_right.x);
                  const y3 = transformY(line.bottom_right.y);
                  const x4 = transformX(line.bottom_left.x);
                  const y4 = transformY(line.bottom_left.y);
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
                  svgWidth={effectiveSvgWidth}
                  svgHeight={effectiveSvgHeight}
                  delay={convexHullDelay}
                  showIndices
                  cropInfo={cropInfo}
                  fullImageWidth={fullImageWidth}
                  fullImageHeight={fullImageHeight}
                />
              )}

              {/* Step 3: Compute Hull Centroid */}
              {inView && hullCentroid && (
                <AnimatedHullCentroid
                  key={`hull-centroid-${resetKey}`}
                  centroid={hullCentroid}
                  svgWidth={effectiveSvgWidth}
                  svgHeight={effectiveSvgHeight}
                  delay={centroidDelay}
                  cropInfo={cropInfo}
                  fullImageWidth={fullImageWidth}
                  fullImageHeight={fullImageHeight}
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
                  svgWidth={effectiveSvgWidth}
                  svgHeight={effectiveSvgHeight}
                  delay={extentsDelay}
                  cropInfo={cropInfo}
                  fullImageWidth={fullImageWidth}
                  fullImageHeight={fullImageHeight}
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
                  svgWidth={effectiveSvgWidth}
                  svgHeight={effectiveSvgHeight}
                  delay={extentsDelay + 1500}
                  cropInfo={cropInfo}
                  fullImageWidth={fullImageWidth}
                  fullImageHeight={fullImageHeight}
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
                  svgWidth={effectiveSvgWidth}
                  svgHeight={effectiveSvgHeight}
                  delay={receiptDelay}
                  cropInfo={cropInfo}
                  fullImageWidth={fullImageWidth}
                  fullImageHeight={fullImageHeight}
                />
              )}
            </svg>
          ) : (
            // Loading placeholder - fills the frame completely
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
              }}
            >
              <span style={{ color: "var(--text-color)", fontSize: "0.9rem", opacity: 0.5 }}>
                Loading...
              </span>
            </div>
          );
        }}
      </ReceiptBoundingBoxFrame>
    </div>
  );
};

export default PhotoReceiptBoundingBox;
