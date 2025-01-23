import React from "react";

interface Receipt {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  raw_s3_bucket: string;
  raw_s3_key: string;
  // And so on ...
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  // Possibly more fields...
}

interface ImageItem {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  raw_s3_bucket: string;
  raw_s3_key: string;
}

interface BoundingBoxInterface {
    x: number;
    y: number;
    width: number;
    height: number;
}

interface Point {
    x: number;
    y: number;
}

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

function scalePointByImage(point: Point, img: ImageItem) {
    return {
        x: point.x * img.width,
        y: point.y * img.height,
    };
}

function invert_y(point: Point) {
    return {
        x: point.x,
        y: 1-point.y,
    };
}

export function BoundingBoxLine(line: LineItem, img: ImageItem, color: string) {
  const bottomLeft = scalePointByImage(invert_y(line.bottom_left), img);
  const bottomRight = scalePointByImage(invert_y(line.bottom_right), img);
  const topLeft = scalePointByImage(invert_y(line.top_left), img);
  const topRight = scalePointByImage(invert_y(line.top_right), img);

  return (
    <React.Fragment key={`line-${line.id}`}>
      {/* Diagonals (if you want them) */}
      <line
        x1={bottomLeft.x}
        y1={bottomLeft.y}
        x2={topRight.x}
        y2={topRight.y}
        stroke={color}
        strokeWidth={1}
        opacity={line.confidence}
      />
      <line
        x1={topLeft.x}
        y1={topLeft.y}
        x2={bottomRight.x}
        y2={bottomRight.y}
        stroke={color}
        strokeWidth={1}
        opacity={line.confidence}
      />
      {/* Horizontal edges */}
      <line
        x1={topLeft.x}
        y1={topLeft.y}
        x2={topRight.x}
        y2={topRight.y}
        stroke={color}
        strokeWidth={3}
        opacity={line.confidence}
      />
      <line
        x1={bottomLeft.x}
        y1={bottomLeft.y}
        x2={bottomRight.x}
        y2={bottomRight.y}
        stroke={color}
        strokeWidth={3}
        opacity={line.confidence}
      />
      {/* Vertical edges */}
      <line
        x1={topLeft.x}
        y1={topLeft.y}
        x2={bottomLeft.x}
        y2={bottomLeft.y}
        stroke={color}
        strokeWidth={3}
        opacity={line.confidence}
      />
      <line
        x1={topRight.x}
        y1={topRight.y}
        x2={bottomRight.x}
        y2={bottomRight.y}
        stroke={color}
        strokeWidth={3}
        opacity={line.confidence}
      />
    </React.Fragment>
  );
}

export function BoundingBoxReceipt(receipt: Receipt, image: ImageItem,  color: string) {
  const bl = scalePointByImage(invert_y(receipt.bottom_left), image);
  const br = scalePointByImage(invert_y(receipt.bottom_right), image);
  const tl = scalePointByImage(invert_y(receipt.top_left), image);
  const tr = scalePointByImage(invert_y(receipt.top_right), image);

  return (
    <React.Fragment key={`receipt-${receipt.id}`}>
      {/* Draw edges of the rectangle in a similar style */}
      <circle cx={tl.x} cy={tl.y} r={20} fill={color} />
      <line
        x1={tl.x}
        y1={tl.y}
        x2={tr.x}
        y2={tr.y}
        stroke={color}
        strokeWidth={3}
      />
      <line
        x1={tr.x}
        y1={tr.y}
        x2={br.x}
        y2={br.y}
        stroke={color}
        strokeWidth={3}
      />
      <line
        x1={br.x}
        y1={br.y}
        x2={bl.x}
        y2={bl.y}
        stroke={color}
        strokeWidth={3}
      />
      <line
        x1={bl.x}
        y1={bl.y}
        x2={tl.x}
        y2={tl.y}
        stroke={color}
        strokeWidth={3}
      />
    </React.Fragment>
  );
}

