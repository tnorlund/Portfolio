/**
 * TufteBoxplot
 *
 * This file provides a fully fluid, minimalistic boxplot visualization
 * for a list of words and their character-frequency histograms.
 *
 * Major components:
 * 1. **IQRStats**: Interface describing min, q1, median, q3, and max.
 * 2. **computeIQR**: Utility to compute boxplot stats from sorted data.
 * 3. **computeLetterIQRMap**: Aggregates frequencies for each character,
 *    then computes stats for each character.
 * 4. **MiniBoxPlot**: Renders a boxplot for a single character.
 * 5. **BoxplotList**: Renders a list of MiniBoxPlot components, one for each character.
 * 6. **TufteBoxplot** (default export): The main component that wraps it all.
 */

import React from "react";
import { CHARACTERS } from "./utils";
import { ReceiptWord } from "../interfaces";

/** 
 * Boxplot statistics for a single character:
 * - min, q1, median, q3, max
 */
export interface IQRStats {
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
}

/**
 * Maps each character (string) to its IQRStats.
 * Example: { "a": { min: 0, q1: 1, ... }, "b": { ... }, ... }
 */
interface IQRMap {
  [char: string]: IQRStats;
}

/**
 * Compute boxplot stats (min, q1, median, q3, max) for an **already sorted** array of numbers.
 * If the array is empty, return zeros.
 */
function computeIQR(sorted: number[]): IQRStats {
  if (sorted.length === 0) {
    return { min: 0, q1: 0, median: 0, q3: 0, max: 0 };
  }

  /**
   * A helper to interpolate a percentile in a sorted array.
   * @param arr The sorted array
   * @param p   Percentile (0..1)
   */
  const getPercentile = (arr: number[], p: number) => {
    // Special case: if only one value, always return it
    if (arr.length === 1) return arr[0];

    // Position in array
    const idx = (arr.length - 1) * p;
    const lower = Math.floor(idx);
    const upper = Math.ceil(idx);

    // If the index is an integer, return that exact value
    if (lower === upper) return arr[lower];

    // Otherwise, interpolate fractionally
    const fraction = idx - lower;
    return arr[lower] + (arr[upper] - arr[lower]) * fraction;
  };

  return {
    min: sorted[0],
    q1: getPercentile(sorted, 0.25),
    median: getPercentile(sorted, 0.5),
    q3: getPercentile(sorted, 0.75),
    max: sorted[sorted.length - 1],
  };
}

/**
 * Build an object (IQRMap) with boxplot stats for each character.
 * 1. Initialize a frequency map (freqMap) for each character.
 * 2. Fill freqMap with character frequencies from each word's histogram.
 * 3. Sort and compute the boxplot stats for each character array.
 */
function computeLetterIQRMap(words: ReceiptWord[], chars: string[]): IQRMap {
  // freqMap holds an array of frequencies per character
  const freqMap: Record<string, number[]> = {};
  chars.forEach((c) => (freqMap[c] = []));

  // Collect frequencies for each char from every word's histogram
  words.forEach((w) => {
    chars.forEach((c) => {
      // Default to 0 if a character is missing
      freqMap[c].push((w.histogram?.[c] ?? 0));
    });
  });

  // Compute IQR stats for each character
  const iqrMap: IQRMap = {};
  chars.forEach((c) => {
    const sortedFreqs = freqMap[c].sort((a, b) => a - b);
    iqrMap[c] = computeIQR(sortedFreqs);
  });

  return iqrMap;
}

/**
 * A tiny, horizontally fluid boxplot for a single character.
 *
 * Props:
 * - stats      : The boxplot stats (min, q1, median, q3, max) for the character.
 * - globalMin  : Minimum frequency across all used characters (for consistent scaling).
 * - globalMax  : Maximum frequency across all used characters (for consistent scaling).
 * - height     : SVG height in pixels (default = 16).
 */
const MiniBoxPlot: React.FC<{
  stats: IQRStats;
  globalMin: number;
  globalMax: number;
  height?: number;
}> = ({ stats, globalMin, globalMax, height = 16 }) => {
  const leftMargin = 2;
  const rightMargin = 2;
  const chartWidth = 100 - leftMargin - rightMargin;
  const yCenter = height / 2;

  const xScale = (val: number) => {
    if (globalMax === globalMin) {
      return leftMargin + chartWidth / 2;
    }
    return leftMargin + ((val - globalMin) / (globalMax - globalMin)) * chartWidth;
  };

  // Precompute scaled X positions
  const xMin = xScale(stats.min);
  const xQ1 = xScale(stats.q1);
  const xMed = xScale(stats.median);
  const xQ3 = xScale(stats.q3);
  const xMax = xScale(stats.max);

  return (
    <div style={{ width: "100%" }}>
      <svg
        width="100%"
        height={height + 4}
        viewBox={`-1 -1 102 ${height + 2}`}
        preserveAspectRatio="none"
        style={{ display: "block" }}
      >
        {/* Whiskers (min–max line) */}
        <line
          x1={xMin}
          x2={xMax}
          y1={yCenter}
          y2={yCenter}
          stroke="var(--text-color)"
          strokeWidth={2}
        />
        {/* Box (q1–q3 line) */}
        <line
          x1={xQ1}
          x2={xQ3}
          y1={yCenter}
          y2={yCenter}
          stroke="var(--text-color)"
          strokeWidth={2}
        />
        {/* Median (circle at the median) */}
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

/**
 * Renders a vertical list of mini box-plots, one for each used character.
 * - "Used" characters are those that actually appear at least once (max > 0).
 */
const BoxplotList: React.FC<{ words: ReceiptWord[] }> = ({ words }) => {
  // 1) Compute IQR stats for all characters
  const iqrMap = computeLetterIQRMap(words, CHARACTERS);

  // 2) Filter out characters with no data (max=0)
  const usedChars = CHARACTERS.filter((c) => iqrMap[c].max > 0);

  // 3) Find global min/max across used characters for consistent scaling
  let globalMin = Infinity;
  let globalMax = -Infinity;

  usedChars.forEach((char) => {
    const { min, max } = iqrMap[char];
    if (min < globalMin) globalMin = min;
    if (max > globalMax) globalMax = max;
  });

  // If no data at all, provide a fallback
  if (globalMin === Infinity) {
    globalMin = 0;
    globalMax = 1;
  }

  return (
    <div style={{ width: "100%", height: "100%", overflowY: "auto" }}>
      {usedChars.map((char) => {
        const stats = iqrMap[char];
        return (
          <div key={char} style={{ display: "flex", alignItems: "center" }}>
            {/* Left side: the character label */}
            <div style={{ textAlign: "center", width: 30, fontSize: 16 }}>
              {char}
            </div>
            {/* Right side: fluid mini boxplot */}
            <div style={{ flex: 1 }}>
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

/**
 * Main TufteBoxplot component.
 * 
 * Wraps the BoxplotList inside a container that can size appropriately
 * (e.g., fill half the screen if used in a flex layout).
 */
const TufteBoxplot: React.FC<{ words: ReceiptWord[] }> = ({ words }) => {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        minHeight: "300px",
        position: "relative", // allows for any absolutely-positioned elements
      }}
    >
      {/* Renders the list of mini box-plots for each used character */}
      <BoxplotList words={words} />
    </div>
  );
};

export default TufteBoxplot;