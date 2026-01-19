import React, { useEffect, useState } from "react";

import { animated, useSpring, useTransition } from "@react-spring/web";
import useImageDetails from "../../../hooks/useImageDetails";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import { getBestImageUrl } from "../../../utils/imageFormat";
import AnimatedLineBox from "../animations/AnimatedLineBox";
import ReceiptBoundingBoxFrame from "./ReceiptBoundingBoxFrame";
import type { CropViewBox } from "./utils/smartCrop";

const isDevelopment = process.env.NODE_ENV === "development";

// AnimatedReceipt: component for receipt bounding box and centroid animation
interface AnimatedReceiptProps {
  receipt: any; // Adjust type accordingly
  svgWidth: number;
  svgHeight: number;
  delay: number;
  cropInfo: CropViewBox | null;
  fullImageWidth: number;
  fullImageHeight: number;
}

const AnimatedReceipt: React.FC<AnimatedReceiptProps> = ({
  receipt,
  svgWidth,
  svgHeight,
  delay,
  cropInfo,
  fullImageWidth,
  fullImageHeight,
}) => {
  // Transform normalized coordinates to SVG pixel coordinates
  // The viewBox handles the crop offset, so we just convert to full image coordinates
  const transformX = (normX: number) => normX * fullImageWidth;
  const transformY = (normY: number) => (1 - normY) * fullImageHeight;

  // Compute the receipt bounding box corners.
  const x1 = transformX(receipt.top_left.x);
  const y1 = transformY(receipt.top_left.y);
  const x2 = transformX(receipt.top_right.x);
  const y2 = transformY(receipt.top_right.y);
  const x3 = transformX(receipt.bottom_right.x);
  const y3 = transformY(receipt.bottom_right.y);
  const x4 = transformX(receipt.bottom_left.x);
  const y4 = transformY(receipt.bottom_left.y);
  const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

  // Compute the centroid of the receipt bounding box.
  const centroidX = (x1 + x2 + x3 + x4) / 4;
  const centroidY = (y1 + y2 + y3 + y4) / 4;
  // midY is the center of the visible area (crop region or full image)
  const midY = cropInfo
    ? cropInfo.y + cropInfo.height / 2  // Center of crop region in full image coords
    : fullImageHeight / 2;

  // Animate the receipt bounding box to fade in.
  const boxSpring = useSpring({
    from: { opacity: 0 },
    to: { opacity: 1 },
    delay: delay,
    config: { duration: 800 },
  });

  // Animate the receipt centroid marker:
  // Start at midY, then animate to the computed receipt centroid.
  const centroidSpring = useSpring({
    from: { opacity: 0, cy: midY },
    to: async (next) => {
      await next({ opacity: 1, cy: midY, config: { duration: 400 } });
      await next({ cy: centroidY, config: { duration: 800 } });
    },
    delay: delay, // Same delay as the bounding box fade in.
  });

  return (
    <>
      <animated.polygon
        style={boxSpring}
        points={points}
        fill="var(--color-blue)"
        fillOpacity={0.2}
        stroke="var(--color-blue)"
        strokeWidth="6"
      />
      <animated.circle
        cx={centroidX}
        cy={centroidSpring.cy}
        r={10}
        fill="var(--color-blue)"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

// Main ImageBoundingBox component
const ImageBoundingBox: React.FC = () => {
  const { imageDetails, formatSupport, error } = useImageDetails();
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
  const receipts = imageDetails?.receipts ?? [];

  // Animate word bounding boxes using a transition.
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

  // Compute the total delay for word animations.
  const totalDelayForLines =
    lines.length > 0 ? (lines.length - 1) * 30 + 1130 : 0;

  // Use the first image from the API.
  const firstImage = imageDetails?.image;


  // Get the optimal image URL based on browser support and available formats
  // Use medium size for ScanBoundingBox to balance quality and performance
  // Use fallback URL during SSR/initial render to prevent hydration mismatch
  const cdnUrl =
    firstImage && formatSupport && isClient
      ? getBestImageUrl(firstImage, formatSupport, 'medium')
      : firstImage
        ? `${isDevelopment
          ? "https://dev.tylernorlund.com"
          : "https://www.tylernorlund.com"
        }/${firstImage.cdn_s3_key}`
        : "";


  // When imageDetails is loaded, compute these values;
  // otherwise, fall back on default dimensions.
  const svgWidth = firstImage ? firstImage.width : defaultSvgWidth;
  const svgHeight = firstImage ? firstImage.height : defaultSvgHeight;

  useEffect(() => {
    if (!inView) {
      setResetKey(k => k + 1);
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
              {/* Full image - viewBox handles the cropping */}
              <image
                href={cdnUrl}
                x={0}
                y={0}
                width={fullImageWidth}
                height={fullImageHeight}
              />

              {/* Render animated word bounding boxes (via transition) */}
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

              {/* Render animated word centroids */}
              {inView &&
                lines.map((line, index) => (
                  <AnimatedLineBox
                    key={`${line.line_id}`}
                    line={line}
                    svgWidth={cropInfo ? cropInfo.width : fullImageWidth}
                    svgHeight={cropInfo ? cropInfo.height : fullImageHeight}
                    delay={index * 30}
                    cropInfo={cropInfo}
                    fullImageWidth={fullImageWidth}
                    fullImageHeight={fullImageHeight}
                  />
                ))}

              {/* Render animated receipt bounding boxes and centroids */}
              {inView &&
                receipts.map((receipt, index) => (
                  <AnimatedReceipt
                    key={`receipt-${receipt.receipt_id}`}
                    receipt={receipt}
                    svgWidth={cropInfo ? cropInfo.width : fullImageWidth}
                    svgHeight={cropInfo ? cropInfo.height : fullImageHeight}
                    delay={totalDelayForLines + index * 100}
                    cropInfo={cropInfo}
                    fullImageWidth={fullImageWidth}
                    fullImageHeight={fullImageHeight}
                  />
                ))}
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

export default ImageBoundingBox;
