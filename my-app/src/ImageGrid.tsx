// ImageGrid.tsx
import React, { useEffect, useState } from 'react';

/** Interface for each image in the initial list */
interface ImageItem {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  s3_bucket: string;
  s3_key: string;
}

/** Interface for the details response of a single image */
interface ImageDetailsResponse {
    scaled_images: [
        {
            image_id: number;
            timestamp_added: string;
            base64: string;
            quality: number;
        }
    ];
}

/** Interface describing the entire API response object for the image list */
interface ImagesApiResponse {
  images: ImageItem[];
}

/** Interface for each image's details response (the second request) */
interface ImageDetailsResponse {
  base64_jpeg: string; // The base64-encoded JPEG string returned by the second API
}

/** Fetch the main list of images */
async function fetchImages(): Promise<ImagesApiResponse> {
  const response = await fetch('https://wso2tnfiie.execute-api.us-east-1.amazonaws.com/images');
  if (!response.ok) {
    throw new Error('Network response was not ok');
  }
  // Cast the returned JSON to our ImagesApiResponse interface
  const data = (await response.json()) as ImagesApiResponse;
  return data;
}

/** Fetch the details for a single image (base64-encoded JPEG) */
async function fetchImageDetails(imageId: number): Promise<string> {
  const response = await fetch(
    `https://wso2tnfiie.execute-api.us-east-1.amazonaws.com/image_details?image_id=${imageId}`
  );
  if (!response.ok) {
    throw new Error(`Failed to fetch details for image ${imageId}`);
  }
  const data = (await response.json()) as ImageDetailsResponse;
  return data.scaled_images[0].base64; // Return just the base64 string
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

  /**
   * Holds the base64 data for each image, keyed by image ID.
   * Example: { 42: "iVBORw0KGgoAAAANSUhEUgAAA...", 24: "...", ... }
   */
  const [imageDetails, setImageDetails] = useState<Record<number, string>>({});

  /**
   * 1. On mount, fetch the main list of images
   */
  useEffect(() => {
    fetchImages()
      .then((res) => {
        setImages(res.images);
      })
      .catch((err) => {
        console.error('Error fetching images:', err);
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
          .then((base64) => {
            setImageDetails((prev) => ({
              ...prev,
              [img.id]: base64,
            }));
          })
          .catch((err) => {
            console.error(`Error fetching details for image ${img.id}:`, err);
          });
      }
    });
  }, [images, imageDetails]);

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap' }}>
      {images.map((img) => {
        // Example: fixed base width of 200, scale height accordingly
        const baseWidth = 200;
        const aspectRatio = img.height / img.width;
        const scaledHeight = baseWidth * aspectRatio;

        // If the base64 data isn't loaded yet, just show a gray rectangle
        const base64Data = imageDetails[img.id];
        if (!base64Data) {
          return (
            <div
              key={img.id}
              style={{
                width: baseWidth,
                height: scaledHeight,
                margin: '8px',
                backgroundColor: 'gray',
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
              margin: '8px',
              backgroundColor: 'gray',
            }}
            xmlns="http://www.w3.org/2000/svg"
          >
            <image
              width={baseWidth}
              height={scaledHeight}
              // Use xlinkHref for inline SVG in React (some older browsers):
              xlinkHref={`data:image/jpeg;base64,${base64Data}`}
            />
          </svg>
        );
      })}
    </div>
  );
}