import React from "react";
import { ENTITY_DISPLAY_NAMES, LABEL_COLORS } from "./labelStyles";
import styles from "./labelBoxOverlay.module.css";

/**
 * The ground-truth label box layer, extracted verbatim from
 * LayoutLMBatchVisualization so both figures render pixel-identical boxes and
 * can never drift: an SVG in the receipt's own PIXEL coordinate space
 * (preserveAspectRatio="none") with one plain <rect> per word —
 * fill=color, fillOpacity 0.3, stroke=color, strokeWidth 2, and nothing else
 * (strokes scale with the viewBox; no vectorEffect, no rounded corners, no
 * animation). Colors come from LABEL_COLORS with the O fallback.
 */
export interface OverlayBox {
  key: string | number;
  /** Pixel rect in the receipt's own coordinate space. */
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  /** Optional hooks (SynthesisPipeline test/legend); omitted for LayoutLM. */
  testId?: string;
  family?: string;
}

export const LabelBoxOverlay: React.FC<{
  width: number;
  height: number;
  boxes: OverlayBox[];
  className?: string;
}> = ({ width, height, boxes, className }) => (
  <svg
    className={className}
    viewBox={`0 0 ${width} ${height}`}
    preserveAspectRatio="none"
  >
    {boxes.map((b) => (
      <rect
        key={b.key}
        x={b.x}
        y={b.y}
        width={b.width}
        height={b.height}
        fill={b.color}
        fillOpacity={0.3}
        stroke={b.color}
        strokeWidth={2}
        data-testid={b.testId}
        data-family={b.family}
      />
    ))}
  </svg>
);

/**
 * The label legend, matching LayoutLMBatchVisualization's legend markup/classes
 * (a colored dot + display name per family) so both figures read the same.
 */
export const LabelLegend: React.FC<{
  families: string[];
  className?: string;
}> = ({ families, className }) => (
  <div
    className={className ? `${styles.legend} ${className}` : styles.legend}
    aria-label="Label families"
  >
    {families.map((family) => (
      <div key={family} className={styles.legendItem}>
        <div
          className={styles.legendDot}
          style={{ backgroundColor: LABEL_COLORS[family] || LABEL_COLORS.O }}
        />
        <span className={styles.legendLabel}>
          {ENTITY_DISPLAY_NAMES[family] ?? family}
        </span>
      </div>
    ))}
  </div>
);
