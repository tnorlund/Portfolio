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
 * Utility Functions
 * --------------------------------------------------
 */

/**
 * Computes the SVG polygon "points" string for a given ReceiptWord's corners.
 * Assumes that (0,0) in the data space corresponds to the bottom-left,
 * so we invert the Y coordinates by using (1 - y).
 * If your data already has y=0 at the top, you can remove the `1 - y` logic.
 */
function getPolygonPoints(word: ReceiptWord): string {
  const { top_left, top_right, bottom_right, bottom_left } = word;
  const pointsArray = [
    `${top_left.x},${1 - top_left.y}`,
    `${top_right.x},${1 - top_right.y}`,
    `${bottom_right.x},${1 - bottom_right.y}`,
    `${bottom_left.x},${1 - bottom_left.y}`,
  ];
  return pointsArray.join(" ");
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
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
      style={{
        border: "1px solid var(--text-color)",
        background: "var(--background-color)",
        borderRadius: "6px",
        // "overflow: hidden" ensures shapes beyond the viewBox are clipped
        overflow: "hidden",
      }}
    >
      {words.map((word, idx) => {
        const points = getPolygonPoints(word);
        return (
          <polygon
            key={idx}
            points={points}
            fill="none"
            stroke="var(--text-color)"
            strokeWidth="1px"
            // vectorEffect="non-scaling-stroke" prevents stroke width from scaling.
            // Remove or comment out if you'd like the stroke to scale with the SVG.
            vectorEffect="non-scaling-stroke"
          />
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
        height: "100%",     // Fill any available height
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