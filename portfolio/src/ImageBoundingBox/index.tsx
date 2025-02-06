import React, { useState, useEffect } from "react";
import { fetchImageDetails } from "../api";
import { ImageDetailsApiResponse } from "../interfaces";
import { useSpring, useTransition, animated } from "@react-spring/web";

const isDevelopment = process.env.NODE_ENV === "development";

// ----------------------------------------------------
// AnimatedWordBox: already defined for words (unchanged)
interface AnimatedWordBoxProps {
  word: any; // Adjust type as needed
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedWordBox: React.FC<AnimatedWordBoxProps> = ({
  word,
  svgWidth,
  svgHeight,
  delay,
}) => {
  // Convert normalized coordinates to absolute pixel values.
  const x1 = word.top_left.x * svgWidth;
  const y1 = (1 - word.top_left.y) * svgHeight;
  const x2 = word.top_right.x * svgWidth;
  const y2 = (1 - word.top_right.y) * svgHeight;
  const x3 = word.bottom_right.x * svgWidth;
  const y3 = (1 - word.bottom_right.y) * svgHeight;
  const x4 = word.bottom_left.x * svgWidth;
  const y4 = (1 - word.bottom_left.y) * svgHeight;
  const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

  // Compute the polygon's centroid.
  const centroidX = (x1 + x2 + x3 + x4) / 4;
  const centroidY = (y1 + y2 + y3 + y4) / 4;

  // Animate the polygon scaling from 0 to 1, with the centroid as the origin.
  const polygonSpring = useSpring({
    from: { transform: "scale(0)" },
    to: { transform: "scale(1)" },
    delay: delay,
    config: { duration: 800 },
  });

  // Animate the centroid marker:
  // 1. Fade in at the computed centroid.
  // 2. Then animate its y coordinate to mid‑Y.
  const centroidSpring = useSpring({
    from: { opacity: 0, cy: centroidY },
    to: async (next) => {
      await next({ opacity: 1, cy: centroidY, config: { duration: 300 } });
      await next({ cy: svgHeight / 2, config: { duration: 800 } });
    },
    delay: delay + 30,
  });

  return (
    <>
      <animated.polygon
        style={{
            ...polygonSpring,
            transformOrigin: "50% 50%",
            transformBox: "fill-box",
          }}
        points={points}
        fill="none"
        stroke="red"
        strokeWidth="2"
      />
      <animated.circle
        cx={centroidX}
        cy={centroidSpring.cy}
        r={10}
        fill="red"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

// ----------------------------------------------------
// AnimatedReceipt: new component for receipt bounding box and centroid animation.
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
        fill="none"
        stroke="blue"
        strokeWidth="4"
      />
      <animated.circle
        cx={centroidX}
        cy={centroidSpring.cy}
        r={10}
        fill="blue"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

// ----------------------------------------------------
// Main ImageBoundingBox component
const ImageBoundingBox: React.FC = () => {
  const [imageDetails, setImageDetails] =
    useState<ImageDetailsApiResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const [resetKey, setResetKey] = useState(0);

  useEffect(() => {
    const loadImageDetails = async () => {
      try {
        const details = await fetchImageDetails();
        console.log("Image details:", details);
        setImageDetails(details);
      } catch (err) {
        setError(err as Error);
      }
    };

    loadImageDetails();
  }, []);

  // Unconditionally extract words and receipts.
  const words = imageDetails?.words ?? [];
  const receipts = imageDetails?.receipts ?? [];

  // Animate word bounding boxes using a transition.
  const wordTransitions = useTransition(words, {
    keys: (word) => `${word.line_id}-${word.word_id}`,
    from: { opacity: 0, transform: "scale(0.8)" },
    enter: (item, index) => ({
      opacity: 1,
      transform: "scale(1)",
      delay: index * 30,
    }),
    config: { duration: 800 },
  });

  // Compute the total delay for word animations.
  const totalDelayForWords =
    words.length > 0 ? (words.length - 1) * 30 + 1130 : 0;

  if (error) {
    return <div>Error loading image details: {error.message}</div>;
  }
  if (!imageDetails) {
    return <div>Loading...</div>;
  }
  if (imageDetails.images.length === 0) {
    return <div>No images available</div>;
  }

  // Use the first image from the API.
  const firstImage = imageDetails.images[0];
  const cdnUrl = isDevelopment
    ? `https://dev.tylernorlund.com/${firstImage.cdn_s3_key}`
    : `https://www.tylernorlund.com/${firstImage.cdn_s3_key}`;

  const svgWidth = firstImage.width;
  const svgHeight = firstImage.height;

  // Scale the displayed SVG to a maximum width of 400px.
  const maxDisplayWidth = 400;
  const scaleFactor = Math.min(1, maxDisplayWidth / svgWidth);
  const displayWidth = svgWidth * scaleFactor;
  const displayHeight = svgHeight * scaleFactor;

  return (
    <div>
      <h1>ImageBoundingBox</h1>
    <div style={{ margin: "0 auto" }}>
      <svg
        key={resetKey}
        onClick={() => setResetKey((k) => k + 1)}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        width={displayWidth}
        height={displayHeight}
      >
        {/* Render the background image */}
        <image href={cdnUrl} x="0" y="0" width={svgWidth} height={svgHeight} />

        {/* Render animated word bounding boxes (via transition) */}
        {wordTransitions((style, word) => {
          const x1 = word.top_left.x * svgWidth;
          const y1 = (1 - word.top_left.y) * svgHeight;
          const x2 = word.top_right.x * svgWidth;
          const y2 = (1 - word.top_right.y) * svgHeight;
          const x3 = word.bottom_right.x * svgWidth;
          const y3 = (1 - word.bottom_right.y) * svgHeight;
          const x4 = word.bottom_left.x * svgWidth;
          const y4 = (1 - word.bottom_left.y) * svgHeight;
          const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;
          return (
            <animated.polygon
              key={`${word.line_id}-${word.word_id}`}
              style={style}
              points={points}
              fill="none"
              stroke="red"
              strokeWidth="2"
            />
          );
        })}

        {/* Render animated word centroids */}
        {words.map((word, index) => (
          <AnimatedWordBox
            key={`${word.line_id}-${word.word_id}`}
            word={word}
            svgWidth={svgWidth}
            svgHeight={svgHeight}
            delay={index * 30}
          />
        ))}

        {/* Render animated receipt bounding boxes and centroids.
            Their animations start after all word centroids have finished. */}
        {receipts.map((receipt, index) => (
          <AnimatedReceipt
            key={`receipt-${receipt.receipt_id}`}
            receipt={receipt}
            svgWidth={svgWidth}
            svgHeight={svgHeight}
            delay={totalDelayForWords + index * 100}
          />
        ))}
      </svg>
      </div>
    </div>
  );
};

export default ImageBoundingBox;