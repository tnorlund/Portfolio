import React from "react";
import { ReceiptWord } from "./interfaces";
import WordsSvgInner from "./WordsSvgInner";

interface Props {
  words: ReceiptWord[];
}

const WordsSvgContainer: React.FC<Props> = ({ words }) => {
  // Just style the container to fill any flex parent or set a minHeight
  return (
    <div
      style={{
        width: "100%",
        height: "100%",     // fill available height
        minHeight: "300px", // keep from collapsing
        position: "relative",
      }}
    >
      {/* No measuring. Just render the inner component. */}
      <WordsSvgInner words={words} />
    </div>
  );
};

export default WordsSvgContainer;