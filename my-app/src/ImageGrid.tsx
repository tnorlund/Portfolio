// ImageGrid.tsx
import React, { useEffect, useState } from "react";
import BoundingBox from "./boundingBox";

/** Interface for each image in the initial list */
interface ImageItem {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  s3_bucket: string;
  s3_key: string;
}

/** Interface for each line from OCR */
interface LineItem {
  image_id: number;
  id: number;
  text: string;
  x: number; // normalized [0..1]
  y: number; // normalized [0..1], presumably bottom-left from OCR
  width: number; // normalized [0..1]
  height: number; // normalized [0..1]
  angle: number; // degrees
  confidence: number;
}

/** Interface for Scaled Image */
interface ScaledImage {
  image_id: number;
  timestamp_added: string;
  base64: string;
  quality: number;
}

/** Interface for the details response of a single image */
interface ImageDetailsResponse {
  image: ImageItem;
  lines: LineItem[];
  scaled_images: ScaledImage[];
}

/** Interface describing the entire API response object for the image list */
interface ImagesApiResponse {
  images: ImageItem[];
}

function FittableText({
  text,
  x,
  y,
  angleDeg,
  boxWidth,
  boxHeight,
  opacity,
  initialSize = 50,
}: {
  text: string;
  x: number;
  y: number;
  angleDeg: number;    // in degrees
  boxWidth: number;    // bounding box width in SVG units
  boxHeight: number;   // bounding box height in SVG units
  opacity?: number;
  initialSize?: number; // optional initial font size
}) {
  const [fontSize, setFontSize] = React.useState(initialSize);
  const textRef = React.useRef<SVGTextElement>(null);

  React.useEffect(() => {
    if (textRef.current) {
      const bbox = textRef.current.getBBox();
      const boxAspectRatio = boxWidth / boxHeight;
      if (bbox.width > 0 && bbox.height > 0) {
        // How many times bigger do we need to scale to fill the box width or height?
        const scaleW = boxWidth / bbox.width;
        const scaleH = boxHeight / bbox.height;

        // Pick the larger scale so the text will reach EITHER top/bottom OR left/right
        // This may cause overflow in the smaller dimension.
        const scale = Math.min(scaleW, scaleH);

        // Allow the text to grow above initialSize if needed
        if (scale !== 1) {
          setFontSize((prev) => prev * scale);
        }
      }
    }
    // Re-run if the text or box size changes
  }, [text, boxWidth, boxHeight]);
  return (
    <text
      ref={textRef}
      x={x}
      y={y}
      textAnchor="middle"
      alignmentBaseline="middle"
      fontSize={fontSize}
      opacity={opacity}
      fill="black"
      transform={`rotate(${angleDeg}, ${x}, ${y})`}
    >
      {text}
    </text>
  );
}

/** Fetch the main list of images */
async function fetchImages(): Promise<ImagesApiResponse> {
  const response = await fetch(
    "https://wso2tnfiie.execute-api.us-east-1.amazonaws.com/images"
  );
  if (!response.ok) {
    throw new Error("Network response was not ok");
  }
  // Cast the returned JSON to our ImagesApiResponse interface
  const data = (await response.json()) as ImagesApiResponse;
  return data;
}

/** Fetch the details for a single image */
async function fetchImageDetails(
  imageId: number
): Promise<ImageDetailsResponse> {
  const response = await fetch(
    `https://wso2tnfiie.execute-api.us-east-1.amazonaws.com/image_details?image_id=${imageId}`
  );
  if (!response.ok) {
    throw new Error(`Failed to fetch details for image ${imageId}`);
  }
  const data = (await response.json()) as ImageDetailsResponse;
  return data;
}

/**
 * ImageGrid component
 * 1. Fetches the main list of images from the API
 * 2. Renders a scaled rectangle for each image
 * 3. For each image, fetches the base64 data and embeds it in an <svg>
 */
export default function ImageGrid() {
  const [images, setImages] = useState<ImageItem[]>([]);
  const [imageDetails, setImageDetails] = useState<
    Record<number, ImageDetailsResponse>
  >({});

  // On mount, fetch the main list of images
  useEffect(() => {
    fetchImages()
      .then((res) => {
        setImages(res.images);
      })
      .catch((err) => {
        console.error("Error fetching images:", err);
      });
  }, []);

  // Whenever "images" changes, fetch details for each one
  useEffect(() => {
    images.forEach((img) => {
      if (!imageDetails[img.id]) {
        fetchImageDetails(img.id)
          .then((details) => {
            setImageDetails((prev) => ({
              ...prev,
              [img.id]: details,
            }));
          })
          .catch((err) => {
            console.error(`Error fetching details for image ${img.id}:`, err);
          });
      }
    });
  }, [images, imageDetails]);

  return (
    <div style={{ display: "flex", flexWrap: "wrap" }}>
      {images.map((img) => {
        // We’ll display each image in a 500px wide container, scaled in height.
        const baseWidth = 500;
        const aspectRatio = img.height / img.width;
        const scaledHeight = baseWidth * aspectRatio;

        // If the base64 data isn't loaded yet, just show a gray rectangle
        const base64Data = imageDetails[img.id]?.scaled_images?.[0]?.base64;
        const lines = imageDetails[img.id]?.lines;
        if (!base64Data) {
          return (
            <div
              key={img.id}
              style={{
                width: baseWidth,
                height: scaledHeight,
                margin: "8px",
                backgroundColor: "gray",
              }}
            />
          );
        }

        // Render an SVG with a viewBox that matches the original image’s pixel size
        return (
          <svg
            key={img.id}
            width={baseWidth}
            height={scaledHeight}
            style={{ margin: "8px", backgroundColor: "gray" }}
            viewBox={`0 0 ${img.width} ${img.height}`}
            preserveAspectRatio="xMidYMid meet"
            xmlns="http://www.w3.org/2000/svg"
          >
            <image
              width={img.width}
              height={img.height}
              preserveAspectRatio="xMidYMid meet"
              xlinkHref={`data:image/jpeg;base64,${base64Data}`}
            />
            {/* Draw bounding boxes for each line */}
            {lines?.map((line, idx) => {
              // Convert from normalized [0..1] to image pixels
              // If line.y is bottom-left-based, we flip it to top-left for SVG:
              const x = line.x * img.width;
              const y = line.y * img.height;
              const flippedY = img.height - y;
              const bottomLeft = { x, y: flippedY };

              // Convert angle from degrees, and negate if Vision is CCW but we want a different orientation
              const angleRad = -(Math.PI / 180) * line.angle;
              const angleDeg = -line.angle;

              // Box width & height in image pixels:
              const boxW = line.width * img.width;
              const boxH = line.height * img.height;

              // Bottom-right corner
              const bottomRight = {
                x: bottomLeft.x + boxW * Math.cos(angleRad),
                y: bottomLeft.y + boxW * Math.sin(angleRad),
              };

              // Top-left corner
              const topLeft = {
                x: bottomLeft.x - boxH * Math.sin(angleRad),
                y: bottomLeft.y + boxH * Math.cos(angleRad),
              };

              // Top-right corner
              const topRight = {
                x: bottomRight.x - boxH * Math.sin(angleRad),
                y: bottomRight.y + boxH * Math.cos(angleRad),
              };

              const centroid = {
                x: (topLeft.x + topRight.x + bottomLeft.x + bottomRight.x) / 4,
                y: (topLeft.y + topRight.y + bottomLeft.y + bottomRight.y) / 4,
              };

              // calculate the distance between the top-left and top-right corners
              const width = Math.sqrt(
                Math.pow(topRight.x - topLeft.x, 2) +
                  Math.pow(topRight.y - topLeft.y, 2)
              );
              // calculate the distance between the top-left and bottom-left corners
              const height = Math.sqrt(
                Math.pow(bottomLeft.x - topLeft.x, 2) +
                  Math.pow(bottomLeft.y - topLeft.y, 2)
              );

              // Example: draw circles at bottom-left & bottom-right
              return (
                <React.Fragment key={idx}>
                  {/* diagonal */}
                  <line
                    x1={bottomLeft.x}
                    y1={bottomLeft.y}
                    x2={topRight.x}
                    y2={topRight.y}
                    stroke="gray"
                    strokeWidth={1}
                    opacity={line.confidence}
                  />
                  <line
                    x1={topLeft.x}
                    y1={topLeft.y}
                    x2={bottomRight.x}
                    y2={bottomRight.y}
                    stroke="gray"
                    strokeWidth={1}
                    opacity={line.confidence}
                  />
                  {/* horizontal */}
                  <line
                    x1={topLeft.x}
                    y1={topLeft.y}
                    x2={topRight.x}
                    y2={topRight.y}
                    stroke="gray"
                    strokeWidth={3}
                    opacity={line.confidence}
                  />
                  <line
                    x1={bottomLeft.x}
                    y1={bottomLeft.y}
                    x2={bottomRight.x}
                    y2={bottomRight.y}
                    stroke="gray"
                    strokeWidth={3}
                    opacity={line.confidence}
                  />
                  {/* vertical */}
                  <line
                    x1={topLeft.x}
                    y1={topLeft.y}
                    x2={bottomLeft.x}
                    y2={bottomLeft.y}
                    stroke="gray"
                    strokeWidth={3}
                    opacity={line.confidence}
                  />
                  <line
                    x1={topRight.x}
                    y1={topRight.y}
                    x2={bottomRight.x}
                    y2={bottomRight.y}
                    stroke="gray"
                    strokeWidth={3}
                    opacity={line.confidence}
                  />
                </React.Fragment>
              );
            })}
            {/* Label the image ID */}
            <text
              x={10}
              y={250}
              fill="black"
              fontSize={250}
              style={{ pointerEvents: "none" }}
            >
              {img.id}
            </text>
          </svg>
        );
      })}
    </div>
  );
}
