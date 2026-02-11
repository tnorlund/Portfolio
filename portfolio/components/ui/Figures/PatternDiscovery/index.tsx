import { useCallback, useEffect, useMemo, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  Constellation,
  LabelPositionStats,
  PatternResponse,
  PatternSampleReceipt,
} from "../../../../types/api";
import {
  detectImageFormatSupport,
  FormatSupport,
  getBestImageUrl,
  ImageFormats,
} from "../../../../utils/imageFormat";
import styles from "./PatternDiscovery.module.css";

// Semantic label color palette — consistent with project conventions
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  ADDRESS_LINE: "var(--color-red)",
  PHONE_NUMBER: "var(--color-orange)",
  WEBSITE: "var(--color-purple)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  GRAND_TOTAL: "var(--color-green)",
  SUBTOTAL: "var(--color-green)",
  TAX: "var(--color-green)",
  LINE_TOTAL: "var(--color-green)",
  PRODUCT_NAME: "var(--text-color)",
  QUANTITY: "var(--text-color)",
  UNIT_PRICE: "var(--text-color)",
  DISCOUNT: "var(--color-orange)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-purple)",
};

// All valid semantic label names — anything not in this set is data noise
const VALID_LABELS = new Set([
  ...Object.keys(LABEL_COLORS),
  "CHANGE",
  "TENDER",
  "LOYALTY_ID",
  "REGISTER",
  "COUPON",
  "OTHER",
  "TIP",
  "CASHBACK",
]);

function isValidLabel(label: string): boolean {
  return VALID_LABELS.has(label);
}

function labelColor(label: string): string {
  return LABEL_COLORS[label] ?? "var(--text-color)";
}

// ---------------------------------------------------------------------------
// Label Position Map (SVG) — left panel
// ---------------------------------------------------------------------------

const POS_SVG_WIDTH = 320;
const POS_LEFT = 12;
const POS_TOP = 24;
const POS_BOTTOM_PAD = 24;
const MIN_LABEL_GAP = 16; // minimum px between label baselines

/**
 * Resolve label y-positions so no two labels overlap.
 * Marks stay at their true `mean_y`; labels are nudged apart and connected
 * by a thin leader line when displaced.
 */
function resolvePositions(
  entries: [string, LabelPositionStats][],
  yScale: (v: number) => number,
) {
  // Compute raw positions sorted top-to-bottom (smallest SVG y first)
  const items = entries
    .map(([label, stats]) => ({
      label,
      stats,
      markY: yScale(stats.mean_y),
      labelY: yScale(stats.mean_y),
    }))
    .sort((a, b) => a.markY - b.markY);

  // Push overlapping labels downward
  for (let i = 1; i < items.length; i++) {
    const gap = items[i].labelY - items[i - 1].labelY;
    if (gap < MIN_LABEL_GAP) {
      items[i].labelY = items[i - 1].labelY + MIN_LABEL_GAP;
    }
  }

  return items;
}

function LabelPositionMap({
  positions,
}: {
  positions: Record<string, LabelPositionStats>;
}) {
  const entries = useMemo(() => {
    return Object.entries(positions)
      .filter(([label, s]) => s.count > 0 && isValidLabel(label))
      .sort((a, b) => b[1].mean_y - a[1].mean_y);
  }, [positions]);

  if (entries.length === 0) {
    return (
      <div className={styles.emptyPanel}>No label position data available</div>
    );
  }

  const maxCount = Math.max(...entries.map(([, s]) => s.count));

  // Scale to the actual data range (with 10% padding)
  const yValues = entries.map(([, s]) => s.mean_y);
  const dataMin = Math.min(...yValues);
  const dataMax = Math.max(...yValues);
  const dataRange = dataMax - dataMin || 0.1;
  const pad = dataRange * 0.1;

  // Initial layout pass — use a generous working height
  const workingHeight = Math.max(entries.length * MIN_LABEL_GAP + 80, 400);
  const workingBottom = workingHeight - POS_BOTTOM_PAD;

  const yScale = (meanY: number) => {
    const t = (meanY - (dataMin - pad)) / (dataRange + pad * 2);
    return workingBottom - t * (workingBottom - POS_TOP);
  };

  const axisX = POS_LEFT + 8;
  const resolved = resolvePositions(entries, yScale);

  // Derive final SVG height from the actual extent of resolved labels
  const maxLabelY = Math.max(...resolved.map((r) => r.labelY));
  const svgHeight = Math.max(maxLabelY + POS_BOTTOM_PAD, workingHeight);
  const posBottom = svgHeight - POS_BOTTOM_PAD;

  return (
    <svg
      viewBox={`0 0 ${POS_SVG_WIDTH} ${svgHeight}`}
      className={styles.svg}
      role="img"
      aria-label="Label positions on a normalized receipt"
    >
      {/* Thin vertical axis rule */}
      <line
        x1={axisX}
        y1={POS_TOP}
        x2={axisX}
        y2={posBottom}
        stroke="var(--text-color)"
        strokeOpacity={0.15}
        strokeWidth={1}
      />

      {resolved.map(({ label, stats, markY, labelY }) => {
        const opacity = 0.4 + 0.6 * (stats.count / maxCount);
        const displaced = Math.abs(labelY - markY) > 2;

        return (
          <g key={label}>
            {/* Leader line when label is nudged away from mark */}
            {displaced && (
              <line
                x1={axisX}
                y1={markY}
                x2={axisX + 10}
                y2={labelY}
                stroke="var(--text-color)"
                strokeOpacity={0.1}
                strokeWidth={0.5}
              />
            )}
            {/* Position mark (always at true mean_y) */}
            <circle
              cx={axisX}
              cy={markY}
              r={2.5}
              fill={labelColor(label)}
              fillOpacity={opacity}
            />
            {/* Label text (collision-avoided) */}
            <text
              x={axisX + 12}
              y={labelY}
              dy="0.35em"
              fill={labelColor(label)}
              fillOpacity={opacity}
              fontSize={10}
              fontFamily="var(--font-mono, monospace)"
            >
              {label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Constellation Diagram (SVG) — right panel
// ---------------------------------------------------------------------------

const CON_SVG_WIDTH = 380;
const CON_GROUP_HEIGHT = 100;
const CON_GAP = 8;
const CON_PADDING = 16;
const CON_LABEL_MARGIN = 100; // reserve space for label text

function ConstellationDiagram({
  constellations,
}: {
  constellations: Constellation[];
}) {
  if (constellations.length === 0) {
    return (
      <div className={styles.emptyPanel}>No constellation data available</div>
    );
  }

  // Show observation count once if all groups share the same value
  const counts = constellations.map((c) => c.observation_count);
  const allSame = counts.every((c) => c === counts[0]);

  const totalHeight =
    constellations.length * CON_GROUP_HEIGHT +
    (constellations.length - 1) * CON_GAP +
    (allSame ? 16 : 0); // room for shared annotation

  return (
    <svg
      viewBox={`0 0 ${CON_SVG_WIDTH} ${totalHeight}`}
      className={styles.svg}
      role="img"
      aria-label="Constellation diagrams showing label co-occurrence geometry"
    >
      {constellations.map((c, idx) => (
        <ConstellationGroup
          key={idx}
          constellation={c}
          yOffset={idx * (CON_GROUP_HEIGHT + CON_GAP)}
          hideCount={allSame}
        />
      ))}
      {allSame && (
        <text
          x={CON_SVG_WIDTH - CON_PADDING}
          y={totalHeight - 2}
          textAnchor="end"
          fill="var(--text-color)"
          fillOpacity={0.25}
          fontSize={9}
        >
          {counts[0]} observations each
        </text>
      )}
    </svg>
  );
}

function ConstellationGroup({
  constellation,
  yOffset,
  hideCount = false,
}: {
  constellation: Constellation;
  yOffset: number;
  hideCount?: boolean;
}) {
  const { labels, relative_positions, observation_count } = constellation;

  // Find the spatial extent to normalize positions into the drawing area
  const positions = Object.entries(relative_positions);
  if (positions.length === 0) return null;

  const dxs = positions.map(([, p]) => p.mean_dx);
  const dys = positions.map(([, p]) => p.mean_dy);
  const minDx = Math.min(...dxs);
  const maxDx = Math.max(...dxs);
  const minDy = Math.min(...dys);
  const maxDy = Math.max(...dys);

  const rangeX = maxDx - minDx || 0.1;
  const rangeY = maxDy - minDy || 0.1;

  // Map relative positions to SVG coordinates
  const drawW = CON_SVG_WIDTH - CON_PADDING * 2 - CON_LABEL_MARGIN;
  const drawH = CON_GROUP_HEIGHT - CON_PADDING * 2 - 20;
  const centerX = CON_PADDING + drawW / 2;
  const centerY = yOffset + CON_GROUP_HEIGHT / 2;

  const scale = Math.min(drawW / rangeX, drawH / rangeY) * 0.8;

  const points: { label: string; cx: number; cy: number; stdR: number }[] =
    positions.map(([label, p]) => ({
      label,
      cx: centerX + p.mean_dx * scale,
      cy: centerY + p.mean_dy * scale, // positive dy = down
      stdR: Math.sqrt(p.std_dx ** 2 + p.std_dy ** 2) * scale,
    }));

  // Draw connecting lines between all labels in the constellation
  const lines: { x1: number; y1: number; x2: number; y2: number }[] = [];
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      lines.push({
        x1: points[i].cx,
        y1: points[i].cy,
        x2: points[j].cx,
        y2: points[j].cy,
      });
    }
  }

  return (
    <g>
      {/* Connecting lines — the primary visual showing structure */}
      {lines.map((l, i) => (
        <line
          key={i}
          x1={l.x1}
          y1={l.y1}
          x2={l.x2}
          y2={l.y2}
          stroke="var(--text-color)"
          strokeOpacity={0.25}
          strokeWidth={1}
        />
      ))}

      {/* Marks + labels with per-side collision avoidance */}
      {(() => {
        // Split into left-anchored and right-anchored groups
        const midX = CON_SVG_WIDTH / 2;
        const left = points
          .filter((pt) => pt.cx <= midX)
          .sort((a, b) => a.cy - b.cy);
        const right = points
          .filter((pt) => pt.cx > midX)
          .sort((a, b) => a.cy - b.cy);

        // De-collide each side independently
        function deCollide(group: typeof points) {
          const ys = group.map((pt) => pt.cy);
          for (let i = 1; i < ys.length; i++) {
            if (ys[i] - ys[i - 1] < 14) {
              ys[i] = ys[i - 1] + 14;
            }
          }
          return new Map(group.map((pt, i) => [pt.label, ys[i]]));
        }

        const yMap = new Map([...deCollide(left), ...deCollide(right)]);

        return points.map((pt) => {
          const labelY = yMap.get(pt.label) ?? pt.cy;
          const onRight = pt.cx > midX;
          const anchor = onRight ? "end" : "start";
          const textX = onRight ? pt.cx - 7 : pt.cx + 7;

          return (
            <g key={pt.label}>
              <circle
                cx={pt.cx}
                cy={pt.cy}
                r={3}
                fill={labelColor(pt.label)}
                fillOpacity={0.85}
              />
              <text
                x={textX}
                y={labelY}
                dy="0.35em"
                textAnchor={anchor}
                fill={labelColor(pt.label)}
                fillOpacity={0.85}
                fontSize={10}
                fontFamily="var(--font-mono, monospace)"
              >
                {pt.label}
              </text>
            </g>
          );
        });
      })()}

      {/* Observation count — only shown when groups differ */}
      {!hideCount && (
        <text
          x={CON_SVG_WIDTH - CON_PADDING}
          y={yOffset + CON_GROUP_HEIGHT - 4}
          textAnchor="end"
          fill="var(--text-color)"
          fillOpacity={0.25}
          fontSize={9}
        >
          n={observation_count}
        </text>
      )}
    </g>
  );
}

// ---------------------------------------------------------------------------
// Receipt Image Panel — sample receipt with SVG word overlay
// ---------------------------------------------------------------------------

function buildCdnKeys(imageId: string, receiptId: number): ImageFormats {
  const paddedId = String(receiptId).padStart(5, "0");
  const base = `assets/${imageId}_RECEIPT_${paddedId}`;
  return {
    cdn_s3_key: `${base}.jpg`,
    cdn_webp_s3_key: `${base}.webp`,
    cdn_avif_s3_key: `${base}.avif`,
  };
}

function ReceiptImagePanel({
  sample,
  formatSupport,
}: {
  sample: PatternSampleReceipt;
  formatSupport: FormatSupport | null;
}) {
  const [imgSize, setImgSize] = useState<{
    width: number;
    height: number;
  } | null>(null);

  const cdnKeys = useMemo(
    () => buildCdnKeys(sample.image_id, sample.receipt_id),
    [sample.image_id, sample.receipt_id],
  );

  const imgUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(cdnKeys, formatSupport);
  }, [cdnKeys, formatSupport]);

  const handleLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      setImgSize({ width: img.naturalWidth, height: img.naturalHeight });
    },
    [],
  );

  if (!imgUrl) {
    return (
      <div className={styles.emptyPanel}>Detecting image format...</div>
    );
  }

  // Filter to labeled words only (skip "O" and invalid labels)
  const labeledWords = sample.words.filter(
    (w) => w.label && w.label !== "O" && isValidLabel(w.label),
  );

  return (
    <div className={styles.receiptImageWrapper}>
      <div className={styles.receiptImageInner}>
        <img
          src={imgUrl}
          alt={`Receipt ${sample.image_id}`}
          className={styles.receiptImage}
          onLoad={handleLoad}
        />
        {imgSize && (
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${imgSize.width} ${imgSize.height}`}
            preserveAspectRatio="none"
          >
            {labeledWords.map((w) => (
              <rect
                key={`${w.line_id}-${w.word_id}`}
                x={w.bbox.x * imgSize.width}
                y={w.bbox.y * imgSize.height}
                width={w.bbox.width * imgSize.width}
                height={w.bbox.height * imgSize.height}
                fill={labelColor(w.label!)}
                fillOpacity={0.4}
                rx={1}
              />
            ))}
          </svg>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function PatternDiscovery() {
  const [data, setData] = useState<PatternResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(
    null,
  );
  const { ref, inView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: "100px",
  });

  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  useEffect(() => {
    if (!inView) return;
    api
      .fetchLabelEvaluatorPatterns()
      .then(setData)
      .catch((err) => setError(err.message));
  }, [inView]);

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.error}>Failed to load patterns: {error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading}>Loading patterns...</div>
      </div>
    );
  }

  const { merchant } = data;

  return (
    <div ref={ref} className={styles.container}>
      {/* Header line */}
      <div className={styles.header}>
        <h3 className={styles.merchantName}>{merchant.merchant_name}</h3>
        <div className={styles.headerMeta}>
          {merchant.pattern && (
            <span className={styles.receiptType}>
              {merchant.pattern.receipt_type}
            </span>
          )}
          <span className={styles.receiptCount}>
            {merchant.receipt_count} receipts
          </span>
        </div>
      </div>

      {/* Three-panel layout */}
      <div
        className={
          merchant.sample_receipt ? styles.panelsThree : styles.panels
        }
      >
        {merchant.sample_receipt && (
          <div className={styles.panel}>
            <ReceiptImagePanel
              sample={merchant.sample_receipt}
              formatSupport={formatSupport}
            />
          </div>
        )}
        <div className={styles.panel}>
          <LabelPositionMap positions={merchant.label_positions} />
        </div>
        <div className={styles.panel}>
          <ConstellationDiagram constellations={merchant.constellations} />
        </div>
      </div>

      {/* Annotation footer */}
      <div className={styles.footer}>
        {merchant.pattern?.receipt_type_reason && (
          <p className={styles.reason}>{merchant.pattern.receipt_type_reason}</p>
        )}
        <p className={styles.footerMeta}>
          1 of {data.total_count} merchants
        </p>
      </div>
    </div>
  );
}
