import React, { useState, useEffect } from "react";
import { fetchImageDetails } from "../api";
import { ImageDetailsApiResponse } from "../interfaces";
import { useSpring, animated } from "@react-spring/web";

const isDevelopment = process.env.NODE_ENV === "development";

// Define a component for each animated word bounding box.
interface AnimatedWordBoxProps {
  word: any; // Adjust the type as needed
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedWordBox: React.FC<AnimatedWordBoxProps> = ({ word, svgWidth, svgHeight, delay }) => {
  // Compute the absolute coordinates for each corner:
  const x1 = word.top_left.x * svgWidth;
  const y1 = (1 - word.top_left.y) * svgHeight;
  const x2 = word.top_right.x * svgWidth;
  const y2 = (1 - word.top_right.y) * svgHeight;
  const x3 = word.bottom_right.x * svgWidth;
  const y3 = (1 - word.bottom_right.y) * svgHeight;
  const x4 = word.bottom_left.x * svgWidth;
  const y4 = (1 - word.bottom_left.y) * svgHeight;
  const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

  // Compute the centroid as the average of the corners.
  const centroidX = (x1 + x2 + x3 + x4) / 4;
  const centroidY = (y1 + y2 + y3 + y4) / 4;

  // Animate the polygon: scale in from 0 to 1.
  // We set the transform origin to the centroid so that scaling happens from the center.
  const polygonSpring = useSpring({
    from: { transform: "scale(0)" },
    to: { transform: "scale(1)" },
    delay: delay,
    config: { duration: 30 },
  });

  // Animate the centroid marker (a small circle) to fade in after the polygon animation finishes.
  const centroidSpring = useSpring({
    from: { opacity: 0 },
    to: { opacity: 1 },
    delay: delay + 30, // show the centroid after the polygon finishes scaling in
    config: { duration: 30 },
  });

  return (
    <>
      <animated.polygon
        style={{
          ...polygonSpring,
          // Set the transform origin to the computed centroid (in pixels).
          transformOrigin: `${centroidX}px ${centroidY}px`,
        }}
        points={points}
        fill="none"
        stroke="red"
        strokeWidth="2"
      />
      <animated.circle
        cx={centroidX}
        cy={centroidY}
        r={4}
        fill="blue"
        style={centroidSpring}
      />
    </>
  );
};

const ImageBoundingBox: React.FC = () => {
  const [imageDetails, setImageDetails] = useState<ImageDetailsApiResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);

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

  if (error) {
    return <div>Error loading image details: {error.message}</div>;
  }
  if (!imageDetails) {
    return <div>Loading...</div>;
  }
  if (imageDetails.images.length === 0) {
    return <div>No images available</div>;
  }

  // Get the first image.
  const firstImage = imageDetails.images[0];
  const cdnUrl = isDevelopment
    ? `https://dev.tylernorlund.com/${firstImage.cdn_s3_key}`
    : `https://www.tylernorlund.com/${firstImage.cdn_s3_key}`;

  const svgWidth = firstImage.width;
  const svgHeight = firstImage.height;

  // Calculate a scale factor so that the displayed SVG has a maximum width of 400px.
  const maxDisplayWidth = 400;
  const scaleFactor = Math.min(1, maxDisplayWidth / svgWidth);
  const displayWidth = svgWidth * scaleFactor;
  const displayHeight = svgHeight * scaleFactor;

  const words = imageDetails.words ?? [];

  return (
    <div>
      <h1>ImageBoundingBox</h1>
      <svg
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        width={displayWidth}
        height={displayHeight}
      >
        {/* Render the base image */}
        <image href={cdnUrl} x="0" y="0" width={svgWidth} height={svgHeight} />
        {/* Render an AnimatedWordBox for each word.
            The delay is set per word (e.g. 300ms apart). */}
        {words.map((word, index) => (
          <AnimatedWordBox
            key={`${word.line_id}-${word.word_id}`}
            word={word}
            svgWidth={svgWidth}
            svgHeight={svgHeight}
            delay={index * 10}
          />
        ))}
      </svg>
    </div>
  );
};

export default ImageBoundingBox;