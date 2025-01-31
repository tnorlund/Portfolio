import React from "react";
import { ReceiptWord } from "./interfaces";

/** Characters you care about */
const CHARACTERS = [
  " ",
  "!",
  '"',
  "#",
  "$",
  "%",
  "&",
  "'",
  "(",
  ")",
  "*",
  "+",
  ",",
  "-",
  ".",
  "/",
  "0",
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "7",
  "8",
  "9",
  ":",
  ";",
  "<",
  "=",
  ">",
  "?",
  "@",
  "A",
  "B",
  "C",
  "D",
  "E",
  "F",
  "G",
  "H",
  "I",
  "J",
  "K",
  "L",
  "M",
  "N",
  "O",
  "P",
  "Q",
  "R",
  "S",
  "T",
  "U",
  "V",
  "W",
  "X",
  "Y",
  "Z",
  "[",
  "\\",
  "]",
  "_",
  "a",
  "b",
  "c",
  "d",
  "e",
  "f",
  "g",
  "h",
  "i",
  "j",
  "k",
  "l",
  "m",
  "n",
  "o",
  "p",
  "q",
  "r",
  "s",
  "t",
  "u",
  "v",
  "w",
  "x",
  "y",
  "z",
  "{",
  "}",
  "|",
  "~",
];

interface IQRStats {
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
}

interface IQRMap {
  [char: string]: IQRStats;
}

function isNarrowScreen(): boolean {
  // If this runs in a browser, we can do:
  return typeof window !== "undefined" && window.innerWidth < 768;
  // For SSR or advanced usage, you'd do something more robust (e.g. a media query hook).
}
/** Compute basic boxplot stats for a sorted array of frequencies in [0..1]. */
function computeIQR(sorted: number[]): IQRStats {
  if (!sorted.length) {
    return { min: 0, q1: 0, median: 0, q3: 0, max: 0 };
  }

  const getPercentile = (arr: number[], p: number) => {
    if (arr.length === 1) return arr[0];
    const idx = (arr.length - 1) * p;
    const lower = Math.floor(idx);
    const upper = Math.ceil(idx);
    if (lower === upper) return arr[lower];
    const fraction = idx - lower;
    return arr[lower] + (arr[upper] - arr[lower]) * fraction;
  };

  return {
    min: sorted[0],
    max: sorted[sorted.length - 1],
    q1: getPercentile(sorted, 0.25),
    median: getPercentile(sorted, 0.5),
    q3: getPercentile(sorted, 0.75),
  };
}

function computeLetterIQRMap(words: ReceiptWord[], chars: string[]): IQRMap {
  const freqMap: Record<string, number[]> = {};
  chars.forEach((c) => (freqMap[c] = []));

  words.forEach((w) => {
    chars.forEach((c) => {
      freqMap[c].push(w.histogram[c] ?? 0);
    });
  });

  const iqrMap: IQRMap = {};
  for (const c of chars) {
    const sorted = freqMap[c].sort((a, b) => a - b);
    iqrMap[c] = computeIQR(sorted);
  }
  return iqrMap;
}

const TufteChartInner: React.FC<{ words: ReceiptWord[] }> = ({ words }) => {
  // Decide rowHeight & fontSize based on viewport width
  const narrow = isNarrowScreen();
  const finalRowHeight = narrow ? 60 : 35;
  const finalFontSize = narrow ? 14 : 16;

  // 1) Compute data
  const iqrMap = computeLetterIQRMap(words, CHARACTERS);

  // 2) Filter out any characters that have no data
  const usedChars = CHARACTERS.filter((c) => iqrMap[c].max > 0);

  // 3) Find global min/max for the x-scale
  let globalMin = Infinity,
    globalMax = -Infinity;
  for (const c of usedChars) {
    if (iqrMap[c].min < globalMin) globalMin = iqrMap[c].min;
    if (iqrMap[c].max > globalMax) globalMax = iqrMap[c].max;
  }
  if (globalMin === Infinity) {
    globalMin = 0;
    globalMax = 1;
  }

  // 4) "Logical" SVG size
  const viewBoxWidth = 600;
  const totalHeight = usedChars.length * finalRowHeight + 20; // note rowHeight is dynamic
  const viewBoxHeight = totalHeight;

  // Margins & chart width
  const leftMargin = narrow ? 70 : 37;
  const rightMargin = 10;
  const chartWidth = viewBoxWidth - leftMargin - rightMargin;

  const xScale = (val: number) =>
    leftMargin + ((val - globalMin) / (globalMax - globalMin)) * chartWidth;

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      {/* The SVG (lines, boxes) that scales */}
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ backgroundColor: "none" }}
      >
        {usedChars.map((char, i) => {
          const stats = iqrMap[char];
          const yTop = 10 + i * finalRowHeight;
          const yCenter = yTop + finalRowHeight / 2;

          const xMin = xScale(stats.min);
          const xQ1 = xScale(stats.q1);
          const xMed = xScale(stats.median);
          const xQ3 = xScale(stats.q3);
          const xMax = xScale(stats.max);

          return (
            <g key={char}>
              {/* Whisker line */}
              <line
                x1={xMin}
                x2={xMax}
                y1={yCenter}
                y2={yCenter}
                stroke="var(--text-color)"
                strokeWidth={1}
              />

              {/* IQR line */}
              <line
                x1={xQ1}
                x2={xQ3}
                y1={yCenter}
                y2={yCenter}
                stroke="var(--text-color)"
                strokeWidth={3}
              />

              {/* Median dot */}
              <circle cx={xMed} cy={yCenter} r={3} fill="var(--text-color)" />
            </g>
          );
        })}
      </svg>

      {/* Absolutely positioned text labels (fixed px size) */}
      {usedChars.map((char, i) => {
        const yTop = 10 + i * finalRowHeight;
        const yCenter = yTop + finalRowHeight / 2;
        const xLabel = 10;

        // Convert chart coordinates -> percentages for left/top
        const xPercent = (xLabel / viewBoxWidth) * 100;
        const yPercent = (yCenter / viewBoxHeight) * 100;

        return (
          <div
            key={char}
            style={{
              position: "absolute",
              left: `${xPercent}%`,
              top: `${yPercent}%`,
              transform: "translate(0, -50%)",
              fontSize: `${finalFontSize}px`, // e.g. 16 or 12
              color: "var(--text-color)",
              pointerEvents: "none",
            }}
          >
            {char}
          </div>
        );
      })}
    </div>
  );
};
export default TufteChartInner;
