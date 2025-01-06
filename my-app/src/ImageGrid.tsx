// ImageGrid.tsx
import React, { useEffect, useState } from "react";

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
  x: number;
  y: number;
  width: number;
  height: number;
  angle: number;
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
  return data; // Return just the base64 string
}

/**
 * ImageGrid component
 * 1. Fetches the main list of images from the API
 * 2. Renders a gray rectangle for each image (scaled by aspect ratio)
 * 3. For each image, fetches the base64 data and embeds it in an SVG
 */
export default function ImageGrid() {
  /** Holds the list of images from the first API call */
  const [images, setImages] = useState<ImageItem[]>([]);

  // Now it stores the entire ImageDetailsResponse interface by image ID
  const [imageDetails, setImageDetails] = useState<
    Record<number, ImageDetailsResponse>
  >({});

  /**
   * 1. On mount, fetch the main list of images
   */
  useEffect(() => {
    fetchImages()
      .then((res) => {
        setImages(res.images);
      })
      .catch((err) => {
        console.error("Error fetching images:", err);
      });
  }, []);

  /**
   * 2. Whenever "images" changes (i.e., after they’re loaded),
   *    fetch details for each one. Each fetch updates the imageDetails state.
   */
  useEffect(() => {
    images.forEach((img) => {
      // Only fetch if we don't already have details in state
      if (!imageDetails[img.id]) {
        fetchImageDetails(img.id)
          .then((details) => {
            // details is of type ImageDetailsResponse
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
        // Example: fixed base width of 200, scale height accordingly
        const baseWidth = 150;
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

        // Once the base64 data arrives, embed it in an <svg>
        // with an <image> tag referencing the base64 string
        return (
          <svg
            key={img.id}
            width={baseWidth}
            height={scaledHeight}
            style={{
              margin: "8px",
              backgroundColor: "gray",
            }}
            xmlns="http://www.w3.org/2000/svg"
          >
            <image
              width={baseWidth}
              height={scaledHeight}
              // Use xlinkHref for inline SVG in React (some older browsers):
              xlinkHref={`data:image/jpeg;base64,${base64Data}`}
            />
            {/* Draw bounding boxes for each line */}
            {lines?.map((line) => {
              // Scale the normalized positions and dimensions
              // Assume line.x, line.y, line.width, line.height ∈ [0, 1]
              const rectX = line.x * baseWidth;
              const rectWidth = line.width * baseWidth;

              const rectY = line.y * scaledHeight;
              const rectHeight = line.height * scaledHeight;

              // Flip Y because Vision uses bottom-left as origin
              const flippedRectY = scaledHeight - rectY - rectHeight;

              // Rotate around the center of the box
              const centerX = rectX + rectWidth / 2;
              const centerY = flippedRectY + rectHeight / 2;

              const angleDeg = 180 - line.angle;

              const topRightX = rectX + rectWidth;
              const topRightY = flippedRectY;

              const bottomLeftX = rectX;
              const bottomLeftY = flippedRectY + rectHeight;

              return (
                <>
                  <rect
                    key={line.id}
                    x={rectX}
                    y={flippedRectY}
                    width={rectWidth}
                    height={rectHeight}
                    fill="none"
                    stroke="grey"
                    strokeWidth={1}
                    // Use the angle directly, flipping sign if needed
                    transform={`rotate(${angleDeg}, ${centerX}, ${centerY})`}
                  />
                  <circle cx={centerX} cy={centerY} r={2} fill="grey" />
                  <circle cx={topRightX} cy={topRightY} r={1} fill="green" />
                  <circle cx={bottomLeftX} cy={bottomLeftY} r={1} fill="red" />
                </>
              );
            })}
          </svg>
        );
      })}
    </div>
  );
}
