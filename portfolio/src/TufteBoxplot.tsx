import React from "react";
import TufteChartInner from "./TufteChartInner";
import { ReceiptWord } from "./interfaces";

interface TufteBoxplotProps {
  words: ReceiptWord[];
}

const TufteBoxplot: React.FC<TufteBoxplotProps> = ({ words }) => {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        minHeight: "300px",
        position: "relative", // <-- needed for absolutely-positioned text overlay
      }}
    >
      {/* TufteChartInner will render the SVG plus text overlay. */}
      <TufteChartInner words={words} />
    </div>
  );
};

export default TufteBoxplot;