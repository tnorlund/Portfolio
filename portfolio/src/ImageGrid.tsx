import React, { useEffect, useState } from 'react';
import { fetchImages } from './api';
import { ImageReceiptsLines, Receipt, LineItem } from './interfaces';
import {BoundingBoxLine, BoundingBoxReceipt} from './boundingBox';

export default function ImageGrid() {
  const [imageReceiptLines, setImageReceiptLines] = useState<ImageReceiptsLines[]>([]);

  useEffect(() => {
    fetchImages()
      .then((imageItems) => setImageReceiptLines(imageItems))
      .catch((error) => console.error('Error fetching images:', error));
  }, []);

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap' }}>
      {imageReceiptLines.map(([image, receipts, lines]) => {
        const isDevelopment = process.env.NODE_ENV === 'development';
        const cdnUrl = isDevelopment
        ? `https://dev.tylernorlund.com/${image.cdn_s3_key}`
        : `https://www.tylernorlund.com/${image.cdn_s3_key}`;
        const baseWidth = 500;
        const aspectRatio = image.height / image.width;
        const scaledHeight = baseWidth * aspectRatio;

        return (
          <svg
            key={image.id}
            width={baseWidth}
            height={scaledHeight}
            style={{ margin: '8px', backgroundColor: 'gray' }}
            viewBox={`0 0 ${image.width} ${image.height}`}
            preserveAspectRatio="xMidYMid meet"
            xmlns="http://www.w3.org/2000/svg"
          >
            {/* Render the image */}
            <image
              width={image.width}
              height={image.height}
              preserveAspectRatio="xMidYMid meet"
              xlinkHref={cdnUrl}
            />

            {/* Render receipts as bounding boxes */}
            {receipts.map((receipt: Receipt) => (
              BoundingBoxReceipt(receipt, image, 'red')
            ))}

            {/* Render text lines as bounding boxes */}
            {lines.map((line: LineItem) => (
              BoundingBoxLine(line, image, 'blue')
            ))}
          </svg>
        );
      })}
    </div>
  );
}