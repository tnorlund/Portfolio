import React, { useMemo } from "react";
import {
  getBestImageUrl,
  getJpegFallbackUrl,
} from "../../../utils/imageFormat";
import {
  boundsForLineIds,
  lineRectsForLineIds,
} from "../../ui/Figures/GeometricReader/geometry";
import { ImageFormatSupport } from "../../ui/Figures/ReceiptFlow/types";
import styles from "./Validation.module.css";
import { ValidationReceipt } from "./types";

// Muted palette: the image is the evidence, the overlay is annotation.
const SECTION_COLORS: Record<string, string> = {
  ITEMS: "var(--color-yellow)",
  ITEMS_VALUE: "var(--color-yellow)",
  ITEMS_DESCRIPTION: "var(--color-yellow)",
  SECTION_HEADER: "var(--color-yellow)",
  SUMMARY: "var(--color-green)",
  TOTAL_LINE: "var(--color-green)",
  PAYMENT: "var(--color-purple)",
};

const sectionColor = (sectionType: string): string =>
  SECTION_COLORS[sectionType] ??
  "color-mix(in srgb, var(--text-color) 35%, transparent)";

interface ReceiptCanvasProps {
  receipt: ValidationReceipt;
  formatSupport: ImageFormatSupport | null;
  highlightLineIds: number[] | null;
  showSections: boolean;
  showItems: boolean;
}

export const ReceiptCanvas: React.FC<ReceiptCanvasProps> = ({
  receipt,
  formatSupport,
  highlightLineIds,
  showSections,
  showItems,
}) => {
  const { width, height } = receipt.image;
  const imageUrl = useMemo(
    () =>
      formatSupport ? getBestImageUrl(receipt.image, formatSupport) : null,
    [receipt.image, formatSupport],
  );

  const highlight = useMemo(
    () =>
      highlightLineIds && highlightLineIds.length > 0
        ? boundsForLineIds(receipt.lines, highlightLineIds)
        : null,
    [receipt.lines, highlightLineIds],
  );

  return (
    <div className={styles.canvas} data-testid="receipt-canvas">
      <div className={styles.canvasInner}>
        {imageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={imageUrl}
            alt={`${receipt.merchant_name} receipt ${receipt.receipt_id}`}
            width={width}
            height={height}
            className={styles.canvasImage}
            onError={(event) => {
              const fallback = getJpegFallbackUrl(receipt.image);
              if (event.currentTarget.src !== fallback) {
                event.currentTarget.src = fallback;
              }
            }}
          />
        ) : (
          <div className={styles.canvasLoading}>Loading image…</div>
        )}

        <svg
          className={styles.overlay}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {showSections
            ? receipt.sections.map((section) => {
                const color = sectionColor(section.section_type);
                return (
                  <g key={section.section_type}>
                    {lineRectsForLineIds(receipt.lines, section.line_ids).map(
                      (band, index) => (
                        <rect
                          key={`${section.section_type}-${index}`}
                          x={band.x * width}
                          y={band.y * height}
                          width={band.width * width}
                          height={band.height * height}
                          fill={color}
                          fillOpacity={0.16}
                          stroke={color}
                          strokeWidth={1}
                        />
                      ),
                    )}
                  </g>
                );
              })
            : null}

          {showItems
            ? receipt.items.map((item) => {
                const bounds = boundsForLineIds(receipt.lines, item.line_ids);
                if (!bounds) return null;
                const color = item.is_discount
                  ? "var(--color-purple)"
                  : "var(--color-blue)";
                return (
                  <rect
                    key={`item-${item.item_index}`}
                    x={bounds.x * width}
                    y={bounds.y * height}
                    width={bounds.width * width}
                    height={bounds.height * height}
                    fill="none"
                    stroke={color}
                    strokeWidth={3}
                  />
                );
              })
            : null}

          {highlight ? (
            <rect
              data-testid="hover-highlight"
              x={highlight.x * width}
              y={highlight.y * height}
              width={highlight.width * width}
              height={highlight.height * height}
              fill="var(--color-red, #e5484d)"
              fillOpacity={0.24}
              stroke="var(--color-red, #e5484d)"
              strokeWidth={5}
            />
          ) : null}
        </svg>
      </div>
    </div>
  );
};

export default ReceiptCanvas;
