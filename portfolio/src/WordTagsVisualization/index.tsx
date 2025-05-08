import React from "react";
import { ReceiptWord } from "../interfaces";

/**
 * --------------------------------------------------
 * Types and Interfaces
 * --------------------------------------------------
 */

/**
 * Props for components in this file.
 * Expects an array of ReceiptWord objects.
 */
interface Props {
  words: ReceiptWord[];
}

/**
 * --------------------------------------------------
 * Presentational Components
 * --------------------------------------------------
 */

/**
 * Renders an SVG that draws each ReceiptWord as a polygon.
 * The viewBox is set to "0 0 1 1" for a normalized coordinate system,
 * and we preserve aspect ratio by using "none" (i.e., fully stretch).
 */
const WordsPolygonSvg: React.FC<Props> = ({ words }) => {
  return (
    <svg
      width="100%"
      height="100%"
      viewBox="-0.01 -0.01 1.02 1.02"
      preserveAspectRatio="none"
      shapeRendering="geometricPrecision"
      style={{
        border: "2px solid var(--text-color)",
        background: "var(--background-color)",
        borderRadius: "6px",
        overflow: "hidden",
      }}
    >
      {words.map((word, idx) => {
        const { top_left, top_right, bottom_right, bottom_left } = word;
        return (
          <g key={idx}>
            {/* Top line */}
            <line
              x1={top_left.x}
              y1={1 - top_left.y}
              x2={top_right.x}
              y2={1 - top_right.y}
              stroke="var(--text-color)"
              strokeWidth="1"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
            {/* Right line */}
            <line
              x1={top_right.x}
              y1={1 - top_right.y}
              x2={bottom_right.x}
              y2={1 - bottom_right.y}
              stroke="var(--text-color)"
              strokeWidth="1"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
            {/* Bottom line */}
            <line
              x1={bottom_right.x}
              y1={1 - bottom_right.y}
              x2={bottom_left.x}
              y2={1 - bottom_left.y}
              stroke="var(--text-color)"
              strokeWidth="1"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
            {/* Left line */}
            <line
              x1={bottom_left.x}
              y1={1 - bottom_left.y}
              x2={top_left.x}
              y2={1 - top_left.y}
              stroke="var(--text-color)"
              strokeWidth="1"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          </g>
        );
      })}
    </svg>
  );
};

/**
 * A container component that sizes the SVG to fill its parent,
 * with a minimum height to prevent the container from collapsing.
 * It simply wraps the WordsPolygonSvg in a resizable div.
 */
const WordsSvgContainer: React.FC<Props> = ({ words }) => {
  return (
    <div
      style={{
        width: "100%",
        height: "100%", // Fill any available height
        minHeight: "300px", // Prevent collapse when there's little content
        position: "relative",
      }}
    >
      {/* Render the polygonal visualization inside this container */}
      <WordsPolygonSvg words={words} />
    </div>
  );
};

export default WordsSvgContainer;
