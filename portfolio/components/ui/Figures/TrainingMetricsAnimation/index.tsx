import React, { useCallback, useEffect, useState, useMemo, useRef } from "react";
import { animated, useSpring } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import { DatasetMetrics, TrainingMetricsEpoch } from "../../../../types/api";
import styles from "./TrainingMetricsAnimation.module.css";
import {
  axisLabelAnchor,
  clickXToEpochIndex,
  computeAxisLabels,
  computeScales,
} from "./sparkline";

// Normalize ADDRESS_LINE to ADDRESS for display purposes
const normalizeLabel = (label: string): string => {
  if (label === "ADDRESS_LINE") return "ADDRESS";
  return label;
};

// Label color mapping for hybrid model
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  AMOUNT: "var(--color-green)",
  ADDRESS: "var(--color-red)",
  PHONE_NUMBER: "var(--color-pink)",
  WEBSITE: "var(--color-purple)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-orange)",
  O: "var(--color-purple)",
};

const getLabelColor = (label: string): string => {
  return LABEL_COLORS[normalizeLabel(label)] || "var(--color-gray, #888)";
};

// Format label: "MERCHANT_NAME" -> "Merchant Name", "O" -> "None"
const formatLabel = (label: string): string => {
  const normalized = normalizeLabel(label);
  if (normalized === "O") return "None";
  return normalized
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
};

// Abbreviated labels for confusion matrix (drill-in member labels)
const LABEL_ABBREV: Record<string, string> = {
  MERCHANT_NAME: "Merch",
  DATE: "Date",
  TIME: "Time",
  AMOUNT: "Amt",
  GRAND_TOTAL: "Total",
  SUBTOTAL: "Subt",
  TAX: "Tax",
  LINE_TOTAL: "Line",
  UNIT_PRICE: "Unit",
  DISCOUNT: "Disc",
  COUPON: "Coup",
  TIP: "Tip",
  CHANGE: "Chng",
  CASH_BACK: "Cash",
  REFUND: "Rfnd",
  QUANTITY: "Qty",
  ADDRESS: "Addr",
  ADDRESS_LINE: "Addr",
  PHONE_NUMBER: "Phone",
  WEBSITE: "Web",
  STORE_HOURS: "Hours",
  PAYMENT_METHOD: "Pay",
  O: "O",
};

// Confusion-matrix families — collapse the granular labels into ~10 groups so
// the matrix is readable. Multi-member families (Charges, Credits, Date/Time,
// Hours/Pay, Address) can be drilled into to see their sub-matrix.
const CM_FAMILIES: { key: string; abbr: string; full: string; members: string[] }[] = [
  { key: "MERCHANT", abbr: "Merch", full: "Merchant", members: ["MERCHANT_NAME"] },
  { key: "DATETIME", abbr: "D/T", full: "Date / Time", members: ["DATE", "TIME"] },
  { key: "CHARGES", abbr: "Chrg", full: "Charges (total·subtotal·tax·line·unit)", members: ["GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL", "UNIT_PRICE", "AMOUNT"] },
  { key: "CREDITS", abbr: "Crdt", full: "Credits (discount·tip·change…)", members: ["DISCOUNT", "COUPON", "TIP", "CHANGE", "CASH_BACK", "REFUND"] },
  { key: "QTY", abbr: "Qty", full: "Quantity", members: ["QUANTITY"] },
  { key: "ADDRESS", abbr: "Addr", full: "Address", members: ["ADDRESS_LINE", "ADDRESS"] },
  { key: "PHONE", abbr: "Phone", full: "Phone", members: ["PHONE_NUMBER"] },
  { key: "WEBSITE", abbr: "Web", full: "Website", members: ["WEBSITE"] },
  { key: "HOURSPAY", abbr: "Hr/Py", full: "Hours / Payment", members: ["STORE_HOURS", "PAYMENT_METHOD"] },
  { key: "O", abbr: "O", full: "O (no label)", members: ["O"] },
];

interface MatrixView {
  labels: string[];
  titles: string[];
  matrix: number[][];
  drillKeys: (string | null)[];
}

const buildFamilyView = (labels: string[], matrix: number[][]): MatrixView => {
  const labelToFam = new Map<string, number>();
  CM_FAMILIES.forEach((f, fi) => f.members.forEach((m) => labelToFam.set(m, fi)));
  const present = CM_FAMILIES.map((_f, fi) => fi).filter((fi) =>
    CM_FAMILIES[fi].members.some((m) => labels.includes(m))
  );
  const compact = new Map<number, number>();
  present.forEach((fi, k) => compact.set(fi, k));
  const n = present.length;
  const out = Array.from({ length: n }, () => Array(n).fill(0));
  labels.forEach((rl, i) => {
    const rf = labelToFam.get(rl);
    if (rf === undefined || !compact.has(rf)) return;
    labels.forEach((cl, j) => {
      const cf = labelToFam.get(cl);
      if (cf === undefined || !compact.has(cf)) return;
      out[compact.get(rf) as number][compact.get(cf) as number] +=
        matrix[i]?.[j] || 0;
    });
  });
  return {
    labels: present.map((fi) => CM_FAMILIES[fi].abbr),
    titles: present.map((fi) => CM_FAMILIES[fi].full),
    matrix: out,
    drillKeys: present.map((fi) =>
      CM_FAMILIES[fi].members.filter((m) => labels.includes(m)).length > 1
        ? CM_FAMILIES[fi].key
        : null
    ),
  };
};

const buildSubView = (
  labels: string[],
  matrix: number[][],
  famKey: string
): MatrixView | null => {
  const fam = CM_FAMILIES.find((f) => f.key === famKey);
  if (!fam) return null;
  const idxs = fam.members
    .map((m) => labels.indexOf(m))
    .filter((x) => x >= 0);
  if (idxs.length < 2) return null;
  return {
    labels: idxs.map((x) => formatLabelAbbrev(labels[x])),
    titles: idxs.map((x) => formatLabel(labels[x])),
    matrix: idxs.map((ri) => idxs.map((ci) => matrix[ri]?.[ci] || 0)),
    drillKeys: idxs.map(() => null),
  };
};

const formatLabelAbbrev = (label: string): string => {
  const normalized = normalizeLabel(label);
  return LABEL_ABBREV[normalized] || normalized.slice(0, 4);
};

// Spring config for smooth animations
const SPRING_CONFIG = { tension: 120, friction: 14 };

// Cap the total autoplay duration so a 60+-epoch run finishes in a
// reasonable time rather than ~75s at 1200ms/step.
const TIMELINE_AUTOPLAY_TOTAL_MS = 18000;
const TIMELINE_AUTOPLAY_MIN_STEP_MS = 250;
const TIMELINE_AUTOPLAY_MAX_STEP_MS = 1200;

// Epoch Sparkline: a line chart of val_f1 across all epochs. Replaces
// the per-epoch dot row which couldn't scale past ~20 epochs and only
// encoded "which epoch you're on" — the sparkline additionally encodes
// the convergence shape, plateau, and any drift the user can read at a
// glance. Click anywhere on the curve to scrub.
interface EpochSparklineProps {
  epochs: TrainingMetricsEpoch[];
  currentIndex: number;
  onSelectEpoch: (index: number) => void;
  showBestLabel: boolean;
}

// SVG viewBox dims (internal coordinates). The viewBox width tracks the
// rendered container width (via ResizeObserver) so the curve isn't
// horizontally stretched by `preserveAspectRatio="none"` — a 7%-or-so
// distortion would otherwise make the early-epoch climb look gentler
// than it really is.
const SVG_W_FALLBACK = 600; // before measurement / SSR
const SVG_H = 80;
const SVG_PAD_X = 8; // left/right inset so end markers don't clip
const SVG_PAD_TOP = 18; // room for "BEST" label
const SVG_PAD_BOTTOM = 14; // room for x-axis epoch numbers
const AXIS_LABEL_MIN_GAP_PX = 24; // min viewBox-x distance before suppressing best label

const SPARKLINE_DIMS_FALLBACK = {
  width: SVG_W_FALLBACK,
  height: SVG_H,
  padX: SVG_PAD_X,
  padTop: SVG_PAD_TOP,
  padBottom: SVG_PAD_BOTTOM,
};

const EpochSparkline: React.FC<EpochSparklineProps> = ({
  epochs,
  currentIndex,
  onSelectEpoch,
  showBestLabel,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const [svgWidth, setSvgWidth] = useState<number>(SVG_W_FALLBACK);

  // Track rendered SVG width so the viewBox aspect matches the screen
  // aspect and the curve isn't horizontally stretched.
  useEffect(() => {
    const node = svgRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.round(entry.contentRect.width);
        if (w > 0) setSvgWidth(w);
      }
    });
    ro.observe(node);
    return () => ro.disconnect();
  }, []);

  const dims = useMemo(
    () => ({ ...SPARKLINE_DIMS_FALLBACK, width: svgWidth }),
    [svgWidth]
  );

  const { points, yScale, xScale, dataMin, dataMax } = useMemo(
    () => computeScales(epochs, dims),
    [epochs, dims]
  );
  const bestIdx = useMemo(() => epochs.findIndex((e) => e.is_best), [epochs]);

  const handleClick = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if (epochs.length === 0 || !svgRef.current) return;
      const rect = svgRef.current.getBoundingClientRect();
      const idx = clickXToEpochIndex(
        e.clientX,
        rect.left,
        rect.width,
        svgWidth,
        SVG_PAD_X,
        epochs.length
      );
      onSelectEpoch(idx);
    },
    [epochs.length, onSelectEpoch, svgWidth]
  );

  // Keyboard navigation — required because role="slider" promises AT users
  // they can change the value (WCAG). Arrow keys step by 1, Home/End jump
  // to first/last.
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<SVGSVGElement>) => {
      if (epochs.length === 0) return;
      const last = epochs.length - 1;
      let next = currentIndex;
      switch (e.key) {
        case "ArrowLeft":
        case "ArrowDown":
          next = Math.max(0, currentIndex - 1);
          break;
        case "ArrowRight":
        case "ArrowUp":
          next = Math.min(last, currentIndex + 1);
          break;
        case "PageDown":
          next = Math.max(0, currentIndex - Math.max(1, Math.floor(last / 10)));
          break;
        case "PageUp":
          next = Math.min(
            last,
            currentIndex + Math.max(1, Math.floor(last / 10))
          );
          break;
        case "Home":
          next = 0;
          break;
        case "End":
          next = last;
          break;
        default:
          return;
      }
      e.preventDefault();
      if (next !== currentIndex) onSelectEpoch(next);
    },
    [epochs.length, currentIndex, onSelectEpoch]
  );

  const currentEpoch = epochs[currentIndex];
  const currentF1 = currentEpoch?.metrics?.val_f1 ?? 0;
  const cx = xScale(currentIndex);
  const cy = yScale(currentF1);
  const bestEpoch = bestIdx >= 0 ? epochs[bestIdx] : null;
  const bestF1 = bestEpoch?.metrics?.val_f1 ?? 0;
  const bx = bestIdx >= 0 ? xScale(bestIdx) : 0;
  const by = bestIdx >= 0 ? yScale(bestF1) : 0;
  const lastIdx = epochs.length - 1;
  const showBest = bestIdx >= 0 && showBestLabel;

  const axisLabels = useMemo(
    () => computeAxisLabels(epochs, bestIdx, xScale, AXIS_LABEL_MIN_GAP_PX),
    [epochs, bestIdx, xScale]
  );
  void dataMin;
  void dataMax;

  return (
    <>
      {/* Desktop sparkline */}
      <div className={styles.timeline}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${svgWidth} ${SVG_H}`}
          preserveAspectRatio="none"
          className={styles.sparkline}
          onClick={handleClick}
          onKeyDown={handleKeyDown}
          tabIndex={0}
          role="slider"
          aria-label={`Training epoch — use arrow keys to scrub, Home/End to jump to first/last`}
          aria-valuemin={0}
          aria-valuemax={lastIdx}
          aria-valuenow={currentIndex}
          aria-valuetext={
            currentEpoch
              ? `Epoch ${currentEpoch.epoch}${
                  currentIndex === bestIdx ? " (best)" : ""
                }, F1 ${currentF1.toFixed(3)}`
              : undefined
          }
        >
          {/* Convergence curve */}
          {epochs.length >= 2 && (
            <polyline
              points={points}
              className={styles.sparklinePath}
              vectorEffect="non-scaling-stroke"
            />
          )}

          {/* Vertical drop line at active epoch */}
          <line
            x1={cx}
            x2={cx}
            y1={cy}
            y2={SVG_H - SVG_PAD_BOTTOM}
            className={styles.sparklineActiveLine}
            vectorEffect="non-scaling-stroke"
          />

          {/* Best epoch marker (open ring under the curve, labeled) */}
          {bestIdx >= 0 && (
            <>
              {showBest && (
                <text
                  x={bx}
                  y={SVG_PAD_TOP - 6}
                  className={styles.sparklineBestLabel}
                  textAnchor="middle"
                >
                  BEST
                </text>
              )}
              <circle
                cx={bx}
                cy={by}
                r={4}
                className={styles.sparklineBestMarker}
                vectorEffect="non-scaling-stroke"
              >
                <title>
                  {bestEpoch
                    ? `Best epoch ${bestEpoch.epoch} · F1 ${bestF1.toFixed(3)}`
                    : ""}
                </title>
              </circle>
            </>
          )}

          {/* Active epoch marker — filled dot on the curve */}
          <circle
            cx={cx}
            cy={cy}
            r={4.5}
            className={styles.sparklineActiveMarker}
            vectorEffect="non-scaling-stroke"
          />

          {/* Axis labels (first/best/last epoch numbers) */}
          {axisLabels.map((l) => (
            <text
              key={l.key}
              x={l.x}
              y={SVG_H - 3}
              className={styles.sparklineAxisLabel}
              textAnchor={axisLabelAnchor(l.x, svgWidth, SVG_PAD_X)}
            >
              {l.text}
            </text>
          ))}
        </svg>
      </div>

      {/* Mobile uses the existing prev/next arrow UI — touch-friendly,
          handles any epoch count naturally. */}
      <div className={styles.timelineMobile}>
        <button
          className={styles.timelineArrow}
          onClick={() => onSelectEpoch(Math.max(0, currentIndex - 1))}
          disabled={currentIndex === 0}
          aria-label="Previous epoch"
        >
          ‹
        </button>
        <div className={styles.timelineMobileCenter}>
          {showBest && currentIndex === bestIdx && (
            <span className={styles.timelineBestLabelMobile}>Best</span>
          )}
          <span className={styles.timelineMobileText}>
            {currentIndex + 1} / {epochs.length}
          </span>
        </div>
        <button
          className={styles.timelineArrow}
          onClick={() => onSelectEpoch(Math.min(epochs.length - 1, currentIndex + 1))}
          disabled={currentIndex === epochs.length - 1}
          aria-label="Next epoch"
        >
          ›
        </button>
      </div>
    </>
  );
};

// Dataset Stats Component with segmented bars
interface DatasetStatsProps {
  datasetMetrics?: DatasetMetrics;
}

const DatasetStats: React.FC<DatasetStatsProps> = ({ datasetMetrics }) => {
  // Validate required fields exist
  if (
    !datasetMetrics ||
    datasetMetrics.num_train_samples == null ||
    datasetMetrics.num_val_samples == null ||
    datasetMetrics.o_entity_ratio_train == null
  ) {
    return null;
  }

  const { num_train_samples, num_val_samples, o_entity_ratio_train } =
    datasetMetrics;

  // Calculate percentages for train/val split
  const total = num_train_samples + num_val_samples;
  if (total === 0) return null;

  const trainPercent = (num_train_samples / total) * 100;
  const valPercent = (num_val_samples / total) * 100;

  // Calculate percentages for O:entity ratio
  // ratio = O / entity, so entity% = 1 / (1 + ratio), O% = ratio / (1 + ratio)
  const ratio = o_entity_ratio_train;
  const entityPercent = (1 / (1 + ratio)) * 100;
  const oPercent = (ratio / (1 + ratio)) * 100;

  return (
    <div className={styles.datasetStats}>
      {/* Train/Val Split Bar */}
      <div className={styles.statGroup}>
        <span className={styles.statLabel}>Train/Val</span>
        <div className={styles.segmentedBar}>
          <div
            className={styles.segmentTrain}
            style={{ width: `${trainPercent}%` }}
            title={`Train: ${num_train_samples.toLocaleString()}`}
          />
          <div
            className={styles.segmentVal}
            style={{ width: `${valPercent}%` }}
            title={`Val: ${num_val_samples.toLocaleString()}`}
          />
        </div>
        <span className={styles.statValues}>
          {num_train_samples.toLocaleString()} / {num_val_samples.toLocaleString()}
        </span>
      </div>

      {/* O:Entity Ratio Bar */}
      <div className={styles.statGroup}>
        <span className={styles.statLabel}>Labeled</span>
        <div className={styles.segmentedBar}>
          <div
            className={styles.segmentEntity}
            style={{ width: `${entityPercent}%` }}
            title={`Entity tokens: ${entityPercent.toFixed(0)}%`}
          />
          <div
            className={styles.segmentO}
            style={{ width: `${oPercent}%` }}
            title={`O tokens: ${oPercent.toFixed(0)}%`}
          />
        </div>
        <span className={styles.statValues}>{ratio.toFixed(1)}:1</span>
      </div>
    </div>
  );
};

// F1 Score Gauge Component
interface F1GaugeProps {
  value: number;
}

const F1Gauge: React.FC<F1GaugeProps> = ({ value }) => {
  const spring = useSpring({
    to: { value: value, width: value * 100 },
    config: SPRING_CONFIG,
  });

  return (
    <div className={styles.gaugeContainer}>
      <animated.span className={styles.gaugeValue}>
        {spring.value.to((v) => v.toFixed(2))}
      </animated.span>
      <div className={styles.gaugeBar}>
        <animated.div
          className={styles.gaugeBarFill}
          style={{ width: spring.width.to((w) => `${w}%`) }}
        />
      </div>
    </div>
  );
};

// Per-Label Bars Component
interface PerLabelBarsProps {
  perLabel: Record<string, { f1: number; precision: number; recall: number; support: number }>;
}

const PerLabelBars: React.FC<PerLabelBarsProps> = ({ perLabel }) => {
  const labels = Object.keys(perLabel).filter((l) => l !== "O");
  const maxSupport = Math.max(...labels.map((l) => perLabel[l]?.support || 0), 1);

  return (
    <div className={styles.perLabelContainer}>
      {labels.map((label) => {
        const { f1 = 0, support = 0 } = perLabel[label] || {};
        return (
          <LabelBar
            key={label}
            label={label}
            value={f1}
            support={support}
            maxSupport={maxSupport}
          />
        );
      })}
    </div>
  );
};

interface LabelBarProps {
  label: string;
  value: number;
  support: number;
  maxSupport: number;
}

const LabelBar: React.FC<LabelBarProps> = ({ label, value, support, maxSupport }) => {
  const widthPct = Math.max(0, Math.min(100, value * 100));
  const distWidthPct = maxSupport > 0 ? Math.max(0, Math.min(100, (support / maxSupport) * 100)) : 0;

  return (
    <div className={styles.labelRow}>
      <span className={styles.labelName}>{formatLabel(label)}</span>
      <div className={styles.labelBarStack}>
        <div className={styles.labelBarSegmented}>
          <div
            className={styles.labelBarFilled}
            style={{ width: `${widthPct}%` }}
          />
          <div
            className={styles.labelBarEmpty}
            style={{ width: `${100 - widthPct}%` }}
          />
        </div>
        <div className={styles.labelBarDistribution}>
          <div
            className={styles.labelBarDistFilled}
            style={{ width: `${distWidthPct}%` }}
          />
          <div
            className={styles.labelBarDistEmpty}
            style={{ width: `${100 - distWidthPct}%` }}
          />
        </div>
      </div>
      <span className={styles.labelBarValue}>{value.toFixed(2)}</span>
    </div>
  );
};

// Bar Legend Component
const BarLegend: React.FC = () => (
  <div className={styles.barLegend}>
    <div className={styles.legendEntry}>
      <span
        className={styles.legendSwatch}
        style={{ background: "var(--text-color)" }}
      />
      <span className={styles.legendLabel}>F1 Score</span>
    </div>
    <div className={styles.legendEntry}>
      <span
        className={styles.legendSwatch}
        style={{ background: "var(--color-blue)" }}
      />
      <span className={styles.legendLabel}>Support</span>
    </div>
  </div>
);

// Confusion Matrix Heatmap Component
interface ConfusionMatrixProps {
  labels: string[];
  matrix: number[][];
}

const ConfusionMatrix: React.FC<ConfusionMatrixProps> = ({ labels, matrix }) => {
  // Default to a ~10-family heatmap; drill into a multi-member family on click.
  const [expanded, setExpanded] = useState<string | null>(null);

  const familyView = useMemo(
    () => buildFamilyView(labels, matrix),
    [labels, matrix]
  );
  const subView = useMemo(
    () => (expanded ? buildSubView(labels, matrix, expanded) : null),
    [expanded, labels, matrix]
  );
  const view = subView || familyView;

  const rowSums = useMemo(
    () => view.matrix.map((row) => row.reduce((s, v) => s + v, 0) || 1),
    [view]
  );
  const expandedFull = expanded
    ? CM_FAMILIES.find((f) => f.key === expanded)?.full
    : null;

  const gridTemplateColumns = `var(--matrix-label-col) repeat(${view.labels.length}, var(--matrix-cell-size))`;
  const gridTemplateRows = `var(--matrix-header-row) repeat(${view.labels.length}, var(--matrix-cell-size))`;

  return (
    <div className={styles.matrixContainer}>
      {expanded && (
        <button
          type="button"
          className={styles.matrixBack}
          onClick={() => setExpanded(null)}
        >
          ← all families · {expandedFull}
        </button>
      )}
      <div
        className={styles.matrixGrid}
        style={{ gridTemplateColumns, gridTemplateRows }}
      >
        {/* Corner cell */}
        <div className={styles.matrixCorner} />

        {/* X-axis labels (top) */}
        {view.labels.map((label, j) => (
          <div
            key={`x-${j}-${label}`}
            className={styles.matrixAxisLabel}
            title={view.titles[j]}
          >
            {label}
          </div>
        ))}

        {/* Matrix rows */}
        {view.matrix.map((row, i) => {
          const drill = view.drillKeys[i];
          return (
            <React.Fragment key={`row-${i}-${view.labels[i]}`}>
              {/* Y-axis label (clickable when the family can be drilled in) */}
              <div
                className={`${styles.matrixAxisLabel} ${styles.matrixAxisLabelY} ${
                  drill ? styles.matrixAxisLabelClickable : ""
                }`}
                title={view.titles[i]}
                role={drill ? "button" : undefined}
                onClick={drill ? () => setExpanded(drill) : undefined}
              >
                {view.labels[i]}
                {drill ? " ▸" : ""}
              </div>

              {/* Cells */}
              {row.map((value, j) => (
                <MatrixCell
                  key={`${i}-${j}`}
                  value={value}
                  rowSum={rowSums[i]}
                  isDiagonal={i === j}
                />
              ))}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
};

interface MatrixCellProps {
  value: number;
  rowSum: number;
  isDiagonal: boolean;
}

const MatrixCell: React.FC<MatrixCellProps> = ({ value, rowSum, isDiagonal }) => {
  // Row-normalized intensity: what % of this row's predictions went to this cell
  const intensity = rowSum > 0 ? value / rowSum : 0;

  // Use green for diagonal (correct predictions), red for off-diagonal (errors)
  // Empty cells (value = 0) use transparent background
  const colorVar = isDiagonal ? "--color-green-rgb" : "--color-red-rgb";
  const bg = intensity < 0.01 ? "transparent" : `rgba(var(${colorVar}), ${0.2 + intensity * 0.8})`;

  return (
    <div
      className={styles.matrixCell}
      style={{ backgroundColor: bg }}
    >
      <span>{value > 0 ? Math.round(value).toLocaleString() : ""}</span>
    </div>
  );
};

// Skeleton placeholder labels (match the 8 entity labels in the loaded state)
const SKELETON_LABELS = [
  "Address", "Amount", "Date", "Merchant Name",
  "Payment Method", "Store Hours", "Time", "Website",
];

// 9 labels for the confusion matrix (8 entity + O)
const SKELETON_MATRIX_LABELS = ["Addr", "Amt", "Date", "Merch", "Pay", "Hours", "Time", "Web", "O"];

const SKELETON_BG = "rgba(var(--text-color-rgb, 0, 0, 0), 0.08)";

// Skeleton that mirrors the loaded layout exactly
const TrainingMetricsSkeleton: React.FC = () => {
  const N = SKELETON_MATRIX_LABELS.length;
  const gridTemplateColumns = `var(--matrix-label-col) repeat(${N}, var(--matrix-cell-size))`;
  const gridTemplateRows = `var(--matrix-header-row) repeat(${N}, var(--matrix-cell-size))`;

  return (
    <>
      {/* DatasetStats skeleton */}
      <div className={styles.datasetStats}>
        <div className={styles.statGroup}>
          <span className={styles.statLabel}>Train/Val</span>
          <div className={styles.segmentedBar}>
            <div style={{ width: "90%", height: "100%", background: SKELETON_BG }} />
            <div style={{ width: "10%", height: "100%", background: SKELETON_BG, opacity: 0.5 }} />
          </div>
          <span className={styles.statValues} style={{ background: SKELETON_BG, borderRadius: 3, width: 70, height: 10 }} />
        </div>
        <div className={styles.statGroup}>
          <span className={styles.statLabel}>Labeled</span>
          <div className={styles.segmentedBar}>
            <div style={{ width: "33%", height: "100%", background: SKELETON_BG }} />
            <div style={{ width: "67%", height: "100%", background: SKELETON_BG, opacity: 0.5 }} />
          </div>
          <span className={styles.statValues} style={{ background: SKELETON_BG, borderRadius: 3, width: 30, height: 10 }} />
        </div>
      </div>

      {/* Desktop sparkline skeleton — flat baseline in the placeholder color */}
      <div className={styles.timeline}>
        <svg
          viewBox={`0 0 ${SVG_W_FALLBACK} ${SVG_H}`}
          preserveAspectRatio="none"
          className={styles.sparkline}
          aria-hidden="true"
        >
          <line
            x1={SVG_PAD_X}
            x2={SVG_W_FALLBACK - SVG_PAD_X}
            y1={SVG_H - SVG_PAD_BOTTOM}
            y2={SVG_H - SVG_PAD_BOTTOM}
            stroke={SKELETON_BG}
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>

      {/* Mobile timeline skeleton */}
      <div className={styles.timelineMobile}>
        <div className={styles.timelineArrow} style={{ opacity: 0.25 }}>‹</div>
        <div className={styles.timelineMobileCenter}>
          <span className={styles.timelineMobileText} style={{ opacity: 0.3 }}>— / —</span>
        </div>
        <div className={styles.timelineArrow} style={{ opacity: 0.25 }}>›</div>
      </div>

      {/* Left panel skeleton */}
      <div className={styles.leftPanel}>
        {/* F1 Gauge */}
        <div className={styles.gaugeContainer}>
          <div style={{ width: 80, height: 32, background: SKELETON_BG, borderRadius: 4 }} />
          <div className={styles.gaugeBar} />
        </div>

        {/* Per-label bars */}
        <div className={styles.perLabelContainer}>
          {SKELETON_LABELS.map((label) => (
            <div key={label} className={styles.labelRow}>
              <span className={styles.labelName} style={{ opacity: 0.3 }}>{label}</span>
              <div className={styles.labelBarStack}>
                <div className={styles.labelBarSegmented}>
                  <div className={styles.labelBarEmpty} style={{ width: "100%" }} />
                </div>
                <div className={styles.labelBarDistribution}>
                  <div className={styles.labelBarDistEmpty} style={{ width: "100%" }} />
                </div>
              </div>
              <span className={styles.labelBarValue} style={{ opacity: 0 }}>0.00</span>
            </div>
          ))}
        </div>

        {/* Bar legend */}
        <BarLegend />
      </div>

      {/* Right panel — confusion matrix skeleton */}
      <div className={styles.rightPanel}>
        <div className={styles.matrixContainer}>
          <div className={styles.matrixGrid} style={{ gridTemplateColumns, gridTemplateRows }}>
            <div className={styles.matrixCorner} />
            {SKELETON_MATRIX_LABELS.map((label) => (
              <div key={`x-${label}`} className={styles.matrixAxisLabel} style={{ opacity: 0.3 }}>
                {label}
              </div>
            ))}
            {SKELETON_MATRIX_LABELS.map((rowLabel, i) => (
              <React.Fragment key={`row-${i}`}>
                <div className={`${styles.matrixAxisLabel} ${styles.matrixAxisLabelY}`} style={{ opacity: 0.3 }}>
                  {rowLabel}
                </div>
                {SKELETON_MATRIX_LABELS.map((_, j) => (
                  <div
                    key={`${i}-${j}`}
                    className={styles.matrixCell}
                    style={{ backgroundColor: i === j ? SKELETON_BG : "transparent" }}
                  />
                ))}
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>
    </>
  );
};

// Main Component
const TrainingMetricsAnimation: React.FC = () => {
  const { ref: lazyRef, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "200px",
  });
  const { ref: animRef, inView } = useInView({
    threshold: 0.3,
    triggerOnce: true,
  });
  const setRefs = useCallback(
    (node: HTMLDivElement | null) => {
      lazyRef(node);
      animRef(node);
    },
    [lazyRef, animRef],
  );
  const [epochs, setEpochs] = useState<TrainingMetricsEpoch[]>([]);
  const [datasetMetrics, setDatasetMetrics] = useState<DatasetMetrics | undefined>();
  const [currentEpochIndex, setCurrentEpochIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [showBestLabel, setShowBestLabel] = useState(false);
  const hasStartedAnimation = useRef(false);
  const hasFetchedRef = useRef(false);

  // Fetch data only when near viewport - defers work until section is close
  useEffect(() => {
    if (!nearViewport || hasFetchedRef.current) return;
    hasFetchedRef.current = true;

    api
      .fetchFeaturedTrainingMetrics()
      .then((data) => {
        setEpochs(data.epochs);
        setDatasetMetrics(data.dataset_metrics);
        setIsLoading(false);
      })
      .catch((err) => {
        console.error("Failed to fetch training metrics:", err);
        setIsLoading(false);
      });
  }, [nearViewport]);

  // Autoplay animation when in view and data is loaded
  useEffect(() => {
    if (!inView || hasStartedAnimation.current || epochs.length === 0 || isLoading) {
      return;
    }

    hasStartedAnimation.current = true;
    setShowBestLabel(false);

    // Find the best epoch index
    const bestIndex = epochs.findIndex((e) => e.is_best);

    // Start from epoch 0, animate through ALL epochs, then land on best.
    // Step interval adapts to epoch count so the whole playback stays
    // bounded — at ~60+ epochs, 1200ms/step would take 75s, which is too
    // long for a marquee animation.
    const stepMs = Math.max(
      TIMELINE_AUTOPLAY_MIN_STEP_MS,
      Math.min(
        TIMELINE_AUTOPLAY_MAX_STEP_MS,
        Math.round(TIMELINE_AUTOPLAY_TOTAL_MS / Math.max(1, epochs.length))
      )
    );
    let currentIndex = 0;
    setCurrentEpochIndex(0);

    const interval = setInterval(() => {
      currentIndex++;

      if (currentIndex >= epochs.length) {
        clearInterval(interval);
        // After showing all epochs, jump to best and show label
        if (bestIndex !== -1) {
          setTimeout(() => {
            setCurrentEpochIndex(bestIndex);
            setShowBestLabel(true);
          }, 500);
        }
        return;
      }

      setCurrentEpochIndex(currentIndex);
    }, stepMs);

    return () => clearInterval(interval);
  }, [inView, epochs, isLoading]);

  const currentEpoch = epochs[currentEpochIndex];

  if (!nearViewport || isLoading) {
    return (
      <div ref={setRefs} className={styles.container}>
        <TrainingMetricsSkeleton />
      </div>
    );
  }

  if (!currentEpoch) {
    return (
      <div ref={setRefs} className={styles.container}>
        <TrainingMetricsSkeleton />
      </div>
    );
  }

  const handleSelectEpoch = (index: number) => {
    setCurrentEpochIndex(index);
  };

  return (
    <div ref={setRefs} className={styles.container}>
      <DatasetStats datasetMetrics={datasetMetrics} />
      <EpochSparkline
        epochs={epochs}
        currentIndex={currentEpochIndex}
        onSelectEpoch={handleSelectEpoch}
        showBestLabel={showBestLabel}
      />

      <div className={styles.leftPanel}>
        <F1Gauge value={currentEpoch.metrics.val_f1} />
        <PerLabelBars perLabel={currentEpoch.per_label} />
        <BarLegend />
      </div>

      <div className={styles.rightPanel}>
        {currentEpoch.confusion_matrix && (
          <ConfusionMatrix
            labels={currentEpoch.confusion_matrix.labels}
            matrix={currentEpoch.confusion_matrix.matrix}
          />
        )}
      </div>
    </div>
  );
};

export default TrainingMetricsAnimation;
