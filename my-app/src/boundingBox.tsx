import React from "react";

interface ImageItem {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  s3_bucket: string;
  s3_key: string;
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

function BoundingBox(line: LineItem, img: ImageItem) {

    const bottomLeft = scalePointByImage(invert_y(line.bottom_left), img);
  // Bottom-right corner
  const bottomRight = scalePointByImage(invert_y(line.bottom_right), img);

  // Top-left corner
  const topLeft = scalePointByImage(invert_y(line.top_left), img);

  // Top-right corner
  const topRight = scalePointByImage(invert_y(line.top_right), img);
  return (
    <React.Fragment
        key={line.id}
    >
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
