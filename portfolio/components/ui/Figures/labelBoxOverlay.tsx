import React from "react";
import {
  CHARGE_GREEN,
  ENTITY_DISPLAY_NAMES,
  LABEL_COLORS,
} from "./labelStyles";
import styles from "./labelBoxOverlay.module.css";

const LEGEND_GROUPS = [
  {
    color: "var(--color-yellow)",
    label: "Merchant",
    title: "Merchant name · Business name · Loyalty ID",
    types: ["MERCHANT_NAME", "BUSINESS_NAME", "LOYALTY_ID"],
  },
  {
    color: "var(--color-blue)",
    label: "Date / Time",
    title: "Date · Time",
    types: ["DATE", "TIME"],
  },
  {
    color: CHARGE_GREEN,
    label: "Charges",
    title: "Total · Subtotal · Tax · Line total · Unit price",
    types: [
      "GRAND_TOTAL",
      "SUBTOTAL",
      "TAX",
      "LINE_TOTAL",
      "UNIT_PRICE",
      "AMOUNT",
    ],
  },
  {
    color: "var(--color-teal)",
    label: "Credits",
    title: "Discount · Coupon · Tip · Change · Cash back · Refund",
    types: ["DISCOUNT", "COUPON", "TIP", "CHANGE", "CASH_BACK", "REFUND"],
  },
  {
    color: "var(--color-cyan)",
    label: "Quantity",
    title: "Quantity",
    types: ["QUANTITY"],
  },
  {
    color: "var(--color-red)",
    label: "Address",
    title: "Address line",
    types: ["ADDRESS", "ADDRESS_LINE"],
  },
  {
    color: "var(--color-pink)",
    label: "Phone",
    title: "Phone number",
    types: ["PHONE_NUMBER"],
  },
  {
    color: "var(--color-purple)",
    label: "Product",
    title: "Product name",
    types: ["PRODUCT_NAME"],
  },
  {
    color: "var(--color-purple)",
    label: "Website",
    title: "Website",
    types: ["WEBSITE"],
  },
  {
    color: "var(--color-orange)",
    label: "Hours / Pay",
    title: "Store hours · Payment method",
    types: ["STORE_HOURS", "PAYMENT_METHOD"],
  },
] as const;

const groupedFamilies: Set<string> = new Set(
  LEGEND_GROUPS.flatMap((group) => [...group.types]),
);

const fallbackLabel = (family: string): string =>
  ENTITY_DISPLAY_NAMES[family] ??
  family
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (character) => character.toUpperCase());

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
 * The label legend, matching LayoutLMBatchVisualization's compact taxonomy
 * groups and dot/label styling so both figures read the same without letting a
 * granular receipt-specific label list outgrow the fixed carousel stage.
 */
export const LabelLegend: React.FC<{
  families: string[];
  className?: string;
}> = ({ families, className }) => {
  const presentFamilies = new Set(families);
  const visibleGroups = LEGEND_GROUPS.filter((group) =>
    group.types.some((type) => presentFamilies.has(type)),
  );
  const unmatchedFamilies = Array.from(presentFamilies).filter(
    (family) => family !== "O" && !groupedFamilies.has(family),
  );

  return (
    <div
      className={className ? `${styles.legend} ${className}` : styles.legend}
      aria-label="Label families"
    >
      {visibleGroups.map((group) => (
        <div key={group.label} title={group.title} className={styles.legendItem}>
          <div
            className={styles.legendDot}
            style={{ backgroundColor: group.color }}
          />
          <span className={styles.legendLabel}>{group.label}</span>
        </div>
      ))}
      {unmatchedFamilies.map((family) => (
        <div key={family} title={family} className={styles.legendItem}>
          <div
            className={styles.legendDot}
            style={{
              backgroundColor: LABEL_COLORS[family] || LABEL_COLORS.O,
            }}
          />
          <span className={styles.legendLabel}>{fallbackLabel(family)}</span>
        </div>
      ))}
    </div>
  );
};
