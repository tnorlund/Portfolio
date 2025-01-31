import React from "react";
import { ReceiptWord } from "./interfaces";

interface Props {
  words: ReceiptWord[];
}

const WordsSvgInner: React.FC<Props> = ({ words }) => {
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
        // "overflow: hidden" ensures we clip any shapes that extend beyond the viewBox
        overflow: "hidden",
      }}
    >
      {words.map((word, idx) => {
        const { top_left, top_right, bottom_right, bottom_left } = word;

        // If (0,0) is the bottom-left in your data and you want (0,0) at the top-left visually,
        // invert y-coords by using (1 - y). 
        // If your data already has y=0 at top, you might skip this inversion.
        const points = [
          `${top_left.x},${1 - top_left.y}`,
          `${top_right.x},${1 - top_right.y}`,
          `${bottom_right.x},${1 - bottom_right.y}`,
          `${bottom_left.x},${1 - bottom_left.y}`,
        ].join(" ");

        return (
          <polygon
            key={idx}
            points={points}
            fill="none"
            stroke="var(--text-color)"
            strokeWidth="1px"
            // "vectorEffect" prevents the stroke width from scaling if you do want that:
            // remove or comment out if you'd like the stroke to scale with the SVG
            vectorEffect="non-scaling-stroke"
          />
        );
      })}
    </svg>
  );
};

export default WordsSvgInner;