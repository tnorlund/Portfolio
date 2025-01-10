import React from "react";

interface ImageItem {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  s3_bucket: string;
  s3_key: string;
}

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

function BoundingBox(line: LineItem, img: ImageItem) {
  const x = line.x * img.width;
  const y = line.y * img.height;
  const flippedY = img.height - y;
  // Box width & height in image pixels:
  const boxW = line.width * img.width;
  const boxH = line.height * img.height;
  const angleRad = -(Math.PI / 180) * line.angle;
  const bottomLeft = { x, y: flippedY };

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
  return (
    <React.Fragment>
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
}

export default BoundingBox;
