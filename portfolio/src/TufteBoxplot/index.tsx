import React from "react";
import { CHARACTERS } from "./utils";
import { ReceiptWord } from "../interfaces";

export interface IQRStats {
    min: number;
    q1: number;
    median: number;
    q3: number;
    max: number;
  }

  interface IQRMap {
    [char: string]: IQRStats;
  }

/** Compute boxplot stats (min, q1, median, q3, max) for a sorted array. */
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

/** Build an object with boxplot stats for each character. */
function computeLetterIQRMap(
  words: ReceiptWord[],
  chars: string[]
): IQRMap {
  const freqMap: Record<string, number[]> = {};
  chars.forEach((c) => (freqMap[c] = []));

  // Collect frequencies for each char from every word's histogram.
  words.forEach((w) => {
    chars.forEach((c) => {
      freqMap[c].push(w.histogram[c] ?? 0);
    });
  });

  // Compute IQR stats for each character.
  const iqrMap: IQRMap = {};
  for (const c of chars) {
    const sorted = freqMap[c].sort((a, b) => a - b);
    iqrMap[c] = computeIQR(sorted);
  }
  return iqrMap;
}

/**
 * A mini boxplot that is fully fluid.
 * - width="100%" on the <svg>
 * - viewBox in the range [0..100] so xScales become fractional.
 */
const MiniBoxPlot: React.FC<{
  stats: IQRStats;
  globalMin: number;
  globalMax: number;
  height?: number;
}> = ({ stats, globalMin, globalMax, height = 16 }) => {
  // We’ll “pretend” our logical width is 100 units:
  const leftMargin = 2;  // in that same 0..100 space
  const rightMargin = 2;
  const chartWidth = 100 - leftMargin - rightMargin; 
  const yCenter = height / 2;

  // Map a data value (in [globalMin..globalMax]) to [leftMargin..(100 - rightMargin)]
  const xScale = (val: number) => {
    if (globalMax === globalMin) {
      // Avoid divide-by-zero if everything is the same
      return leftMargin + chartWidth / 2;
    }
    return (
      leftMargin +
      ((val - globalMin) / (globalMax - globalMin)) * chartWidth
    );
  };

  const xMin = xScale(stats.min);
  const xQ1 = xScale(stats.q1);
  const xMed = xScale(stats.median);
  const xQ3 = xScale(stats.q3);
  const xMax = xScale(stats.max);

  return (
    <div style={{ width: "100%" }}>
      <svg
        width="100%"
        height={height}
        style={{ display: "block" }}
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
      >
        {/* Whiskers (min–max line) */}
        <line
          x1={xMin}
          x2={xMax}
          y1={yCenter}
          y2={yCenter}
          stroke="var(--text-color)"
          strokeWidth={1}
        />
        {/* Box (q1–q3 line) */}
        <line
          x1={xQ1}
          x2={xQ3}
          y1={yCenter}
          y2={yCenter}
          stroke="var(--text-color)"
          strokeWidth={3}
        />
        {/* Median (circle) */}
        <circle
          cx={xMed}
          cy={yCenter}
          r={2}
          fill="var(--text-color)"
        />
      </svg>
    </div>
  );
};

// TufteChartInner
const TufteChartInner: React.FC<{ words: ReceiptWord[] }> = ({ words }) => {
  // 1) Compute boxplot data for all characters
  const iqrMap = computeLetterIQRMap(words, CHARACTERS);

  // 2) Filter out characters with no data (i.e., max=0)
  const usedChars = CHARACTERS.filter((c) => iqrMap[c].max > 0);

  // 3) Determine global min/max across all used characters
  let globalMin = Infinity,
    globalMax = -Infinity;
  for (const c of usedChars) {
    const { min, max } = iqrMap[c];
    if (min < globalMin) globalMin = min;
    if (max > globalMax) globalMax = max;
  }
  if (globalMin === Infinity) {
    // Fallback if there is no data at all
    globalMin = 0;
    globalMax = 1;
  }

  return (
    <div style={{ width: "100%", height: "100%", overflowY: "auto" }}>
      {usedChars.map((char) => {
        const stats = iqrMap[char];
        return (
          <div
            key={char}
            style={{
              display: "flex",
              alignItems: "center",
            }}
          >
            {/* Left side: the character */}
            <div style={{ textAlign: "center", width: 30, fontSize: 16 }}>
              {char}
            </div>
            {/* Right side: fluid mini‐boxplot */}
            <div>
              <MiniBoxPlot
                stats={stats}
                globalMin={globalMin}
                globalMax={globalMax}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
};

const TufteBoxplot: React.FC<{ words: ReceiptWord[] }> = ({ words }) => {
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