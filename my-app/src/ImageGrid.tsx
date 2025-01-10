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
            {lines?.map((line, idx) => BoundingBox(line, img))}
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
