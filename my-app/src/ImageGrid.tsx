// ImageGrid.tsx
import React, { useEffect, useState } from 'react';

/** Interface describing a single image item */
interface ImageItem {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  s3_bucket: string;
  s3_key: string;
}

/** Interface describing the entire API response object */
interface ImagesApiResponse {
  images: ImageItem[];
}

/** Fetch function that returns a promise resolving to ImagesApiResponse */
async function fetchImages(): Promise<ImagesApiResponse> {
  // Fetch from your actual API endpoint
  const response = await fetch('https://wso2tnfiie.execute-api.us-east-1.amazonaws.com/images');
  if (!response.ok) {
    throw new Error('Network response was not ok');
  }
  // Cast the returned JSON to our ImagesApiResponse interface
  const data = (await response.json()) as ImagesApiResponse;
  return data;
}

/**
 * ImageGrid component
 * - Fetches images from the API on mount
 * - Displays a gray rectangle for each image, scaled by aspect ratio
 */
export default function ImageGrid() {
  const [images, setImages] = useState<ImageItem[]>([]);

  useEffect(() => {
    fetchImages()
      .then((res) => {
        // `res.images` is the array of image objects
        setImages(res.images);
      })
      .catch((err) => {
        console.error('Error fetching images:', err);
      });
  }, []);

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap' }}>
      {images.map((img) => {
        // Example: fixed base width of 200, scale the height accordingly
        const baseWidth = 200;
        const aspectRatio = img.height / img.width;
        const scaledHeight = baseWidth * aspectRatio;

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
      })}
    </div>
  );
}