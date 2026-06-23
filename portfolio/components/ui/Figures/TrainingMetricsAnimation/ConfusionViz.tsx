import React, { useState, useMemo } from "react";
import styles from "./TrainingMetricsAnimation.module.css";
import {
  buildPatternHeatmapPlan,
  buildTopConfusionPairs,
  ConfusionPair,
  PatternHeatmapCell,
  ReceiptHeatmapZone,
} from "./confusionPairs";
import {
  buildFamilyView,
  buildSubView,
  CM_FAMILIES,
  formatLabel,
} from "./confusionMatrixView";

// Confusion Matrix Heatmap Component
export interface ConfusionMatrixProps {
  labels: string[];
  matrix: number[][];
}

export const ConfusionMatrix: React.FC<ConfusionMatrixProps> = ({ labels, matrix }) => {
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

export interface MatrixCellProps {
  value: number;
  rowSum: number;
  isDiagonal: boolean;
}

export const MatrixCell: React.FC<MatrixCellProps> = ({ value, rowSum, isDiagonal }) => {
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

export interface TopConfusionPairsProps {
  labels: string[];
  matrix: number[][];
}

export const TopConfusionPairs: React.FC<TopConfusionPairsProps> = ({
  labels,
  matrix,
}) => {
  const pairs = useMemo(
    () => buildTopConfusionPairs(labels, matrix, 3),
    [labels, matrix]
  );

  if (pairs.length === 0) return null;

  const maxCount = Math.max(...pairs.map((pair) => pair.count), 1);

  return (
    <div
      className={styles.confusionPairs}
      aria-label="Top confusion pairs and synthetic receipt targets"
    >
      <div className={styles.confusionPairsHeader}>
        <span>Top Confusions</span>
        <span>Pattern Target</span>
      </div>
      {pairs.map((pair) => (
        <ConfusionPairRow
          key={pair.id}
          pair={pair}
          maxCount={maxCount}
        />
      ))}
      <PatternHeatmapPanel pairs={pairs} />
    </div>
  );
};

export interface ConfusionPairRowProps {
  pair: ConfusionPair;
  maxCount: number;
}

export const ConfusionPairRow: React.FC<ConfusionPairRowProps> = ({
  pair,
  maxCount,
}) => {
  const widthPct = Math.max(8, Math.min(100, (pair.count / maxCount) * 100));

  return (
    <div className={styles.confusionPairRow}>
      <div className={styles.confusionPairMetric}>
        <div className={styles.confusionPairLabels}>
          <span>{formatLabel(pair.actualLabel)}</span>
          <span className={styles.confusionArrow}>→</span>
          <span>{formatLabel(pair.predictedLabel)}</span>
        </div>
        <div className={styles.confusionPairBar}>
          <span style={{ width: `${widthPct}%` }} />
        </div>
        <div className={styles.confusionPairCount}>
          {pair.count.toLocaleString()} · {(pair.share * 100).toFixed(0)}%
        </div>
      </div>
      <div className={styles.confusionPairTarget}>
        <span className={styles.patternTarget}>{pair.patternTarget}</span>
        <span className={styles.syntheticTarget}>{pair.syntheticTarget}</span>
        <span className={styles.llmCue}>{pair.llmCue}</span>
      </div>
    </div>
  );
};

export const RECEIPT_HEATMAP_BANDS: {
  zone: ReceiptHeatmapZone;
  y: number;
  height: number;
}[] = [
  { zone: "header", y: 18, height: 42 },
  { zone: "identity", y: 66, height: 50 },
  { zone: "items", y: 124, height: 86 },
  { zone: "totals", y: 218, height: 42 },
  { zone: "footer", y: 266, height: 26 },
];

export const RECEIPT_HEATMAP_LABELS: Record<ReceiptHeatmapZone, string> = {
  header: "Header",
  identity: "Identity",
  items: "Items",
  totals: "Totals",
  footer: "Footer",
};

export interface PatternHeatmapPanelProps {
  pairs: ConfusionPair[];
}

export const PatternHeatmapPanel: React.FC<PatternHeatmapPanelProps> = ({
  pairs,
}) => {
  const plan = useMemo(() => buildPatternHeatmapPlan(pairs), [pairs]);
  if (plan.cells.length === 0) return null;

  const cellsByZone = new Map(plan.cells.map((cell) => [cell.zone, cell]));
  const primaryCells = plan.cells.slice(0, 3);

  return (
    <div
      className={styles.patternMiningPanel}
      aria-label="Receipt-zone heatmap and synthetic receipt plan"
    >
      <svg
        className={styles.receiptHeatmap}
        viewBox="0 0 190 310"
        role="img"
        aria-label="Receipt heatmap of confusion-prone zones"
      >
        <title>Confusion-pair heatmap projected onto receipt zones</title>
        <rect
          className={styles.receiptHeatmapPaper}
          x="22"
          y="8"
          width="146"
          height="294"
          rx="4"
        />
        {RECEIPT_HEATMAP_BANDS.map((band) => {
          const cell = cellsByZone.get(band.zone);
          const alpha = cell ? 0.1 + cell.intensity * 0.48 : 0.05;
          return (
            <g key={band.zone}>
              <rect
                className={styles.receiptHeatmapZone}
                x="34"
                y={band.y}
                width="122"
                height={band.height}
                rx="3"
                style={{
                  fill: `rgba(var(--color-red-rgb), ${alpha})`,
                }}
              />
              <text
                className={styles.receiptHeatmapLabel}
                x="42"
                y={band.y + 16}
              >
                {cell?.label || RECEIPT_HEATMAP_LABELS[band.zone]}
              </text>
              {cell && (
                <text
                  className={styles.receiptHeatmapCount}
                  x="148"
                  y={band.y + 16}
                  textAnchor="end"
                >
                  {cell.count}
                </text>
              )}
            </g>
          );
        })}
        <path
          className={styles.receiptHeatmapFold}
          d="M34 292 L48 278 L62 292 L76 278 L90 292 L104 278 L118 292 L132 278 L156 292"
        />
      </svg>

      <div className={styles.patternMiningSummary}>
        <div className={styles.patternMiningTitle}>Pattern Mining</div>
        <div className={styles.patternMiningMeta}>
          {plan.syntheticReceiptCount} train-only synthetic receipts
        </div>
        {primaryCells.map((cell) => (
          <PatternHeatmapCellRow key={cell.zone} cell={cell} />
        ))}
      </div>
    </div>
  );
};

export interface PatternHeatmapCellRowProps {
  cell: PatternHeatmapCell;
}

export const PatternHeatmapCellRow: React.FC<PatternHeatmapCellRowProps> = ({
  cell,
}) => (
  <div className={styles.patternHeatmapCellRow}>
    <div className={styles.patternHeatmapCellHeader}>
      <span>{cell.label}</span>
      <span>{cell.count.toLocaleString()}</span>
    </div>
    <div className={styles.patternHeatmapCellBar}>
      <span style={{ width: `${Math.round(cell.intensity * 100)}%` }} />
    </div>
    <div className={styles.patternHeatmapSynthetic}>
      {cell.syntheticBrief}
    </div>
    <div className={styles.patternHeatmapPrompt}>{cell.llmPrompt}</div>
  </div>
);
