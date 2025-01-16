// ImageGrid.tsx
import React, { useEffect, useState } from "react";
import BoundingBox from "./boundingBox";

/** Represents a single image from the API */
interface ImageItem {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  s3_bucket: string;
  s3_key: string;
  cdn_s3_key: string;
}

/** A point in 2D space */
interface Point {
  x: number;
  y: number;
}

/** A bounding box (x, y, width, height) */
interface BoundingBoxInterface {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Represents a line of OCR text */
interface LineItem {
  image_id: number;
  id: number;
  text: string;
  bounding_box: BoundingBoxInterface;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  angle_degrees: number; 
  angle_radians: number;
  confidence: number;
}

/** Represents a receipt annotation on the image */
interface Receipt {
  id: number;
  image_id: number;
  width: number;
  height: number;
  timestamp_added: string;
  s3_bucket: string;
  s3_key: string;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  cdn_s3_key: string;
}

export interface ImagePayload {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  s3_bucket: string;
  s3_key: string;
  sha256: string;
  cdn_s3_key: string;
}

/** PayloadItem now includes the arrays of `receipts` and `lines` */
export interface PayloadItem {
  image: ImagePayload;
  lines: LineItem[];
  receipts: Receipt[];
}

export interface RootPayload {
  [key: string]: PayloadItem; // e.g. "1", "2", "3", etc.
}

export interface ApiResponse {
  payload: RootPayload;
  last_evaluated_key: null | string; // or null | number, depending on usage
}

/** A tuple type: [ImageItem, Receipt[], LineItem[]]. */
type ImageReceiptsLines = [ImageItem, Receipt[], LineItem[]];

/**
 * Convert RootPayload into an array of [ImageItem, Receipt[], LineItem[]].
 */
export function mapPayloadToImages(payload: RootPayload): ImageReceiptsLines[] {
  return Object.values(payload).map((item) => {
    const image: ImageItem = {
      id: item.image.id,
      width: item.image.width,
      height: item.image.height,
      timestamp_added: item.image.timestamp_added,
      s3_bucket: item.image.s3_bucket,
      s3_key: item.image.s3_key,
      cdn_s3_key: item.image.cdn_s3_key,
      // If you want sha256 in your ImageItem, add it here
      // sha256: item.image.sha256,
    };

    const receipts = item.receipts || [];
    const lines = item.lines || [];

    return [image, receipts, lines];
  });
}

/** Fetches the main list of images from the new API shape and returns the tuple array */
async function fetchImages(): Promise<ImageReceiptsLines[]> {
  const response = await fetch("https://wso2tnfiie.execute-api.us-east-1.amazonaws.com/images");
  if (!response.ok) {
    throw new Error(`Network response was not ok (status: ${response.status})`);
  }

  const data = (await response.json()) as ApiResponse;
  return mapPayloadToImages(data.payload);
}

/**
 * ImageGrid component
 * 1. Fetches the main list of images ([image, receipts, lines] tuples)
 * 2. Renders a scaled rectangle for each image
 * 3. Potentially uses receipts/lines to draw bounding boxes (if desired)
 */
export default function ImageGrid() {
  const [imageReceiptLines, setImageReceiptLines] = useState<ImageReceiptsLines[]>([]);

  useEffect(() => {
    fetchImages()
      .then((imageItems) => {
        setImageReceiptLines(imageItems);
      })
      .catch((err) => {
        console.error("Error fetching images:", err);
      });
  }, []);

  return (
    <div style={{ display: "flex", flexWrap: "wrap" }}>
      {/**
       * Because each element is a tuple [image, receipts, lines],
       * we destructure them like so:
       */}
      {imageReceiptLines.map(([image, receipts, lines]) => {
        const cdn_url = `https://d3izz2n0uhacrm.cloudfront.net/${image.cdn_s3_key}`;

        console.log(cdn_url)
        
        // We’ll display each image in a 500px-wide container, scaled in height.
        const baseWidth = 500;
        const aspectRatio = image.height / image.width;
        const scaledHeight = baseWidth * aspectRatio;

        if (!image ) {
          return <text>Loading</text>;
        }

        return (
          <svg
            key={image.id}
            width={baseWidth}
            height={scaledHeight}
            style={{ margin: "8px", backgroundColor: "gray" }}
            viewBox={`0 0 ${image.width} ${image.height}`}
            preserveAspectRatio="xMidYMid meet"
            xmlns="http://www.w3.org/2000/svg"
          >
            {/* The image itself */}
            <image
              width={image.width}
              height={image.height}
              preserveAspectRatio="xMidYMid meet"
              xlinkHref={cdn_url}
            />

            {/* Example: if you want to draw bounding boxes for receipts */}
            {receipts.map((receipt) => (
              console.log(`receipt: ${receipt}`),
              // <rect
              //   key={`receipt-${receipt.id}`}
              //   x={receipt.top_left.x}
              //   y={receipt.top_left.y}
              //   // width={receipt.width}
              //   // height={receipt.height}
              //   fill="rgba(255, 0, 0, 0.25)"
              //   stroke="red"
              //   strokeWidth={10}
              // />
              <></>
            ))
            }

            {/* Example: if you want to draw bounding boxes for lines */}
            {lines.map((line) => (
              <React.Fragment key={`line-${line.id}`}>
                {BoundingBox(line, image, 'gray')}
              </React.Fragment>
            ))}

            {/* A simple text label with the image ID */}
            <text
              x={10}
              y={250}
              fill="black"
              fontSize={250}
              style={{ pointerEvents: "none" }}
            >
              {image.id}
            </text>
          </svg>
        );
      })}
    </div>
  );
}