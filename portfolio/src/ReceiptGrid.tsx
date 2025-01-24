// ReceiptGrid.tsx
import React, { useEffect, useState } from 'react';
import { fetchReceipts } from './api'; // <-- import your fetch function
import {
  ReceiptApiResponse,
  ReceiptPayload,
  ReceiptPayloadEntry,
  Receipt,
  ReceiptWord,
  Point
} from './interfaces';
import './ReceiptGrid.css'; // We'll define some CSS rules here


function scalePointByReceipt(point: Point, rcpt: Receipt) {
    return {
        x: point.x * rcpt.width,
        y: point.y * rcpt.height,
    };
}

function invert_y(point: Point) {
    return {
        x: point.x,
        y: 1-point.y,
    };
}

// Example bounding-box helper (like your "BoundingBoxReceipt" or "BoundingBoxLine"):
function boundingBoxWord(word: ReceiptWord, rcpt: Receipt, color: string) {
    const tl = scalePointByReceipt(invert_y(word.top_left), rcpt);
    const tr = scalePointByReceipt(invert_y(word.top_right), rcpt);
    const bl = scalePointByReceipt(invert_y(word.bottom_left), rcpt);
    const br = scalePointByReceipt(invert_y(word.bottom_right), rcpt);
  return (
    <React.Fragment key={`ReceiptWord-${rcpt.image_id}-${rcpt.id}#${word.line_id}-${word.id}`}>
        <line x1={tl.x} y1={tl.y} x2={tr.x} y2={tr.y} stroke={color} strokeWidth="2" />
        <line x1={tr.x} y1={tr.y} x2={br.x} y2={br.y} stroke={color} strokeWidth="2" />
        <line x1={br.x} y1={br.y} x2={bl.x} y2={bl.y} stroke={color} strokeWidth="2" />
        <line x1={bl.x} y1={bl.y} x2={tl.x} y2={tl.y} stroke={color} strokeWidth="2" />
    </React.Fragment>
  );
}

export default function ReceiptGrid() {
  const [receiptPayload, setReceiptPayload] = useState<ReceiptPayload>({});

  useEffect(() => {
    // Example: fetch all receipts (or a specific ID if your API requires it)
    fetchReceipts(6)
      .then((data: ReceiptApiResponse) => {
        setReceiptPayload(data.payload);
      })
      .catch((error) => console.error('Error fetching receipts:', error));
  }, []);

  return (
    <div className="receipt-grid-container">
      {/* 
          receiptPayload is an object with keys (receipt IDs) -> {receipt, words}.
          We map over each entry to render an <svg> or other container.
       */}
      {Object.entries(receiptPayload).map(([receiptId, payloadEntry]) => {
        // Each payload entry can have .receipt and .words
        const { receipt, words } = payloadEntry as ReceiptPayloadEntry;

        // If no receipt or words, we can skip or display differently
        if (!receipt || !words) return null;

        const isDevelopment = process.env.NODE_ENV === 'development';
        const cdnUrl = isDevelopment
        ? `https://dev.tylernorlund.com/${receipt.cdn_s3_key}`
        : `https://www.tylernorlund.com/${receipt.cdn_s3_key}`;

        return (
            <div key={`Image-${receipt.image_id}-Receipt-${receipt.id}`} >
          <svg
            className="receipt-svg"
            viewBox={`0 0 ${receipt.width} ${receipt.height}`}
            preserveAspectRatio="xMidYMid meet"
            xmlns="http://www.w3.org/2000/svg"
          >
            {/* Optionally render the actual receipt image, if available: */}
            <image
              width={receipt.width}
              height={receipt.height}
              xlinkHref={cdnUrl} // or { cdnUrl }
            />

            {/* Draw bounding boxes for each word */}
            {words.map((word: ReceiptWord) =>
              boundingBoxWord(word, receipt, 'blue')
            )}
          </svg>
          </div>
        );
      })}
    </div>
  );
}