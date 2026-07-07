import { useMemo } from "react";
import type { GlyphSource } from "../types";
import { strokePathD } from "../geometry/bezier";

// Small SVG rendering of a glyph's stroke skeleton, cap-unit space, y-up.
export function GlyphThumb({
  glyph,
  dot,
  size = 44,
  ink = "#eee",
}: {
  glyph: GlyphSource | undefined;
  dot: number;
  size?: number;
  ink?: string;
}) {
  const d = useMemo(
    () => (glyph ? glyph.strokes.map(strokePathD).join(" ") : ""),
    [glyph],
  );
  // viewBox spans a little beyond baseline/cap for descenders/ascenders.
  const vbX = -120;
  const vbY = -400;
  const vbW = 1240;
  const vbH = 1560;
  return (
    <svg
      width={size}
      height={size * (vbH / vbW)}
      viewBox={`${vbX} ${vbY} ${vbW} ${vbH}`}
      style={{ display: "block" }}
    >
      <g transform="scale(1,-1)">
        {/* baseline + cap guides */}
        <line x1={vbX} y1={0} x2={vbX + vbW} y2={0} stroke="#333" strokeWidth={6} />
        <line x1={vbX} y1={1000} x2={vbX + vbW} y2={1000} stroke="#2a2a2a" strokeWidth={4} />
        {glyph && (
          <path
            d={d}
            fill="none"
            stroke={ink}
            strokeWidth={dot}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )}
      </g>
    </svg>
  );
}
