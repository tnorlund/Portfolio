import React, { useEffect, useState } from "react";

import { type Point as ApiPoint } from "../../../types/api";
import { useTransition, animated } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import {
  AnimatedConvexHull,
  AnimatedHullCentroid,
  AnimatedOrientedAxes,
  AnimatedTopAndBottom,
  AnimatedHullEdgeAlignment,
  AnimatedFinalReceiptBox,
} from "../animations";
import { getBestImageUrl } from "../../../utils/imageFormat";
import useImageDetails from "../../../hooks/useImageDetails";
import { estimateReceiptPolygonFromLines } from "../../../utils/receipt";
import useReceiptGeometry from "../../../hooks/useReceiptGeometry";
import useReceiptClustering from "../../../hooks/useReceiptClustering";
import { getAnimationConfig } from "./animationConfig";

// Define simple point and line-segment shapes
const isDevelopment = process.env.NODE_ENV === "development";

/**
 * Find the top and bottom edges of lines at the secondary axis extremes
 */

/**
 * Display a random photo image with animated overlays that illustrate
 * how the receipt bounding box is derived from OCR line data.
 *
 * Uses the same Hull Edge Alignment geometry calculations as tested in
 * receipt.fixture.test.ts to ensure accurate CW/CCW neighbor selection
 * for skewed boundary lines.
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
  // Use pixel-based clustering to match Python implementation
  const { clusters, noiseLines } = useReceiptClustering(allLines, {
    minPoints: 10, // Match Python's min_samples=10
    imageWidth: imageDetails?.image?.width,
    imageHeight: imageDetails?.image?.height,
  });
  
  // Use only the lines from the largest cluster (main receipt)
  const lines = clusters.length > 0 
    ? clusters.sort((a, b) => b.lines.length - a.lines.length)[0].lines
    : [];
    
  const computedReceipt = estimateReceiptPolygonFromLines(lines);
  const receipts = computedReceipt
    ? [computedReceipt]
    : imageDetails?.receipts ?? [];

  const avgAngle =
    lines.length > 0
      ? lines.reduce((sum, l) => sum + l.angle_degrees, 0) / lines.length
      : 0;

  const {
    convexHullPoints,
    hullCentroid,
    finalAngle,
    hullExtremes,
    refinedSegments,
    boundaries,
    finalReceiptBox,
  } = useReceiptGeometry(lines);

  // Step 1 – Display OCR line boxes (see components/ui/Figures/PhotoReceiptBoundingBox.md)
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
  // Use medium size for PhotoReceiptBoundingBox to balance quality and performance
  // Use fallback URL during SSR/initial render to prevent hydration mismatch
  const cdnUrl =
    firstImage && formatSupport && isClient
      ? getBestImageUrl(firstImage, formatSupport, 'medium')
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
              
              {/* Optionally show noise lines in a subtle way */}
              {inView && noiseLines.map((line) => {
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

              {/* Step 2 – Compute the Convex Hull */}
              {/* Use Graham-scan algorithm to find minimal convex polygon enclosing all corners */}
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

              {/* Step 3 – Compute Hull Centroid */}
              {/* Calculate the average of all hull vertices to find the polygon's center point */}
              {inView && hullCentroid && (
                <AnimatedHullCentroid
                  key={`hull-centroid-${resetKey}`}
                  centroid={hullCentroid}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={centroidDelay}
                />
              )}

              {/* Step 4 – Estimate Initial Skew from OCR */}
              {/* Calculate each line's bottom-edge angle, filter near-zero angles, and average */}
              {inView && convexHullPoints.length > 0 && hullCentroid && (
                <AnimatedOrientedAxes
                  key={`oriented-axes-${resetKey}`}
                  hull={convexHullPoints}
                  centroid={hullCentroid}
                  lines={lines}
                  finalAngle={finalAngle}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay}
                />
              )}

              {/* Step 5 – Find Top and Bottom Boundary Candidates */}
              {/* Project hull vertices onto axis perpendicular to preliminary tilt, */}
              {/* then take the 2 smallest and 2 largest projections as boundary extremes */}
              {inView &&
                convexHullPoints.length > 0 &&
                hullCentroid &&
                lines.length > 0 && (
                  <AnimatedTopAndBottom
                    key={`top-and-bottom-${resetKey}`}
                    lines={lines}
                    hull={convexHullPoints}
                    centroid={hullCentroid}
                    avgAngle={avgAngle}
                    svgWidth={svgWidth}
                    svgHeight={svgHeight}
                    delay={extentsDelay + 1500}
                  />
                )}

              {/* Step 5 & 6 – Find Top/Bottom Boundaries and Compute Final Tilt */}
              {/* Step 5: Project hull vertices onto axis perpendicular to preliminary tilt */}
              {/* Step 6: Fit lines through extremes, compute angles, and average for final tilt */}
              {/* Step 7 – Find Left and Right Extremes Along Receipt Tilt */}
              {/* Project hull vertices onto axis defined by final receipt tilt */}
              {/* Step 8 – Refine with Hull Edge Alignment (CW/CCW Neighbor Comparison) */}
              {/* Compare adjacent hull neighbors using Hull Edge Alignment scoring */}
              {inView &&
                convexHullPoints.length > 0 &&
                hullCentroid &&
                lines.length > 0 &&
                refinedSegments && (
                  <AnimatedHullEdgeAlignment
                    key={`hull-edge-alignment-${resetKey}`}
                    hull={convexHullPoints}
                    refinedSegments={refinedSegments}
                    svgWidth={svgWidth}
                    svgHeight={svgHeight}
                    delay={extentsDelay + 2000}
                  />
                )}

              {/* Step 9 – Compute Final Receipt Quadrilateral */}
              {/* Intersect refined left/right boundaries with top/bottom edges */}
              {inView &&
                boundaries.top &&
                boundaries.bottom &&
                boundaries.left &&
                boundaries.right && (
                  <AnimatedFinalReceiptBox
                    key={`final-receipt-box-${resetKey}`}
                    boundaries={boundaries}
                    fallbackCentroid={hullCentroid}
                    svgWidth={svgWidth}
                    svgHeight={svgHeight}
                    delay={receiptDelay}
                  />
                )}
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
