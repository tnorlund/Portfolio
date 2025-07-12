import React, { useEffect, useState } from "react";

import { useSpring, useTransition, animated } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import AnimatedLineBox from "../animations/AnimatedLineBox";
import { getBestImageUrl } from "../../../utils/imageFormat";
import useImageDetails from "../../../hooks/useImageDetails";

const isDevelopment = process.env.NODE_ENV === "development";

// AnimatedReceipt: component for receipt bounding box and centroid animation
interface AnimatedReceiptProps {
  receipt: any; // Adjust type accordingly
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedReceipt: React.FC<AnimatedReceiptProps> = ({
  receipt,
  svgWidth,
  svgHeight,
  delay,
}) => {
  // Compute the receipt bounding box corners.
  const x1 = receipt.top_left.x * svgWidth;
  const y1 = (1 - receipt.top_left.y) * svgHeight;
  const x2 = receipt.top_right.x * svgWidth;
  const y2 = (1 - receipt.top_right.y) * svgHeight;
  const x3 = receipt.bottom_right.x * svgWidth;
  const y3 = (1 - receipt.bottom_right.y) * svgHeight;
  const x4 = receipt.bottom_left.x * svgWidth;
  const y4 = (1 - receipt.bottom_left.y) * svgHeight;
  const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

  // Compute the centroid of the receipt bounding box.
  const centroidX = (x1 + x2 + x3 + x4) / 4;
  const centroidY = (y1 + y2 + y3 + y4) / 4;
  const midY = svgHeight / 2;

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
      setResetKey(k => k + 1);
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

              {/* Render animated word centroids */}
              {inView &&
                lines.map((line, index) => (
                  <AnimatedLineBox
                    key={`${line.line_id}`}
                    line={line}
                    svgWidth={svgWidth}
                    svgHeight={svgHeight}
                    delay={index * 30}
                  />
                ))}

              {/* Render animated receipt bounding boxes and centroids */}
              {inView &&
                receipts.map((receipt, index) => (
                  <AnimatedReceipt
                    key={`receipt-${receipt.receipt_id}`}
                    receipt={receipt}
                    svgWidth={svgWidth}
                    svgHeight={svgHeight}
                    delay={totalDelayForLines + index * 100}
                  />
                ))}
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

export default ImageBoundingBox;
