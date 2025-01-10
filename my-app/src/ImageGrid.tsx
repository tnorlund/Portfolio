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
              // X and Y are normalized to [0, 1] in the Vision API
              // They are the bottom left corner of the bounding box
              const x = line.x * baseWidth;
              const y = line.y * scaledHeight;
              // The angle is in degrees
              const angleRad = (Math.PI / 180) * line.angle;
              // flip the Y coordinate
              const flippedY = scaledHeight - y;
              const bottomLeft = { x, y: flippedY };

              // Calculate the bottom right corner of the box
              const bottomRight = {
                x: bottomLeft.x + line.width * baseWidth * Math.cos(angleRad),
                y: bottomLeft.y + line.width * baseWidth * Math.sin(angleRad),
              };


              return (
                <>
                  <line
                    x1={bottomLeft.x}
                    y1={bottomLeft.y}
                    x2={bottomRight.x}
                    y2={bottomRight.y}
                    stroke="grey"
                    strokeWidth={1}
                  />
                </>
              );
            })}

            {/* Add the image ID to the SVG */}
            <text x={5} y={scaledHeight - 5} fill="black">
              {img.id}
            </text>
          </svg>
        );
      })}
    </div>
  );
}
