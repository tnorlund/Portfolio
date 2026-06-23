import React, { useCallback, useEffect, useState, useMemo, useRef } from "react";
import { animated, useSpring } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  DatasetMetrics,
  TrainingMetricsEpoch,
  TrainingSynthesisCandidateQuality,
  TrainingSynthesisAcceptedSourceLineage,
  TrainingSynthesisLayoutIntegrityEvidence,
  TrainingSynthesisMerchantGapSummary,
  TrainingSynthesisMixBalance,
  TrainingSynthesisQualityMerchant,
  TrainingSynthesisRealBaselineSummary,
  TrainingSynthesisSourceQualityMerchant,
  TrainingSynthesisStructureEvidence,
  TrainingSynthesisSummary,
  TrainingSynthesisTrainingBatchPolicy,
} from "../../../../types/api";
import styles from "./TrainingMetricsAnimation.module.css";
import {
  axisLabelAnchor,
  buildMetricPath,
  clickXToEpochIndex,
  computeAxisLabels,
  computeScales,
  countMetricValues,
} from "./sparkline";
import {
  buildPatternHeatmapPlan,
  buildTopConfusionPairs,
  ConfusionPair,
  PatternHeatmapCell,
  ReceiptHeatmapZone,
} from "./confusionPairs";

// Normalize ADDRESS_LINE to ADDRESS for display purposes
const normalizeLabel = (label: string): string => {
  if (label === "ADDRESS_LINE") return "ADDRESS";
  return label;
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
  synthesis?: TrainingSynthesisSummary | null;
}

// SVG viewBox dims (internal coordinates). The viewBox width tracks the
// rendered container width (via ResizeObserver) so the curve isn't
// horizontally stretched by `preserveAspectRatio="none"` — a 7%-or-so
// distortion would otherwise make the early-epoch climb look gentler
// than it really is.
const SVG_W_FALLBACK = 600; // before measurement / SSR
const SVG_H = 118;
const F1_CHART_H = 76;
const SVG_PAD_X = 8; // left/right inset so end markers don't clip
const SVG_PAD_TOP = 18; // room for "BEST" label
const F1_PAD_BOTTOM = 8;
const LOSS_STRIP_TOP = 86;
const LOSS_STRIP_BOTTOM = 104;
const AXIS_LABEL_MIN_GAP_PX = 24; // min viewBox-x distance before suppressing best label

const SPARKLINE_DIMS_FALLBACK = {
  width: SVG_W_FALLBACK,
  height: F1_CHART_H,
  padX: SVG_PAD_X,
  padTop: SVG_PAD_TOP,
  padBottom: F1_PAD_BOTTOM,
};

const EpochSparkline: React.FC<EpochSparklineProps> = ({
  epochs,
  currentIndex,
  onSelectEpoch,
  showBestLabel,
  synthesis,
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

  const ratioMetricKeys = useMemo(
    () =>
      (["val_f1", "val_precision", "val_recall"] as const).filter(
        (metricKey) => countMetricValues(epochs, metricKey) >= 2
      ),
    [epochs]
  );

  const { points, yScale, xScale, dataMin, dataMax } = useMemo(
    () => computeScales(epochs, dims, [...ratioMetricKeys]),
    [epochs, dims, ratioMetricKeys]
  );
  const bestIdx = useMemo(() => epochs.findIndex((e) => e.is_best), [epochs]);
  const precisionPath = useMemo(
    () => buildMetricPath(epochs, "val_precision", xScale, yScale),
    [epochs, xScale, yScale]
  );
  const recallPath = useMemo(
    () => buildMetricPath(epochs, "val_recall", xScale, yScale),
    [epochs, xScale, yScale]
  );

  const lossPaths = useMemo(() => {
    const values = epochs.flatMap((epoch) =>
      [epoch.metrics?.train_loss, epoch.metrics?.eval_loss].filter(
        (value): value is number => Number.isFinite(value)
      )
    );
    if (values.length === 0) {
      return null;
    }

    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min;
    const pad = span === 0 ? Math.max(0.01, max * 0.05) : span * 0.08;
    const dataMinLoss = Math.max(0, min - pad);
    const dataMaxLoss = max + pad;
    const yLossScale = (value: number) =>
      LOSS_STRIP_BOTTOM -
      ((value - dataMinLoss) / (dataMaxLoss - dataMinLoss || 1)) *
        (LOSS_STRIP_BOTTOM - LOSS_STRIP_TOP);

    return {
      train: buildMetricPath(epochs, "train_loss", xScale, yLossScale),
      eval: buildMetricPath(epochs, "eval_loss", xScale, yLossScale),
      hasTrain: countMetricValues(epochs, "train_loss") >= 2,
      hasEval: countMetricValues(epochs, "eval_loss") >= 2,
    };
  }, [epochs, xScale]);

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
  const currentPrecision = currentEpoch?.metrics?.val_precision;
  const currentRecall = currentEpoch?.metrics?.val_recall;
  const currentTrainLoss = currentEpoch?.metrics?.train_loss;
  const currentEvalLoss = currentEpoch?.metrics?.eval_loss;
  const cx = xScale(currentIndex);
  const cy = yScale(currentF1);
  const bestEpoch = bestIdx >= 0 ? epochs[bestIdx] : null;
  const bestF1 = bestEpoch?.metrics?.val_f1 ?? 0;
  const bx = bestIdx >= 0 ? xScale(bestIdx) : 0;
  const by = bestIdx >= 0 ? yScale(bestF1) : 0;
  const lastIdx = epochs.length - 1;
  const showBest = bestIdx >= 0 && showBestLabel;
  const showPostBestRegion = bestIdx >= 0 && bestIdx < lastIdx;
  const postBestWidth = showPostBestRegion
    ? Math.max(0, svgWidth - SVG_PAD_X - bx)
    : 0;

  const axisLabels = useMemo(
    () => computeAxisLabels(epochs, bestIdx, xScale, AXIS_LABEL_MIN_GAP_PX),
    [epochs, bestIdx, xScale]
  );
  void dataMin;
  void dataMax;

  const ariaMetricText = [
    `F1 ${currentF1.toFixed(3)}`,
    Number.isFinite(currentPrecision)
      ? `precision ${(currentPrecision as number).toFixed(3)}`
      : null,
    Number.isFinite(currentRecall)
      ? `recall ${(currentRecall as number).toFixed(3)}`
      : null,
    Number.isFinite(currentTrainLoss)
      ? `train loss ${(currentTrainLoss as number).toFixed(3)}`
      : null,
    Number.isFinite(currentEvalLoss)
      ? `eval loss ${(currentEvalLoss as number).toFixed(3)}`
      : null,
  ]
    .filter(Boolean)
    .join(", ");

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
                }, ${ariaMetricText}`
              : undefined
          }
        >
          <title>
            Training metrics by epoch. Solid line is F1, dashed line is
            precision, dotted line is recall, and the lower strip shows train
            and evaluation loss.
          </title>

          {showPostBestRegion && (
            <rect
              x={bx}
              y={SVG_PAD_TOP - 4}
              width={postBestWidth}
              height={LOSS_STRIP_BOTTOM - SVG_PAD_TOP + 4}
              className={styles.sparklinePostBestRegion}
            >
              <title>
                Epochs after the best validation F1. Watch for train loss
                falling while validation metrics stop improving.
              </title>
            </rect>
          )}

          <g className={styles.sparklineMetricLegend} aria-hidden="true">
            <line x1={SVG_PAD_X} x2={SVG_PAD_X + 15} y1={8} y2={8} />
            <text x={SVG_PAD_X + 19} y={11}>
              F1
            </text>
            <line
              x1={SVG_PAD_X + 45}
              x2={SVG_PAD_X + 60}
              y1={8}
              y2={8}
              className={styles.sparklinePrecisionSample}
            />
            <text x={SVG_PAD_X + 64} y={11}>
              P
            </text>
            <line
              x1={SVG_PAD_X + 84}
              x2={SVG_PAD_X + 99}
              y1={8}
              y2={8}
              className={styles.sparklineRecallSample}
            />
            <text x={SVG_PAD_X + 103} y={11}>
              R
            </text>
          </g>

          {/* Convergence curve */}
          {precisionPath && (
            <path
              d={precisionPath}
              className={styles.sparklinePrecisionPath}
              vectorEffect="non-scaling-stroke"
            />
          )}

          {recallPath && (
            <path
              d={recallPath}
              className={styles.sparklineRecallPath}
              vectorEffect="non-scaling-stroke"
            />
          )}

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
            y2={LOSS_STRIP_BOTTOM}
            className={styles.sparklineActiveLine}
            vectorEffect="non-scaling-stroke"
          />

          <line
            x1={SVG_PAD_X}
            x2={svgWidth - SVG_PAD_X}
            y1={LOSS_STRIP_TOP - 7}
            y2={LOSS_STRIP_TOP - 7}
            className={styles.sparklineLossDivider}
            vectorEffect="non-scaling-stroke"
          />

          {lossPaths && (lossPaths.hasTrain || lossPaths.hasEval) && (
            <g className={styles.sparklineLossGroup}>
              {lossPaths.hasEval && (
                <path
                  d={lossPaths.eval}
                  className={styles.sparklineEvalLossPath}
                  vectorEffect="non-scaling-stroke"
                />
              )}
              {lossPaths.hasTrain && (
                <path
                  d={lossPaths.train}
                  className={styles.sparklineTrainLossPath}
                  vectorEffect="non-scaling-stroke"
                />
              )}
              <text
                x={SVG_PAD_X}
                y={LOSS_STRIP_TOP + 3}
                className={styles.sparklineLossLabel}
              >
                loss
              </text>
            </g>
          )}

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
      <SynthesisEvidenceStrip synthesis={synthesis} />
    </>
  );
};

interface SynthesisEvidenceStripProps {
  synthesis?: TrainingSynthesisSummary | null;
}

const formatCount = (value?: number | null): string =>
  Number.isFinite(value) ? (value as number).toLocaleString() : "—";

const formatSimilarity = (value?: number | null): string =>
  Number.isFinite(value) ? (value as number).toFixed(2) : "—";

const formatPercent = (value?: number | null): string =>
  Number.isFinite(value) ? `${Math.round((value as number) * 100)}%` : "—";

const SYNTHETIC_REJECTION_REASON_LABELS: Record<string, string> = {
  add_item_base_category_missing: "wrong section",
  add_item_catalog_category_mismatch: "catalog category mismatch",
  add_item_catalog_missing_category_evidence: "catalog missing category",
  add_item_catalog_not_cross_receipt_grounded: "catalog ungrounded item",
  add_item_category_mismatch: "category mismatch",
  add_item_missing_category_evidence: "missing category",
  add_item_not_cross_receipt_grounded: "ungrounded item",
  add_item_placement_base_category_missing: "placement missing section",
  add_item_placement_category_mismatch: "placement category mismatch",
  accepted_synthetic_mix_high_risk: "high-risk merchant mix",
  accepted_synthetic_mix_single_merchant_high_risk: "single merchant mix",
  accepted_synthetic_mix_top_merchant_high_risk: "top merchant dominates",
  bundle_not_ready: "bundle not ready",
  bundle_training_not_ready: "bundle training hold",
  invalid_arithmetic_reconciliation: "bad arithmetic",
  below_real_structure_baseline: "below real baseline",
  low_category_sequence_similarity: "weak category order",
  low_category_set_similarity: "weak category match",
  low_line_step_similarity: "weak row spacing",
  low_price_column_similarity: "weak price column",
  low_structure_similarity: "low similarity",
  low_token_count_similarity: "weak token count",
  merchant_operation_synthetic_cap: "operation cap",
  merchant_synthesis_not_ready: "merchant not ready",
  merchant_synthetic_cap: "merchant cap",
  missing_arithmetic_reconciliation: "missing arithmetic",
  missing_base_receipt_lineage: "missing base receipt",
  missing_metadata: "missing metadata",
  missing_structure_similarity: "missing similarity",
  replace_field_format_mismatch: "format mismatch",
  replace_field_insufficient_observations: "few field examples",
  replace_field_invalid_value: "bad field value",
  replace_field_label_mismatch: "label mismatch",
  replace_field_missing_evidence: "missing field evidence",
  replace_field_missing_format: "missing field format",
  replace_field_not_mutable: "field not mutable",
  replace_field_unsupported_label: "unsupported field",
  replace_field_unstable_geometry: "unstable field geometry",
  single_merchant_accepted: "single merchant accepted",
  top_merchant_share_ge_80pct: "top merchant >=80%",
};

const formatSyntheticRejectionReason = (reason: string): string =>
  SYNTHETIC_REJECTION_REASON_LABELS[reason] ||
  reason.replaceAll("_", " ");

const summarizeSyntheticRejections = (
  reasons: Record<string, number> | undefined,
  rejectedCount: number
): { label: string; title?: string } => {
  const entries = Object.entries(reasons || {})
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  const title = entries.length
    ? entries
        .map(
          ([reason, count]) =>
            `${formatCount(count)} ${formatSyntheticRejectionReason(reason)}`
        )
        .join(", ")
    : undefined;
  const cappedCount = entries.reduce(
    (sum, [reason, count]) =>
      reason === "merchant_synthetic_cap" ||
      reason === "merchant_operation_synthetic_cap"
        ? sum + count
        : sum,
    0
  );
  if (cappedCount > 0) {
    const label =
      cappedCount === rejectedCount
        ? `${formatCount(rejectedCount)} capped`
        : `${formatCount(rejectedCount)} rejected (${formatCount(cappedCount)} capped)`;
    return { label, title };
  }
  return { label: `${formatCount(rejectedCount)} rejected`, title };
};

const formatCategoryName = (category: string): string =>
  category
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");

const formatFieldName = (field: string): string => formatCategoryName(field);

const formatOperationName = (operation: string): string =>
  operation === "replace_field"
    ? "Field edits"
    : formatCategoryName(operation);

type TrainingSynthesisMerchantGap = NonNullable<
  TrainingSynthesisMerchantGapSummary["merchants"]
>[number];

const summarizeCategoryCounts = (
  counts: Record<string, number> | undefined
): { label: string; title?: string } | null => {
  const entries = Object.entries(counts || {})
    .filter(([category, count]) => category && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return null;
  const [topCategory, topCount] = entries[0];
  return {
    label: `${formatCategoryName(topCategory)}: ${formatCount(topCount)}`,
    title: entries
      .map(([category, count]) => `${formatCategoryName(category)}: ${formatCount(count)}`)
      .join(", "),
  };
};

const summarizeFieldReplacementCounts = (
  counts: Record<string, number> | undefined,
  examples: TrainingSynthesisSummary["candidate_examples"] | undefined
): { label: string; title?: string } | null => {
  const merged = new Map<string, number>();
  Object.entries(counts || {}).forEach(([field, count]) => {
    if (field && count > 0) merged.set(field, count);
  });
  (examples || []).forEach((example) => {
    if (example.operation !== "replace_field" || !example.field_label) return;
    if (!merged.has(example.field_label)) {
      merged.set(example.field_label, 1);
    }
  });

  const entries = Array.from(merged.entries()).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  if (!entries.length) return null;

  const examplesByField = new Map(
    (examples || [])
      .filter((example) => example.field_label)
      .map((example) => [example.field_label as string, example])
  );
  const label = entries
    .slice(0, 2)
    .map(([field, count]) => `${formatFieldName(field)}: ${formatCount(count)}`)
    .join(", ");
  const title = entries
    .map(([field, count]) => {
      const example = examplesByField.get(field);
      const replacement =
        example?.old_text && example?.new_text
          ? ` (${example.old_text} -> ${example.new_text}${
              example.field_format ? `, ${example.field_format}` : ""
            })`
          : "";
      return `${formatFieldName(field)}: ${formatCount(count)}${replacement}`;
    })
    .join(", ");

  return {
    label:
      entries.length > 2
        ? `${label}, +${formatCount(entries.length - 2)}`
        : label,
    title,
  };
};

const summarizeContractCoverage = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const contracts = synthesis.merchant_synthesis_contracts || [];
  const total =
    synthesis.contract_merchant_count ??
    (contracts.length ? contracts.length : null);
  const ready =
    synthesis.contract_ready_merchant_count ??
    (contracts.length
      ? contracts.filter((contract) => contract.status === "ready").length
      : null);
  const operationEntries = Object.entries(synthesis.contract_operation_counts || {})
    .filter(([operation, count]) => operation && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const operationCoverage = synthesis.quality_report?.operation_coverage;
  const acceptedOperationCoverage =
    synthesis.quality_report?.accepted_operation_coverage ??
    synthesis.accepted_operation_coverage ??
    synthesis.synthetic_accepted_operation_coverage;

  if (total == null && !operationEntries.length && !acceptedOperationCoverage) {
    return null;
  }

  const operationTitle = operationEntries
    .map(
      ([operation, count]) =>
        `${formatOperationName(operation)}: ${formatCount(count)}`
    )
    .join(", ");
  const readyOperations = operationCoverage?.ready_operation_count;
  const totalOperations = operationCoverage?.operation_count;
  const coverageTitle =
    readyOperations != null && totalOperations
      ? `Operations ready: ${formatCount(readyOperations)} / ${formatCount(totalOperations)}`
      : null;
  const acceptedReadyOperations =
    acceptedOperationCoverage?.accepted_ready_operation_count;
  const readyAcceptedTotal = acceptedOperationCoverage?.ready_operation_count;
  const acceptedCoverageTitle =
    acceptedReadyOperations != null && readyAcceptedTotal
      ? `Ready ops accepted: ${formatCount(acceptedReadyOperations)} / ${formatCount(readyAcceptedTotal)}`
      : null;
  const uncoveredTitle = (
    acceptedOperationCoverage?.uncovered_ready_operations || []
  ).length
    ? `Uncovered ready ops: ${(
        acceptedOperationCoverage?.uncovered_ready_operations || []
      )
        .map(formatOperationName)
        .join(", ")}`
    : null;
  const trainingReadyTitle =
    synthesis.quality_report?.training_ready === false
      ? `Training hold: ${(synthesis.quality_report.training_ready_reasons || [])
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null;
  return {
    label:
      total != null
        ? `${formatCount(ready ?? 0)} / ${formatCount(total)} ready`
        : acceptedReadyOperations != null && readyAcceptedTotal != null
          ? `${formatCount(acceptedReadyOperations)} / ${formatCount(readyAcceptedTotal)} accepted`
        : "—",
    title: [
      operationTitle,
      coverageTitle,
      acceptedCoverageTitle,
      uncoveredTitle,
      trainingReadyTitle,
    ]
      .filter((value): value is string => Boolean(value))
      .join(" | ") || undefined,
  };
};

const describeModelGuidanceStatus = (
  freshnessGate:
    | NonNullable<
        NonNullable<TrainingSynthesisSummary["quality_report"]>["quality_gates"]
      >["llm_model_freshness_gate"]
    | undefined,
  verifiedAt?: string | null
): { guidanceStatus: string; latestModelStatus: string } => {
  if (freshnessGate?.requires_current_model_guidance === false) {
    const localStatus = "not required for local-only run";
    return {
      guidanceStatus: localStatus,
      latestModelStatus: verifiedAt
        ? `${localStatus}; verified ${verifiedAt}`
        : localStatus,
    };
  }

  if (freshnessGate?.passed === false) {
    return {
      guidanceStatus: "stale or missing",
      latestModelStatus: "stale or missing",
    };
  }

  if (
    freshnessGate?.requires_current_model_guidance === true &&
    freshnessGate.passed === true
  ) {
    return {
      guidanceStatus: "fresh",
      latestModelStatus: verifiedAt
        ? `verified ${verifiedAt}`
        : "verification date missing",
    };
  }

  if (freshnessGate?.passed === true) {
    return {
      guidanceStatus: "freshness gate passed",
      latestModelStatus: verifiedAt
        ? `freshness gate passed; verified ${verifiedAt}`
        : "freshness gate passed; verification date missing",
    };
  }

  return {
    guidanceStatus: "verification not reported",
    latestModelStatus: verifiedAt ? `verified ${verifiedAt}` : "verification missing",
  };
};

const summarizeLlmExecution = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const execution =
    synthesis.llm_execution ?? synthesis.quality_report?.summary?.llm_execution;
  if (!execution) return null;

  const entries = Object.entries(execution.mode_counts || {})
    .filter(([mode, count]) => mode && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const apiAllowed = execution.api_call_allowed_count ?? 0;
  const disabled = execution.paid_llm_disabled_count ?? 0;
  if (!entries.length && !apiAllowed && !disabled) return null;

  const primaryMode = entries[0]?.[0];
  let label = "Unknown";
  if (apiAllowed > 0) {
    label = "LLM assisted";
  } else if (primaryMode === "deterministic_fallback" || disabled > 0) {
    label = "Local only";
  } else if (primaryMode) {
    label = formatFieldName(primaryMode);
  }
  const modeTitle = entries
    .map(([mode, count]) => `${formatFieldName(mode)}: ${formatCount(count)}`)
    .join(", ");
  const freshnessGate = synthesis.quality_report?.quality_gates
    ?.llm_model_freshness_gate;
  const modelTitle = (execution.configured_models || []).length
    ? `Models: ${(execution.configured_models || []).join(", ")}`
    : null;
  const guidanceStatus = describeModelGuidanceStatus(
    freshnessGate,
    execution.latest_model_verified_at
  );
  const latestModelTitle = (execution.latest_openai_models || []).length
    ? `Latest OpenAI guidance: ${(execution.latest_openai_models || []).join(
        ", "
      )} (${guidanceStatus.latestModelStatus})`
    : null;
  const sourceTitle = (execution.latest_model_sources || []).length
    ? `Latest model source: ${(execution.latest_model_sources || []).join(", ")}`
    : null;
  const verifiedTitle = execution.latest_model_verified_at
    ? `Latest model verified: ${execution.latest_model_verified_at}`
    : null;
  const freshnessTitle = freshnessGate
    ? `Model guidance: ${guidanceStatus.guidanceStatus}${
        freshnessGate.latest_model_age_days != null
          ? ` (${formatCount(freshnessGate.latest_model_age_days)}d old`
          : ""
      }${
        freshnessGate.max_age_days != null
          ? `${freshnessGate.latest_model_age_days != null ? ", " : " ("}max ${formatCount(freshnessGate.max_age_days)}d`
          : ""
      }${
        freshnessGate.latest_model_age_days != null ||
        freshnessGate.max_age_days != null
          ? ")"
          : ""
      }`
    : null;
  const freshnessHoldTitle =
    freshnessGate?.requires_current_model_guidance && freshnessGate.passed === false
      ? `Training hold: ${formatSyntheticRejectionReason(
          freshnessGate.reason || "refresh_latest_model_guidance_before_synthesis"
        )}`
      : null;

  return {
    label,
    title:
      [
        modeTitle,
        `Paid LLM disabled: ${formatCount(disabled)}`,
        `API allowed: ${formatCount(apiAllowed)}`,
        modelTitle,
        latestModelTitle,
        sourceTitle,
        verifiedTitle,
        freshnessTitle,
        freshnessHoldTitle,
      ]
        .filter((value): value is string => Boolean(value))
        .join(" | ") || undefined,
  };
};

const summarizeCountRecord = (
  counts: Record<string, number> | undefined,
  formatter: (value: string) => string,
  limit = 3
): string | null => {
  const entries = Object.entries(counts || {})
    .filter(([key, count]) => key && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return null;
  return entries
    .slice(0, limit)
    .map(([key, count]) => `${formatter(key)}: ${formatCount(count)}`)
    .join(", ");
};

const hasMerchantGap = (merchant: TrainingSynthesisMerchantGap): boolean =>
  merchant.status === "blocked" ||
  Boolean(merchant.blockers?.length) ||
  Boolean(merchant.limitations?.length) ||
  Boolean(merchant.missing_operations?.length);

const summarizeMerchantGaps = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const summary = synthesis.quality_report?.merchant_gap_summary;
  if (!summary) return null;

  const merchants = summary.merchants || [];
  const derivedGapCount = merchants.filter(hasMerchantGap).length;
  const gapCount = summary.merchant_gap_count ?? derivedGapCount;
  const blockedCount =
    summary.blocked_merchant_count ??
    merchants.filter((merchant) => merchant.status === "blocked").length;
  const total = synthesis.merchant_count ?? (merchants.length || null);
  const topBlockers = summarizeCountRecord(
    summary.top_blockers,
    formatSyntheticRejectionReason
  );
  const topLimitations = summarizeCountRecord(
    summary.top_limitations,
    formatSyntheticRejectionReason
  );
  const missingOperations = Array.from(
    new Set(merchants.flatMap((merchant) => merchant.missing_operations || []))
  ).filter(Boolean);
  const title = [
    topBlockers ? `Blockers: ${topBlockers}` : null,
    topLimitations ? `Limitations: ${topLimitations}` : null,
    missingOperations.length
      ? `Missing ops: ${missingOperations.map(formatOperationName).join(", ")}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  if (gapCount > 0) {
    return {
      label: `${formatCount(gapCount)}${total ? ` / ${formatCount(total)}` : ""} gaps`,
      title: title || undefined,
    };
  }
  if (blockedCount > 0) {
    return {
      label: `${formatCount(blockedCount)} blocked`,
      title: title || undefined,
    };
  }
  return {
    label: "No gaps",
    title: title || undefined,
  };
};

const summarizeOperationReadiness = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string; warning: boolean } | null => {
  const merchants = synthesis.quality_report?.merchants || [];
  const operationRows = merchants.flatMap((merchant) =>
    (merchant.operation_readiness || []).map((row) => ({
      merchant: merchant.merchant_name || "Unknown merchant",
      ...row,
    }))
  );
  if (!operationRows.length) return null;

  const supportedRows = operationRows.filter((row) => row.supported);
  const readyRows = supportedRows.filter((row) => row.ready);
  const reusableReadyRows = readyRows.filter(
    (row) => (row.evidence_candidate_count ?? 0) > 0
  );
  const missingOperations = Array.from(
    new Set(merchants.flatMap((merchant) => merchant.missing_operations || []))
  ).filter(Boolean);
  const nextActions = Array.from(
    new Set(merchants.flatMap((merchant) => merchant.next_synthesis_actions || []))
  ).filter(Boolean);
  const nextActionCounts = summarizeCountRecord(
    synthesis.quality_report?.summary?.next_synthesis_action_counts,
    formatSyntheticRejectionReason,
    6
  );
  const actionNeedsAttention = nextActions.some(
    (action) => action.startsWith("collect_") || action.startsWith("resolve_")
  );
  const blockedRows = operationRows
    .filter((row) => !row.ready)
    .slice(0, 5)
    .map((row) => {
      const blockers = (row.blockers || [])
        .slice(0, 2)
        .map(formatSyntheticRejectionReason)
        .join(", ");
      return `${row.merchant} ${formatOperationName(row.operation || "unknown")}${
        blockers ? ` (${blockers})` : ""
      }`;
    });

  const title = [
    `Supported operation rows: ${formatCount(supportedRows.length)} / ${formatCount(operationRows.length)}`,
    `Contract-ready rows: ${formatCount(readyRows.length)} / ${formatCount(supportedRows.length)}`,
    `Ready with reusable evidence: ${formatCount(reusableReadyRows.length)} / ${formatCount(readyRows.length)}`,
    missingOperations.length
      ? `Missing ops: ${missingOperations.map(formatOperationName).join(", ")}`
      : null,
    nextActions.length
      ? `Next actions: ${nextActions
          .slice(0, 4)
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
    nextActionCounts ? `Action backlog: ${nextActionCounts}` : null,
    blockedRows.length ? `Blocked rows: ${blockedRows.join(" | ")}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  const label = readyRows.length
    ? `${formatCount(reusableReadyRows.length)} / ${formatCount(readyRows.length)} reusable`
    : supportedRows.length
      ? `0 / ${formatCount(supportedRows.length)} ready`
      : "No supported ops";

  return {
    label,
    title: title || undefined,
    warning:
      supportedRows.length === 0 ||
      readyRows.length < supportedRows.length ||
      reusableReadyRows.length < readyRows.length ||
      missingOperations.length > 0 ||
      actionNeedsAttention,
  };
};

const summarizeReasonList = (
  merchants: TrainingSynthesisSourceQualityMerchant[],
  field: "blockers" | "limitations"
): string | null => {
  const counts = new Map<string, number>();
  merchants.forEach((merchant) => {
    (merchant[field] || []).forEach((reason) => {
      if (!reason) return;
      counts.set(reason, (counts.get(reason) || 0) + 1);
    });
  });
  const entries = Array.from(counts.entries()).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  if (!entries.length) return null;
  return entries
    .slice(0, 3)
    .map(([reason, count]) => `${formatSyntheticRejectionReason(reason)}: ${formatCount(count)}`)
    .join(", ");
};

const summarizeSourceLabels = (
  merchants: TrainingSynthesisSourceQualityMerchant[]
): string | null => {
  const counts = new Map<string, number>();
  merchants.forEach((merchant) => {
    Object.entries(merchant.top_labels || {}).forEach(([label, count]) => {
      if (!label || !count) return;
      counts.set(label, (counts.get(label) || 0) + count);
    });
  });
  const entries = Array.from(counts.entries()).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  if (!entries.length) return null;
  return entries
    .slice(0, 4)
    .map(([label, count]) => `${formatFieldName(label)}: ${formatCount(count)}`)
    .join(", ");
};

const sourceQualityMerchantMap = (
  synthesis: TrainingSynthesisSummary
): Map<string, TrainingSynthesisSourceQualityMerchant> =>
  new Map(
    (synthesis.source_receipt_quality?.merchants || [])
      .filter((merchant) => merchant.merchant_name)
      .map((merchant) => [merchant.merchant_name as string, merchant])
  );

const reportMerchantSourceQuality = (
  merchant: TrainingSynthesisQualityMerchant
): TrainingSynthesisSourceQualityMerchant | undefined => {
  if (
    !merchant.source_quality_status &&
    merchant.source_quality_receipt_count == null &&
    merchant.source_quality_labeled_word_count == null
  ) {
    return undefined;
  }
  return {
    merchant_name: merchant.merchant_name,
    status: merchant.source_quality_status,
    receipt_count: merchant.source_quality_receipt_count,
    labeled_word_count: merchant.source_quality_labeled_word_count,
    receipts_with_line_item_labels:
      merchant.source_quality_receipts_with_line_item_labels,
    receipts_with_grand_total_label:
      merchant.source_quality_receipts_with_grand_total_label,
    receipts_with_date_or_time_label:
      merchant.source_quality_receipts_with_date_or_time_label,
    text_structure_status: merchant.source_quality_text_structure_status,
    line_item_like_text_line_count:
      merchant.source_quality_line_item_like_text_line_count,
    total_like_text_line_count:
      merchant.source_quality_total_like_text_line_count,
    limitations: Array.from(
      new Set([
        ...(merchant.source_quality_limitations || []),
        ...(merchant.limitations || []),
      ])
    ),
    requires_label_validation:
      merchant.source_quality_requires_label_validation,
    blockers: Array.from(
      new Set(Object.values(merchant.source_quality_operation_blockers || {}))
    ),
  };
};

const summarizeSourceQualityMerchant = (
  merchant?: TrainingSynthesisSourceQualityMerchant
): { label: string; title?: string } | null => {
  if (!merchant) return null;
  const receiptCount = merchant.receipt_count ?? 0;
  const labeledWordCount = merchant.labeled_word_count ?? 0;
  const lineItemReceipts = merchant.receipts_with_line_item_labels ?? 0;
  const totalReceipts = receiptCount || merchant.receipts_with_labels || 0;
  const labelValidationRequired = (merchant.limitations || []).includes(
    "unlabeled_text_requires_label_validation"
  );
  const recoverableUnlabeled =
    merchant.requires_label_validation === true ||
    merchant.text_structure_status === "recoverable_unlabeled_text" ||
    labelValidationRequired;
  const title = [
    `${formatReadinessStatus(merchant.status)} source receipts`,
    `Receipts: ${formatCount(receiptCount)}`,
    `Labeled words: ${formatCount(labeledWordCount)}`,
    recoverableUnlabeled
      ? `Recoverable OCR: ${formatCount(
          merchant.total_like_text_line_count ?? 0
        )} total-like anchors, ${formatCount(
          merchant.line_item_like_text_line_count ?? 0
        )} line-item-like rows`
      : null,
    `Line item labels: ${formatCount(lineItemReceipts)} / ${formatCount(totalReceipts)}`,
    merchant.receipts_with_grand_total_label != null
      ? `Grand total labels: ${formatCount(merchant.receipts_with_grand_total_label)}`
      : null,
    merchant.receipts_with_date_or_time_label != null
      ? `Date/time labels: ${formatCount(merchant.receipts_with_date_or_time_label)}`
      : null,
    merchant.blockers?.length
      ? `Blockers: ${merchant.blockers.map(formatSyntheticRejectionReason).join(", ")}`
      : null,
    merchant.limitations?.length
      ? `Limitations: ${merchant.limitations.map(formatSyntheticRejectionReason).join(", ")}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");
  return {
    label: recoverableUnlabeled
      ? `${formatCount(receiptCount)} src · ${formatCount(labeledWordCount)} labels · validate`
      : `${formatCount(receiptCount)} src · ${formatCount(labeledWordCount)} labels`,
    title,
  };
};

const summarizeSourceReceiptQuality = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const quality = synthesis.source_receipt_quality;
  if (!quality) return null;
  const merchants = quality.merchants || [];
  const merchantCount = quality.merchant_count ?? merchants.length;
  const usable =
    quality.usable_merchant_count ??
    quality.status_counts?.usable ??
    merchants.filter((merchant) => merchant.status === "usable").length;
  const limited =
    quality.limited_merchant_count ??
    quality.status_counts?.limited ??
    merchants.filter((merchant) => merchant.status === "limited").length;
  const blocked =
    quality.blocked_merchant_count ??
    quality.status_counts?.blocked ??
    merchants.filter((merchant) => merchant.status === "blocked").length;
  const receiptCount =
    quality.receipt_count ??
    merchants.reduce((sum, merchant) => sum + (merchant.receipt_count || 0), 0);
  const labeledWordCount =
    quality.labeled_word_count ??
    merchants.reduce(
      (sum, merchant) => sum + (merchant.labeled_word_count || 0),
      0
    );
  const blockers = summarizeReasonList(merchants, "blockers");
  const limitations = summarizeReasonList(merchants, "limitations");
  const labels = summarizeSourceLabels(merchants);
  const title = [
    `Receipts: ${formatCount(receiptCount)}`,
    `Labeled words: ${formatCount(labeledWordCount)}`,
    `Usable: ${formatCount(usable)}`,
    limited ? `Limited: ${formatCount(limited)}` : null,
    blocked ? `Blocked: ${formatCount(blocked)}` : null,
    blockers ? `Blockers: ${blockers}` : null,
    limitations ? `Limitations: ${limitations}` : null,
    labels ? `Top labels: ${labels}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");
  return {
    label:
      merchantCount != null
        ? `${formatCount(usable)} / ${formatCount(merchantCount)} usable`
        : `${formatCount(usable)} usable`,
    title,
  };
};

const summarizeCandidateQuality = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const byId = new Map<string, TrainingSynthesisCandidateQuality>();
  (synthesis.candidate_examples || []).forEach((example, index) => {
    if (example.candidate_quality) {
      byId.set(example.candidate_id || `candidate-${index}`, example.candidate_quality);
    }
  });
  (synthesis.quality_report?.merchants || []).forEach((merchant) => {
    (merchant.accepted_examples || []).forEach((example, index) => {
      if (example.candidate_quality) {
        byId.set(
          example.candidate_id || `${merchant.merchant_name || "merchant"}-${index}`,
          example.candidate_quality
        );
      }
    });
  });
  const qualities = Array.from(byId.values()).filter(
    (quality): quality is TrainingSynthesisCandidateQuality =>
      Boolean(quality) && Number.isFinite(quality.score)
  );
  const aggregate =
    synthesis.accepted_candidate_quality ??
    synthesis.quality_report?.summary?.accepted_candidate_quality;
  const aggregateComponents =
    synthesis.accepted_candidate_quality_components ??
    synthesis.quality_report?.summary?.accepted_candidate_quality_components;

  if (!qualities.length && !aggregate) return null;
  if (!qualities.length && aggregate) {
    const componentTitle = Object.entries(aggregateComponents || {})
      .filter(([, value]) => Number.isFinite(value.avg))
      .sort(
        (a, b) =>
          (a[1].avg as number) - (b[1].avg as number) ||
          a[0].localeCompare(b[0])
      )
      .slice(0, 4)
      .map(
        ([name, value]) =>
          `${formatFieldName(name)} ${formatSimilarity(value.avg)}`
      )
      .join(", ");
    return {
      label:
        aggregate.avg != null
          ? `avg ${formatSimilarity(aggregate.avg)}`
          : `${formatCount(aggregate.count)} scored`,
      title:
        [
          aggregate.count != null ? `Scored candidates: ${formatCount(aggregate.count)}` : null,
          aggregate.min != null ? `Min: ${formatSimilarity(aggregate.min)}` : null,
          aggregate.max != null ? `Max: ${formatSimilarity(aggregate.max)}` : null,
          componentTitle ? `Weakest components: ${componentTitle}` : null,
        ]
          .filter((value): value is string => Boolean(value))
          .join(" | ") || undefined,
    };
  }

  const highFidelityCount = qualities.filter(
    (quality) => quality.high_fidelity
  ).length;
  const avgScore =
    qualities.reduce((sum, quality) => sum + (quality.score as number), 0) /
    qualities.length;
  const componentTotals = new Map<string, { sum: number; count: number }>();
  qualities.forEach((quality) => {
    Object.entries(quality.components || {}).forEach(([name, value]) => {
      if (!Number.isFinite(value)) return;
      const current = componentTotals.get(name) || { sum: 0, count: 0 };
      componentTotals.set(name, {
        sum: current.sum + value,
        count: current.count + 1,
      });
    });
  });
  const componentTitle = Array.from(componentTotals.entries())
    .map(([name, value]) => ({
      name,
      avg: value.count ? value.sum / value.count : 0,
    }))
    .sort((a, b) => a.avg - b.avg || a.name.localeCompare(b.name))
    .slice(0, 4)
    .map(({ name, avg }) => `${formatFieldName(name)} ${formatSimilarity(avg)}`)
    .join(", ");
  const structureGateIssues = qualities.flatMap((quality) => {
    const gate = quality.structure_gate;
    if (!gate || gate.passed) return [];
    const failed = Object.entries(gate.failed_components || {}).map(
      ([component, detail]) =>
        `${formatFieldName(component)} ${formatSimilarity(detail.value)} < ${formatSimilarity(detail.threshold)}`
    );
    const missing = (gate.missing_components || []).map(
      (component) => `${formatFieldName(component)} missing`
    );
    return [...failed, ...missing];
  });
  const gateTitle = Array.from(new Set(structureGateIssues)).slice(0, 4).join(", ");

  return {
    label: `${formatCount(highFidelityCount)} / ${formatCount(qualities.length)} high`,
    title:
      [
        `Average fidelity: ${formatSimilarity(avgScore)}`,
        componentTitle ? `Weakest components: ${componentTitle}` : null,
        gateTitle ? `Structure gate issues: ${gateTitle}` : null,
      ]
        .filter((value): value is string => Boolean(value))
        .join(" | ") || undefined,
  };
};

const summarizeSourceLineage = (
  synthesis: TrainingSynthesisSummary
): {
  label: string;
  statusLabel: string;
  title?: string;
  warning: boolean;
} | null => {
  const lineage =
    synthesis.accepted_source_lineage ??
    synthesis.quality_report?.summary?.accepted_source_lineage;
  if (!lineage) return null;

  const schemaVersion = lineage.schema_version ?? null;
  const supportedSchema = schemaVersion === "accepted-source-lineage-v1";
  const observed = lineage.observed_candidate_count ?? null;
  const hasExpected = lineage.expected_candidate_count != null;
  const expected = lineage.expected_candidate_count ?? null;
  const candidateCount = lineage.candidate_count ?? null;
  const baseCoverage = lineage.with_base_receipt_count ?? null;
  const acceptedCandidateCount =
    synthesis.quality_report?.summary?.accepted_count ??
    synthesis.bundle_candidates_accepted ??
    synthesis.synthetic_candidates_accepted ??
    null;
  const sourceCount = lineage.source_receipt_key_count ?? 0;
  const coverageStatus = (lineage.coverage_status || "").toLowerCase();
  const isCompleteStatus =
    coverageStatus === "complete" || coverageStatus === "full";
  const isCoverageSampled = coverageStatus === "sampled";
  const unverifiedCoverageStatus = !isCompleteStatus;
  const nonAuthoritative = lineage.authoritative === false;
  const countMismatch =
    observed != null && expected != null && observed !== expected;
  const missingObservedCoverage =
    observed == null && (expected != null || candidateCount != null);
  const hasAcceptedCandidateTarget =
    acceptedCandidateCount != null && acceptedCandidateCount > 0;
  const baseCoverageUnavailable =
    supportedSchema &&
    isCompleteStatus &&
    !nonAuthoritative &&
    hasAcceptedCandidateTarget &&
    baseCoverage == null;
  const baseCoverageMismatch =
    supportedSchema &&
    isCompleteStatus &&
    !nonAuthoritative &&
    hasAcceptedCandidateTarget &&
    baseCoverage != null &&
    baseCoverage !== acceptedCandidateCount;
  const hasCompleteBaseCoverage =
    supportedSchema &&
    isCompleteStatus &&
    !nonAuthoritative &&
    hasAcceptedCandidateTarget &&
    baseCoverage != null &&
    baseCoverage === acceptedCandidateCount;
  // Candidate coverage counts can be bounded diagnostics; exact accepted
  // base-link coverage is the training gate surfaced by this badge.
  const blockingBaseCoverage =
    baseCoverageUnavailable || baseCoverageMismatch;
  const blockingCountMismatch = countMismatch && !hasCompleteBaseCoverage;
  const blockingMissingObservedCoverage =
    missingObservedCoverage && !hasCompleteBaseCoverage;
  const statusPrefix = isCoverageSampled
    ? "sampled"
    : nonAuthoritative
      ? "not auth"
      : blockingCountMismatch
        ? "gap"
        : blockingBaseCoverage
          ? "base"
          : unverifiedCoverageStatus
            ? "review"
            : null;
  const warning =
    !supportedSchema ||
    nonAuthoritative ||
    isCoverageSampled ||
    unverifiedCoverageStatus ||
    blockingCountMismatch ||
    blockingMissingObservedCoverage ||
    blockingBaseCoverage ||
    Boolean(lineage.coverage_warning) ||
    Boolean(lineage.source_receipt_keys_truncated);

  let label = "reported";
  if (!supportedSchema) {
    label = "schema review";
  } else if (
    statusPrefix === "base" &&
    baseCoverage != null &&
    acceptedCandidateCount != null
  ) {
    label = `base ${formatCount(baseCoverage)} / ${formatCount(
      acceptedCandidateCount
    )}`;
  } else if (statusPrefix === "base" && acceptedCandidateCount != null) {
    label = `base expected ${formatCount(acceptedCandidateCount)}`;
  } else if (statusPrefix && observed != null && expected != null) {
    label = `${statusPrefix} ${formatCount(observed)} / ${formatCount(expected)}`;
  } else if (statusPrefix && observed != null) {
    label = `${statusPrefix} ${formatCount(observed)}`;
  } else if (statusPrefix && expected != null) {
    label = `${statusPrefix} expected ${formatCount(expected)}`;
  } else if (statusPrefix && candidateCount != null) {
    label = `${statusPrefix} expected ${formatCount(candidateCount)}`;
  } else if (hasCompleteBaseCoverage && baseCoverage != null) {
    label = `${formatCount(baseCoverage)} base-linked`;
  } else if (observed != null) {
    label = `${formatCount(observed)} candidate${observed === 1 ? "" : "s"}`;
  } else if (candidateCount != null) {
    label = `expected ${formatCount(candidateCount)}`;
  } else if (sourceCount) {
    label = `${formatCount(sourceCount)} source receipts`;
  } else if (warning) {
    label = "review";
  }

  let statusLabel = "Lineage";
  if (nonAuthoritative) {
    statusLabel = "Lineage (not auth)";
  } else if (isCoverageSampled) {
    statusLabel = "Lineage (sampled)";
  } else if (!supportedSchema) {
    statusLabel = "Lineage (schema)";
  } else if (blockingCountMismatch || blockingMissingObservedCoverage) {
    statusLabel = "Lineage (gap)";
  } else if (blockingBaseCoverage) {
    statusLabel = "Lineage (base)";
  } else if (unverifiedCoverageStatus) {
    statusLabel = "Lineage (review)";
  }

  const flagTitle = (
    name: keyof Pick<
      TrainingSynthesisAcceptedSourceLineage,
      | "with_base_receipt_count"
      | "with_cross_receipt_item_count"
      | "with_category_evidence_count"
      | "with_nearest_real_structure_count"
      | "with_layout_integrity_count"
      | "with_arithmetic_reconciliation_count"
      | "with_selection_evidence_count"
    >,
    label: string
  ) =>
    lineage[name] != null
      ? `${label}: ${formatCount(lineage[name] as number)}`
      : null;

  let statusTitle = "Source lineage covers accepted candidates";
  if (!supportedSchema) {
    statusTitle = `Source lineage schema is ${
      schemaVersion ? `unsupported (${schemaVersion})` : "missing"
    }`;
  } else if (isCoverageSampled && nonAuthoritative) {
    statusTitle = "Source lineage is sampled and not authoritative";
  } else if (isCoverageSampled) {
    statusTitle = "Source lineage is sampled";
  } else if (nonAuthoritative) {
    statusTitle = "Source lineage is not authoritative";
  } else if (blockingCountMismatch) {
    statusTitle = "Source lineage count does not match expected coverage";
  } else if (blockingMissingObservedCoverage) {
    statusTitle = "Source lineage observed coverage is unavailable";
  } else if (baseCoverageUnavailable) {
    statusTitle = "Source lineage base evidence is unavailable";
  } else if (baseCoverageMismatch) {
    statusTitle = "Source lineage base evidence does not match accepted coverage";
  } else if (unverifiedCoverageStatus) {
    statusTitle = "Source lineage coverage status needs review";
  }

  const title = [
    statusTitle,
    observed != null && hasExpected && expected != null
      ? `Coverage: ${formatCount(observed)} / ${formatCount(expected)}`
      : null,
    observed == null && hasExpected && expected != null
      ? `Observed candidates: unavailable / ${formatCount(expected)} expected`
      : null,
    acceptedCandidateCount != null
      ? `Accepted candidates: ${formatCount(acceptedCandidateCount)}`
      : null,
    candidateCount != null ? `Candidate count: ${formatCount(candidateCount)}` : null,
    sourceCount
      ? `Source receipts: ${formatCount(sourceCount)}${
          lineage.source_receipt_keys_redacted ? " (IDs redacted)" : ""
        }`
      : null,
    lineage.source_receipt_keys_truncated
      ? "Source receipt list was truncated"
      : null,
    lineage.coverage_warning
      ? `Warning: ${formatSyntheticRejectionReason(lineage.coverage_warning)}`
      : null,
    flagTitle("with_base_receipt_count", "Base receipt evidence"),
    flagTitle("with_cross_receipt_item_count", "Cross-receipt item evidence"),
    flagTitle("with_category_evidence_count", "Category evidence"),
    flagTitle("with_nearest_real_structure_count", "Nearest-real structure"),
    flagTitle("with_layout_integrity_count", "Layout evidence"),
    flagTitle("with_arithmetic_reconciliation_count", "Arithmetic evidence"),
    flagTitle("with_selection_evidence_count", "Selection evidence"),
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return {
    label,
    statusLabel,
    title: title || undefined,
    warning,
  };
};

const summarizeMixBalance = (
  balance?: TrainingSynthesisMixBalance
): { label: string; title?: string } | null => {
  if (!balance) return null;
  const risk = (balance.risk_level || "").toLowerCase();
  const label =
    risk && risk !== "none"
      ? `${formatCategoryName(risk)} risk`
      : balance.merchant_count != null && balance.operation_count != null
        ? `${formatCount(balance.merchant_count)} merchants · ${formatCount(balance.operation_count)} ops`
        : null;
  if (!label) return null;

  const title = [
    balance.top_merchant
      ? `Top merchant: ${balance.top_merchant}${
          balance.top_merchant_share != null
            ? ` (${formatPercent(balance.top_merchant_share)})`
            : ""
        }`
      : null,
    balance.top_operation
      ? `Top operation: ${formatOperationName(balance.top_operation)}${
          balance.top_operation_share != null
            ? ` (${formatPercent(balance.top_operation_share)})`
            : ""
        }`
      : null,
    balance.merchant_entropy != null
      ? `Merchant entropy: ${formatSimilarity(balance.merchant_entropy)}`
      : null,
    balance.operation_entropy != null
      ? `Operation entropy: ${formatSimilarity(balance.operation_entropy)}`
      : null,
    (balance.risk_reasons || []).length
      ? `Reasons: ${(balance.risk_reasons || [])
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return { label, title: title || undefined };
};

const summarizeTrainingBatchPolicy = (
  policy?: TrainingSynthesisTrainingBatchPolicy
): { label: string; title?: string; warning?: boolean } | null => {
  if (!policy || !policy.status) return null;
  const status = policy.status.toLowerCase();
  const recommended = policy.recommended_example_count ?? 0;
  const label =
    status === "hold"
      ? "hold"
      : status === "smoke_test_only"
        ? `smoke ${formatCount(recommended)}`
        : status === "bounded_augmentation"
          ? `train ${formatCount(recommended)}`
          : formatCategoryName(policy.status);
  const title = [
    `Recommended examples: ${formatCount(recommended)}`,
    policy.accepted_candidate_count != null
      ? `Accepted candidates: ${formatCount(policy.accepted_candidate_count)}`
      : null,
    policy.selected_candidate_count != null
      ? `Selected rows: ${formatCount(policy.selected_candidate_count)}`
      : null,
    policy.high_fidelity_candidate_count != null
      ? `High fidelity: ${formatCount(policy.high_fidelity_candidate_count)}`
      : null,
    policy.max_synthetic_train_share != null
      ? `Max synthetic train share: ${formatPercent(policy.max_synthetic_train_share)}`
      : null,
    policy.overtraining_risk_level
      ? `Overtraining risk: ${formatCategoryName(policy.overtraining_risk_level)}`
      : null,
    (policy.risk_reasons || []).length
      ? `Risk reasons: ${(policy.risk_reasons || [])
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
    (policy.hold_reasons || []).length
      ? `Hold reasons: ${(policy.hold_reasons || [])
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
    policy.max_per_merchant != null && policy.max_per_merchant_operation != null
      ? `Caps: ${formatCount(policy.max_per_merchant)} / merchant, ${formatCount(policy.max_per_merchant_operation)} / merchant-operation`
      : null,
    policy.requires_real_validation_split ? "Validation: real receipts only" : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return {
    label,
    title: title || undefined,
    warning: policy.review_required === true || status !== "bounded_augmentation",
  };
};

const summarizeRealBaselineComparison = (
  baseline?: TrainingSynthesisRealBaselineSummary | null
): { label: string; title?: string } | null => {
  if (!baseline || !baseline.count) return null;
  const within = baseline.within_real_score_range_count ?? 0;
  const label = `${formatCount(within)} / ${formatCount(
    baseline.count
  )} in range`;
  const title = [
    baseline.within_real_score_range_share != null
      ? `In-range share: ${formatPercent(baseline.within_real_score_range_share)}`
      : null,
    baseline.delta_from_avg?.avg != null
      ? `Avg delta vs real avg: ${baseline.delta_from_avg.avg >= 0 ? "+" : ""}${formatSimilarity(
          baseline.delta_from_avg.avg
        )}`
      : null,
    baseline.delta_from_min?.min != null
      ? `Worst delta vs real min: ${baseline.delta_from_min.min >= 0 ? "+" : ""}${formatSimilarity(
          baseline.delta_from_min.min
        )}`
      : null,
    baseline.baseline_pair_count?.avg != null
      ? `Avg real receipt pairs: ${formatSimilarity(baseline.baseline_pair_count.avg)}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return { label, title: title || undefined };
};

const STRUCTURE_COMPONENT_LABELS: Record<string, string> = {
  price_column: "Price column",
  line_step: "Row spacing",
  category_sequence: "Category order",
  category_set: "Category match",
  token_count: "Token count",
};

const summarizeStructureComponentGate = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const thresholds =
    synthesis.quality_report?.quality_gates?.structure_component_thresholds;
  const acceptedComponents =
    synthesis.quality_report?.summary?.accepted_structure_components ??
    synthesis.accepted_structure_components;
  const entries = Object.entries(thresholds || {})
    .filter(([, value]) => Number.isFinite(value))
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return null;

  const [primaryName, primaryThreshold] = entries[0];
  const title = entries
    .map(([name, threshold]) => {
      const avg = acceptedComponents?.[name]?.avg;
      const accepted = Number.isFinite(avg)
        ? `, accepted avg ${formatSimilarity(avg)}`
        : "";
      return `${STRUCTURE_COMPONENT_LABELS[name] || formatFieldName(name)} >= ${formatPercent(threshold)}${accepted}`;
    })
    .join(", ");
  return {
    label: `${STRUCTURE_COMPONENT_LABELS[primaryName] || formatFieldName(primaryName)} ${formatPercent(primaryThreshold)}`,
    title,
  };
};

const formatEvidenceCheck = (check: string): string =>
  check
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");

type SynthesisQualityExample = NonNullable<
  TrainingSynthesisQualityMerchant["accepted_examples"]
>[number];

const formatPlural = (count: number, singular: string, plural = `${singular}s`): string =>
  `${formatCount(count)} ${count === 1 ? singular : plural}`;

const RECEIPT_EXCERPT_LINE_MAX_CHARS = 96;
const RECEIPT_EXCERPT_LINE_LIMIT = 3;

const clipReceiptExcerptLine = (value: string): string =>
  value.length <= RECEIPT_EXCERPT_LINE_MAX_CHARS
    ? value
    : `${value.slice(0, RECEIPT_EXCERPT_LINE_MAX_CHARS - 3)}...`;

const summarizeExampleGrounding = (
  example?: SynthesisQualityExample
): string | null => {
  if (!example) return null;
  const catalog = example.catalog_grounding;
  const placement = example.category_placement;
  const structure = example.structure_evidence;
  const parts: string[] = [];
  const outsideCount = catalog?.product_seen_outside_base_count;
  if (outsideCount && outsideCount > 0) {
    parts.push(formatPlural(outsideCount, "external receipt"));
  }

  const category = placement?.category || catalog?.category || example.category;
  const categoryCount =
    placement?.category_seen_count ?? catalog?.category_seen_count ?? null;
  if (category && categoryCount && categoryCount > 0) {
    parts.push(
      `${formatCategoryName(category)} in ${formatPlural(categoryCount, "receipt")}`
    );
  }

  const headingCount =
    placement?.category_heading_seen_count ??
    catalog?.category_heading_seen_count ??
    null;
  if (headingCount && headingCount > 0) {
    parts.push(formatPlural(headingCount, "heading"));
  }

  if (placement?.category_alignment === "same_category_as_base") {
    parts.push("same section");
  }

  const shapeCheckCount = structure?.match_summary?.shape_checks?.length || 0;
  if (shapeCheckCount > 0) {
    parts.push(formatPlural(shapeCheckCount, "shape check"));
  } else if (structure?.nearest_real_receipt_key) {
    parts.push("nearest real shape");
  }

  return parts.slice(0, 2).join(" · ") || null;
};

const summarizeStructureEvidence = (
  structure?: TrainingSynthesisStructureEvidence
): { label?: string; title?: string } => {
  if (!structure) return {};
  const shapeChecks = structure.match_summary?.shape_checks || [];
  const weakComponents = structure.match_summary?.weak_components || [];
  const baseline = structure.real_baseline_comparison;
  const baselineStatus =
    baseline?.within_real_score_range === true
      ? "within real range"
      : baseline?.within_real_score_range === false
        ? "below real range"
        : null;
  const similarityLabel =
    structure.score != null ? `${formatSimilarity(structure.score)} sim` : null;
  const label = shapeChecks.length
    ? [
        formatPlural(shapeChecks.length, "shape check"),
        similarityLabel,
        baselineStatus,
      ]
        .filter(Boolean)
        .join(" · ")
    : structure.nearest_real_receipt_key
      ? ["nearest real", similarityLabel, baselineStatus]
          .filter(Boolean)
          .join(" · ")
      : undefined;
  const delta = structure.shape_deltas || {};
  const baselineDelta =
    baseline?.delta_from_avg != null
      ? `, candidate ${baseline.delta_from_avg >= 0 ? "+" : ""}${formatSimilarity(
          baseline.delta_from_avg
        )} vs avg`
      : "";
  const title = [
    structure.nearest_real_receipt_key
      ? `Nearest real receipt: ${structure.nearest_real_receipt_key}`
      : null,
    baseline
      ? `Real baseline: avg ${formatSimilarity(
          baseline.baseline_avg
        )}, range ${formatSimilarity(baseline.baseline_min)}-${formatSimilarity(
          baseline.baseline_max
        )}${baselineDelta}`
      : null,
    shapeChecks.length
      ? `Shape checks: ${shapeChecks.map(formatEvidenceCheck).join(", ")}`
      : null,
    weakComponents.length
      ? `Weak components: ${weakComponents.map(formatEvidenceCheck).join(", ")}`
      : null,
    Object.keys(delta).length
      ? `Deltas: ${Object.entries(delta)
          .map(([key, value]) => `${formatEvidenceCheck(key)} ${value}`)
          .join(", ")}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return { label, title: title || undefined };
};

const summarizeLayoutIntegrity = (
  layout?: TrainingSynthesisLayoutIntegrityEvidence
): { label?: string; title?: string } => {
  if (!layout) return {};
  const issueCount =
    (layout.overlap_pair_count ?? 0) +
    (layout.out_of_bounds_word_count ?? 0) +
    (layout.invalid_word_box_count ?? 0) +
    (layout.line_order_valid === false ? 1 : 0);
  const score =
    layout.score != null ? ` ${formatSimilarity(layout.score)}` : "";
  const label =
    layout.passed === true
      ? `layout ok${score}`
      : issueCount > 0
        ? `layout risk ${formatCount(issueCount)}`
        : `layout unchecked${score}`;
  const title = [
    layout.passed === true
      ? "Layout integrity passed"
      : layout.passed === false
        ? "Layout integrity did not pass"
        : "Layout integrity not reported",
    layout.score != null ? `score ${formatSimilarity(layout.score)}` : null,
    layout.overlap_pair_count != null
      ? `${formatCount(layout.overlap_pair_count)} overlap pairs`
      : null,
    layout.out_of_bounds_word_count != null
      ? `${formatCount(layout.out_of_bounds_word_count)} out-of-bounds words`
      : null,
    layout.invalid_word_box_count != null
      ? `${formatCount(layout.invalid_word_box_count)} invalid boxes`
      : null,
    layout.line_order_valid === false ? "line order invalid" : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");
  return { label, title: title || undefined };
};

const summarizeSelectionEvidence = (
  example?: SynthesisQualityExample
): { label?: string; title?: string } => {
  const evidence = example?.selection_evidence;
  if (!evidence) return {};
  const selectedFrom = evidence.selected_from_candidate_count;
  const score = evidence.selected_score || {};
  const labelParts = [
    selectedFrom && selectedFrom > 1
      ? `selected from ${formatCount(selectedFrom)}`
      : null,
    Number.isFinite(score.candidate_quality)
      ? `quality ${formatSimilarity(score.candidate_quality)}`
      : null,
    score.within_real_score_range === true
      ? "real range"
      : score.within_real_score_range === false
        ? "below range"
        : null,
  ].filter((value): value is string => Boolean(value));
  const rankedBy = (evidence.ranked_by || [])
    .slice(0, 4)
    .map(formatEvidenceCheck);
  const title = [
    selectedFrom != null
      ? `Selected from ${formatCount(selectedFrom)} feasible mutation${
          selectedFrom === 1 ? "" : "s"
        }`
      : null,
    score.candidate_quality != null
      ? `Candidate quality: ${formatSimilarity(score.candidate_quality)}`
      : null,
    score.structure_similarity != null
      ? `Structure similarity: ${formatSimilarity(score.structure_similarity)}`
      : null,
    score.layout_integrity != null
      ? `Layout integrity: ${formatSimilarity(score.layout_integrity)}`
      : null,
    score.delta_from_min != null
      ? `Delta from real baseline min: ${
          score.delta_from_min >= 0 ? "+" : ""
        }${formatSimilarity(score.delta_from_min)}`
      : null,
    score.baseline_pair_count != null
      ? `Real baseline pairs: ${formatCount(score.baseline_pair_count)}`
      : null,
    rankedBy.length ? `Ranked by: ${rankedBy.join(", ")}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");
  return {
    label: labelParts.join(" · ") || undefined,
    title: title || undefined,
  };
};

const summarizeReceiptShape = (
  example?: SynthesisQualityExample
): { label?: string; title?: string } => {
  if (!example) return {};
  const shape = example.receipt_shape || {};
  const previewLines = example.preview_lines || [];
  const focusedLine =
    previewLines.find((line) => line.synthetic_insert) ||
    previewLines.find((line) => (line.modified_labels || []).length > 0) ||
    previewLines[0];
  const lineCount = shape.line_count ?? null;
  const tokenCount = shape.token_count ?? null;
  const labelParts = [
    lineCount != null ? formatPlural(lineCount, "line") : null,
    tokenCount != null ? formatPlural(tokenCount, "token") : null,
  ].filter((value): value is string => Boolean(value));
  const bbox = focusedLine?.bbox;
  const bboxLabel =
    Array.isArray(bbox) && bbox.length === 4
      ? `[${bbox.map((value) => String(value)).join(" / ")}]`
      : null;
  const receiptExcerptLines = previewLines
    .map((line) => {
      const text = line.text?.replace(/\s+/g, " ").trim();
      if (!text) return null;
      const prefix = line.line_number != null ? `[${line.line_number}] ` : "";
      return clipReceiptExcerptLine(`${prefix}${text}`);
    })
    .filter((value): value is string => Boolean(value));
  const receiptExcerpt =
    receiptExcerptLines.length > 0
      ? `${receiptExcerptLines.slice(0, RECEIPT_EXCERPT_LINE_LIMIT).join(" ; ")}${
          receiptExcerptLines.length > RECEIPT_EXCERPT_LINE_LIMIT
            ? ` (+${receiptExcerptLines.length - RECEIPT_EXCERPT_LINE_LIMIT} more)`
            : ""
        }`
      : "";
  const title = [
    labelParts.length ? `Shape: ${labelParts.join(", ")}` : null,
    shape.truncated === true ? "Preview truncated" : null,
    focusedLine?.text ? `Focused line: ${focusedLine.text}` : null,
    focusedLine?.line_number != null
      ? `Line number: ${formatCount(focusedLine.line_number)}`
      : null,
    focusedLine?.y != null
      ? `Line y (normalized): ${formatSimilarity(focusedLine.y)}`
      : null,
    bboxLabel ? `Line bbox (receipt coords): ${bboxLabel}` : null,
    receiptExcerpt ? `Receipt excerpt: ${receiptExcerpt}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return {
    label: labelParts.join(" · ") || undefined,
    title: title || undefined,
  };
};

const summarizeReceiptPreview = (
  examples: TrainingSynthesisSummary["candidate_examples"] | undefined
): { label: string; title?: string } | null => {
  const example = (examples || []).find(
    (candidate) =>
      candidate.receipt_preview?.text ||
      candidate.accuracy_evidence?.changed_text ||
      candidate.item_text ||
      candidate.new_text
  );
  if (!example) return null;

  const previewLines = example.receipt_preview?.lines || [];
  const changedLine =
    previewLines.find((line) => line.synthetic_insert)?.text ||
    previewLines.find((line) => (line.modified_labels || []).length > 0)?.text ||
    example.accuracy_evidence?.changed_text ||
    example.item_text ||
    (example.field_label && example.new_text
      ? `${formatFieldName(example.field_label)} ${example.new_text}`
      : null);
  if (!changedLine) return null;

  const category =
    example.category || example.accuracy_evidence?.category || undefined;
  const detailParts = [
    example.merchant_name,
    example.operation ? formatOperationName(example.operation) : null,
    category ? formatCategoryName(category) : null,
    example.structure_similarity != null
      ? `similarity ${formatSimilarity(example.structure_similarity)}`
      : null,
  ].filter((value): value is string => Boolean(value));
  const checks = (example.accuracy_evidence?.checks || [])
    .slice(0, 4)
    .map(formatEvidenceCheck);
  const structureSummary = summarizeStructureEvidence(
    example.accuracy_evidence?.structure_similarity
  );
  const layoutSummary = summarizeLayoutIntegrity(
    example.accuracy_evidence?.layout_integrity
  );
  const selectionSummary = summarizeSelectionEvidence(
    candidateExampleToQualityExample(example)
  );
  const titleParts = [
    detailParts.join(" | "),
    example.receipt_preview?.text,
    layoutSummary.title,
    structureSummary.title,
    selectionSummary.title,
    checks.length ? `Checks: ${checks.join(", ")}` : null,
  ].filter((value): value is string => Boolean(value));

  return {
    label: changedLine,
    title: titleParts.join("\n"),
  };
};

const summarizeOperationCounts = (
  counts: Record<string, number> | undefined,
  fallbackOperations: string[] | undefined
): string => {
  const entries = Object.entries(counts || {})
    .filter(([operation, count]) => operation && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (entries.length) {
    return entries
      .slice(0, 2)
      .map(
        ([operation, count]) =>
          `${formatOperationName(operation)} ${formatCount(count)}`
      )
      .join(" · ");
  }
  const operations = (fallbackOperations || []).filter(Boolean);
  if (operations.length) {
    return operations.slice(0, 2).map(formatOperationName).join(" · ");
  }
  return "No accepted mutations";
};

const formatReadinessStatus = (status?: string | null): string =>
  status ? formatCategoryName(status) : "Unknown";

const candidateExampleToQualityExample = (
  example: NonNullable<TrainingSynthesisSummary["candidate_examples"]>[number]
): NonNullable<TrainingSynthesisQualityMerchant["accepted_examples"]>[number] => ({
  candidate_id: example.candidate_id,
  operation: example.operation,
  category: example.category || example.accuracy_evidence?.category,
  changed_text:
    example.accuracy_evidence?.changed_text ||
    example.item_text ||
    example.new_text,
  label: example.field_label,
  structure_similarity: example.structure_similarity,
  structure_evidence: example.accuracy_evidence?.structure_similarity,
  candidate_quality: example.candidate_quality,
  selection_evidence: example.selection_evidence,
  layout_integrity: example.accuracy_evidence?.layout_integrity,
  accuracy_checks: example.accuracy_evidence?.checks || [],
  catalog_grounding: example.accuracy_evidence?.catalog_grounding,
  category_placement: example.accuracy_evidence?.category_placement,
  receipt_shape: {
    line_count: example.receipt_preview?.line_count,
    token_count: example.receipt_preview?.token_count,
    truncated: example.receipt_preview?.truncated,
  },
  preview_lines: example.receipt_preview?.lines || [],
});

const merchantGapMap = (
  synthesis: TrainingSynthesisSummary
): Map<string, TrainingSynthesisMerchantGap> =>
  new Map(
    (synthesis.quality_report?.merchant_gap_summary?.merchants || [])
      .filter((merchant) => merchant.merchant_name)
      .map((merchant) => [merchant.merchant_name as string, merchant])
  );

const mergeMerchantGapDetails = (
  merchant: TrainingSynthesisQualityMerchant,
  gap?: TrainingSynthesisMerchantGap
): TrainingSynthesisQualityMerchant => {
  if (!gap) return merchant;
  return {
    ...merchant,
    readiness_status: merchant.readiness_status ?? gap.status,
    readiness_score: merchant.readiness_score ?? gap.score,
    candidate_count: merchant.candidate_count ?? gap.candidate_count,
    accepted_count: merchant.accepted_count ?? gap.accepted_count,
    blockers: merchant.blockers?.length ? merchant.blockers : gap.blockers,
    limitations: merchant.limitations?.length
      ? merchant.limitations
      : gap.limitations,
    missing_operations: gap.missing_operations,
    operation_gap_reasons: gap.operation_gap_reasons,
  };
};

const buildMerchantQualityRows = (
  synthesis: TrainingSynthesisSummary
): TrainingSynthesisQualityMerchant[] => {
  const gapsByMerchant = merchantGapMap(synthesis);
  const reportRows = synthesis.quality_report?.merchants || [];
  if (reportRows.length) {
    return reportRows
      .slice(0, 4)
      .map((merchant) =>
        mergeMerchantGapDetails(
          merchant,
          merchant.merchant_name
            ? gapsByMerchant.get(merchant.merchant_name)
            : undefined
        )
      );
  }

  const contractsByMerchant = new Map(
    (synthesis.merchant_synthesis_contracts || [])
      .filter((contract) => contract.merchant_name)
      .map((contract) => [contract.merchant_name as string, contract])
  );
  const examplesByMerchant = new Map<string, ReturnType<typeof candidateExampleToQualityExample>[]>();
  (synthesis.candidate_examples || []).forEach((example) => {
    const merchant = example.merchant_name || "Unknown merchant";
    const rows = examplesByMerchant.get(merchant) || [];
    rows.push(candidateExampleToQualityExample(example));
    examplesByMerchant.set(merchant, rows);
  });

  return (synthesis.candidate_mix_merchants || []).slice(0, 4).map((merchant) => {
    const merchantName = merchant.merchant_name || "Unknown merchant";
    const contract = contractsByMerchant.get(merchantName);
    const candidateCount = merchant.candidate_count ?? null;
    const acceptedCount = merchant.accepted_count ?? null;
    const acceptanceRate =
      candidateCount && acceptedCount != null ? acceptedCount / candidateCount : null;
    return mergeMerchantGapDetails({
      merchant_name: merchantName,
      readiness_status: contract?.status,
      readiness_score: contract?.score,
      source_receipt_count: contract?.source_receipt_count,
      candidate_count: candidateCount,
      accepted_count: acceptedCount,
      rejected_count: merchant.rejected_count,
      acceptance_rate: acceptanceRate,
      supported_operations: contract?.supported_operations,
      contract_ready_operations: contract?.ready_operations,
      accepted_operation_counts: merchant.accepted_operation_counts,
      accepted_category_counts: merchant.accepted_category_counts,
      accepted_field_replacement_counts: contract?.accepted_field_replacement_counts,
      accepted_structure_similarity: merchant.accepted_structure_similarity,
      rejection_reasons: merchant.rejection_reasons,
      blockers: contract?.blockers,
      limitations: contract?.limitations,
      accepted_examples: examplesByMerchant.get(merchantName)?.slice(0, 3) || [],
    }, gapsByMerchant.get(merchantName));
  });
};

const summarizeMerchantOperationGaps = (
  merchant: TrainingSynthesisQualityMerchant
): { label: string; title?: string } | null => {
  const missingOperations = (merchant.missing_operations || []).filter(Boolean);
  const blockers = (merchant.blockers || []).filter(Boolean);
  const limitations = (merchant.limitations || []).filter(Boolean);
  const nextActions = (merchant.next_synthesis_actions || []).filter(Boolean);
  const operationReasonEntries = Object.entries(
    merchant.operation_gap_reasons || {}
  ).filter(([operation, reasons]) => operation && reasons.length);

  if (
    !missingOperations.length &&
    !blockers.length &&
    !limitations.length &&
    !nextActions.length &&
    !operationReasonEntries.length
  ) {
    return null;
  }

  const label = missingOperations.length
      ? `Needs ${missingOperations.slice(0, 2).map(formatOperationName).join(", ")}${
        missingOperations.length > 2 ? `, +${formatCount(missingOperations.length - 2)}` : ""
      }`
    : blockers.length
      ? `Blocked: ${formatSyntheticRejectionReason(blockers[0])}`
      : limitations.length
        ? `Limited: ${formatSyntheticRejectionReason(limitations[0])}`
        : nextActions.length
          ? `Next: ${formatSyntheticRejectionReason(nextActions[0])}`
          : `Gap: ${formatOperationName(operationReasonEntries[0][0])}`;

  const title = [
    missingOperations.length
      ? `Missing operations: ${missingOperations.map(formatOperationName).join(", ")}`
      : null,
    blockers.length
      ? `Blockers: ${blockers.map(formatSyntheticRejectionReason).join(", ")}`
      : null,
    limitations.length
      ? `Limitations: ${limitations.map(formatSyntheticRejectionReason).join(", ")}`
      : null,
    nextActions.length
      ? `Next actions: ${nextActions
          .slice(0, 4)
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
    ...operationReasonEntries.map(
      ([operation, reasons]) =>
        `${formatOperationName(operation)}: ${reasons
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
    ),
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return {
    label,
    title: title || undefined,
  };
};

const SynthesisMerchantQualityPanel: React.FC<SynthesisEvidenceStripProps> = ({
  synthesis,
}) => {
  if (!synthesis) return null;
  const rows = buildMerchantQualityRows(synthesis);
  if (!rows.length) return null;
  const sourceByMerchant = sourceQualityMerchantMap(synthesis);

  return (
    <div
      className={styles.synthesisMerchantQuality}
      aria-label="Synthetic merchant quality"
    >
      {rows.slice(0, 3).map((merchant) => {
        const merchantName = merchant.merchant_name || "Unknown merchant";
        const accepted = merchant.accepted_count ?? 0;
        const candidateCount = merchant.candidate_count ?? 0;
        const rejected = merchant.rejected_count ?? 0;
        const firstExample = (merchant.accepted_examples || [])[0];
        const previewLine =
          firstExample?.preview_lines?.find((line) => line.synthetic_insert)?.text ||
          firstExample?.preview_lines?.[0]?.text ||
          firstExample?.changed_text ||
          (firstExample?.label && firstExample?.field_replacement?.new_text
            ? `${formatFieldName(firstExample.label)} ${firstExample.field_replacement.new_text}`
            : null);
        const checks = (firstExample?.accuracy_checks || [])
          .slice(0, 2)
          .map(formatEvidenceCheck);
        const grounding = summarizeExampleGrounding(firstExample);
        const shapeSummary = summarizeStructureEvidence(
          firstExample?.structure_evidence
        );
        const layoutSummary = summarizeLayoutIntegrity(
          firstExample?.layout_integrity
        );
        const selectionSummary = summarizeSelectionEvidence(firstExample);
        const receiptShapeSummary = summarizeReceiptShape(firstExample);
        const evidenceTitle = [
          receiptShapeSummary.title,
          layoutSummary.title,
          shapeSummary.title,
          selectionSummary.title,
        ]
          .filter(Boolean)
          .join(" | ");
        const evidenceDetail = [
          grounding,
          receiptShapeSummary.label,
          layoutSummary.label,
          shapeSummary.label,
          selectionSummary.label,
          ...checks,
        ]
          .filter((value): value is string => Boolean(value))
          .slice(0, 3)
          .join(" · ");
        const rejectionTitle = summarizeSyntheticRejections(
          merchant.rejection_reasons,
          rejected
        );
        const operationSummary = summarizeOperationCounts(
          merchant.accepted_operation_counts,
          merchant.contract_ready_operations
        );
        const operationGapSummary = summarizeMerchantOperationGaps(merchant);
        const status = merchant.readiness_status || "unknown";
        const sourceSummary = summarizeSourceQualityMerchant(
          sourceByMerchant.get(merchantName) || reportMerchantSourceQuality(merchant)
        );

        return (
          <div
            key={merchantName}
            className={styles.synthesisMerchantCard}
            data-status={status}
          >
            <div className={styles.synthesisMerchantHeader}>
              <strong>{merchantName}</strong>
              <span>{formatReadinessStatus(status)}</span>
            </div>
            <div className={styles.synthesisMerchantMeta}>
              {sourceSummary && (
                <span title={sourceSummary.title}>{sourceSummary.label}</span>
              )}
              <span>
                {formatCount(accepted)}
                {candidateCount ? ` / ${formatCount(candidateCount)}` : ""} accepted
              </span>
              <span>{formatSimilarity(merchant.accepted_structure_similarity?.avg)} sim</span>
              {rejected > 0 && (
                <span title={rejectionTitle.title}>
                  {formatCount(rejected)} rejected
                </span>
              )}
            </div>
            <div className={styles.synthesisMerchantOps}>{operationSummary}</div>
            {operationGapSummary && (
              <div
                className={styles.synthesisMerchantGap}
                title={operationGapSummary.title}
              >
                {operationGapSummary.label}
              </div>
            )}
            {previewLine && (
              <div
                className={styles.synthesisMerchantEvidence}
                title={evidenceTitle}
              >
                <span>{previewLine}</span>
                {evidenceDetail && <em>{evidenceDetail}</em>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

const SynthesisEvidenceStrip: React.FC<SynthesisEvidenceStripProps> = ({
  synthesis,
}) => {
  if (!synthesis) return null;

  const candidateCount = synthesis.candidate_count ?? 0;
  const groundedCount =
    synthesis.accepted_grounded_candidate_count ??
    synthesis.grounded_candidate_count ??
    0;
  const acceptedSynthetic =
    synthesis.bundle_candidates_accepted ??
    synthesis.synthetic_candidates_accepted ??
    synthesis.synthetic_train_examples ??
    null;
  const seenSynthetic =
    synthesis.bundle_candidates_seen ?? synthesis.synthetic_candidates_seen ?? null;
  const rejectedSynthetic =
    synthesis.bundle_candidates_rejected ??
    synthesis.synthetic_candidates_rejected ??
    synthesis.rejected_count ??
    0;
  const groundedShare =
    synthesis.grounded_candidate_share ??
    (candidateCount ? groundedCount / candidateCount : null);
  const acceptedMerchantCount = synthesis.accepted_merchant_count ?? null;
  const readyMerchantCount =
    acceptedMerchantCount ??
    synthesis.ready_merchant_count ??
    synthesis.readiness_status_counts?.ready ??
    null;
  const blockedMerchantCount =
    synthesis.readiness_status_counts?.blocked ??
    synthesis.candidate_mix_merchants?.filter(
      (merchant) =>
        (merchant.rejection_reasons?.merchant_synthesis_not_ready ?? 0) > 0
    ).length ??
    0;
  const merchantEvidenceLabel =
    acceptedMerchantCount != null ? "Accepted merchants" : "Ready merchants";
  const readinessTotal = synthesis.merchant_count ?? null;
  const avgReadinessScore = synthesis.avg_readiness_score ?? null;
  const merchantEvidenceDetails = [
    blockedMerchantCount ? `${formatCount(blockedMerchantCount)} blocked` : null,
    acceptedMerchantCount == null && avgReadinessScore
      ? formatPercent(avgReadinessScore)
      : null,
  ].filter((value): value is string => Boolean(value));
  const arithmeticUpdateCount = Object.values(
    synthesis.arithmetic_update_counts || {}
  ).reduce((sum, count) => sum + count, 0);
  const arithmeticCandidateCount =
    synthesis.accepted_arithmetic_candidate_count ??
    synthesis.arithmetic_candidate_count ??
    0;
  const hasArtifact = synthesis.status === "available";
  const statusLabel = hasArtifact ? "Artifact" : "Metrics";
  const rejectionSummary = rejectedSynthetic
    ? summarizeSyntheticRejections(
        synthesis.bundle_rejection_reasons ??
          synthesis.synthetic_rejection_reasons ??
          synthesis.rejection_reasons,
        rejectedSynthetic
      )
    : null;
  const categorySummary = summarizeCategoryCounts(
    synthesis.accepted_category_counts ?? synthesis.category_counts
  );
  const fieldReplacementSummary = summarizeFieldReplacementCounts(
    synthesis.accepted_field_replacement_counts ??
      synthesis.field_replacement_counts,
    synthesis.candidate_examples
  );
  const candidateQualitySummary = summarizeCandidateQuality(synthesis);
  const sourceLineageSummary = summarizeSourceLineage(synthesis);
  const merchantGapSummary = summarizeMerchantGaps(synthesis);
  const operationReadinessSummary = summarizeOperationReadiness(synthesis);
  const sourceReceiptQualitySummary = summarizeSourceReceiptQuality(synthesis);
  const contractSummary = summarizeContractCoverage(synthesis);
  const llmExecutionSummary = summarizeLlmExecution(synthesis);
  const receiptPreviewSummary = summarizeReceiptPreview(
    synthesis.candidate_examples
  );
  const structureGateSummary = summarizeStructureComponentGate(synthesis);
  const realBaselineSummary = summarizeRealBaselineComparison(
    synthesis.accepted_real_baseline_comparison ??
      synthesis.synthetic_accepted_real_baseline_comparison ??
      synthesis.quality_report?.summary?.accepted_real_baseline_comparison
  );
  const mixBalanceSummary = summarizeMixBalance(
    synthesis.accepted_mix_balance ??
      synthesis.synthetic_accepted_mix_balance ??
      synthesis.quality_report?.summary?.accepted_mix_balance
  );
  const trainingBatchPolicySummary = summarizeTrainingBatchPolicy(
    synthesis.quality_report?.training_batch_policy
  );

  return (
    <div
      className={styles.synthesisEvidencePanel}
      aria-label="Synthetic receipt grounding evidence"
    >
      <div className={styles.synthesisEvidenceStrip}>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={rejectionSummary?.title}>
            {rejectionSummary ? rejectionSummary.label : statusLabel}
          </span>
          <strong>
            {formatCount(acceptedSynthetic)}
            {seenSynthetic ? ` / ${formatCount(seenSynthetic)}` : ""} synth
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Grounded</span>
          <strong>
            {formatCount(groundedCount)}
            {candidateCount ? ` / ${formatCount(candidateCount)}` : ""}
            {candidateCount ? ` (${formatPercent(groundedShare)})` : ""}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={candidateQualitySummary?.title}>Fidelity</span>
          <strong title={candidateQualitySummary?.title}>
            {candidateQualitySummary?.label ?? "—"}
          </strong>
        </div>
        <div
          className={`${styles.synthesisEvidenceMetric}${
            sourceLineageSummary?.warning
              ? ` ${styles.synthesisEvidenceMetricWarning}`
              : ""
          }`}
        >
          <span title={sourceLineageSummary?.title}>
            {sourceLineageSummary?.statusLabel ?? "Lineage"}
          </span>
          <strong title={sourceLineageSummary?.title}>
            {sourceLineageSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>{merchantEvidenceLabel}</span>
          <strong>
            {formatCount(readyMerchantCount)}
            {readinessTotal ? ` / ${formatCount(readinessTotal)}` : ""}
            {merchantEvidenceDetails.length
              ? ` (${merchantEvidenceDetails.join(", ")})`
              : ""}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={merchantGapSummary?.title}>Merchant gaps</span>
          <strong title={merchantGapSummary?.title}>
            {merchantGapSummary?.label ?? "—"}
          </strong>
        </div>
        <div
          className={`${styles.synthesisEvidenceMetric}${
            operationReadinessSummary?.warning
              ? ` ${styles.synthesisEvidenceMetricWarning}`
              : ""
          }`}
        >
          <span title={operationReadinessSummary?.title}>Operation readiness</span>
          <strong title={operationReadinessSummary?.title}>
            {operationReadinessSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={mixBalanceSummary?.title}>Mix balance</span>
          <strong title={mixBalanceSummary?.title}>
            {mixBalanceSummary?.label ?? "—"}
          </strong>
        </div>
        <div
          className={`${styles.synthesisEvidenceMetric}${
            trainingBatchPolicySummary?.warning
              ? ` ${styles.synthesisEvidenceMetricWarning}`
              : ""
          }`}
        >
          <span title={trainingBatchPolicySummary?.title}>Batch policy</span>
          <strong title={trainingBatchPolicySummary?.title}>
            {trainingBatchPolicySummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={sourceReceiptQualitySummary?.title}>Source receipts</span>
          <strong title={sourceReceiptQualitySummary?.title}>
            {sourceReceiptQualitySummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Contracts</span>
          <strong title={contractSummary?.title}>
            {contractSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={llmExecutionSummary?.title}>LLM mode</span>
          <strong title={llmExecutionSummary?.title}>
            {llmExecutionSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={structureGateSummary?.title}>Avg similarity</span>
          <strong title={structureGateSummary?.title}>
            {formatSimilarity(
              synthesis.accepted_structure_similarity?.avg ??
                synthesis.avg_structure_similarity ??
                synthesis.best_structure_similarity
            )}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={realBaselineSummary?.title}>Real baseline</span>
          <strong title={realBaselineSummary?.title}>
            {realBaselineSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={structureGateSummary?.title}>Geometry gate</span>
          <strong title={structureGateSummary?.title}>
            {structureGateSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Receipt preview</span>
          <strong title={receiptPreviewSummary?.title}>
            {receiptPreviewSummary?.label ?? categorySummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Field edits</span>
          <strong title={fieldReplacementSummary?.title}>
            {fieldReplacementSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Arithmetic</span>
          <strong>
            {formatCount(arithmeticCandidateCount)} edits
            {arithmeticUpdateCount
              ? ` / ${formatCount(arithmeticUpdateCount)} rows`
              : ""}
          </strong>
        </div>
      </div>
      <SynthesisMerchantQualityPanel synthesis={synthesis} />
    </div>
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

  const {
    num_train_samples,
    num_val_samples,
    o_entity_ratio_train,
    synthetic_train_examples,
  } = datasetMetrics;

  // Calculate percentages for train/val split
  const total = num_train_samples + num_val_samples;
  if (total === 0) return null;

  const trainPercent = (num_train_samples / total) * 100;
  const valPercent = (num_val_samples / total) * 100;
  const hasSyntheticExamples =
    Number.isFinite(synthetic_train_examples) &&
    (synthetic_train_examples as number) > 0;

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
        {hasSyntheticExamples && (
          <span
            className={styles.statNote}
            title="Train-only synthetic examples included in the training count"
          >
            {(synthetic_train_examples as number).toLocaleString()} synth
          </span>
        )}
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

interface TopConfusionPairsProps {
  labels: string[];
  matrix: number[][];
}

const TopConfusionPairs: React.FC<TopConfusionPairsProps> = ({
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

interface ConfusionPairRowProps {
  pair: ConfusionPair;
  maxCount: number;
}

const ConfusionPairRow: React.FC<ConfusionPairRowProps> = ({
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

const RECEIPT_HEATMAP_BANDS: {
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

const RECEIPT_HEATMAP_LABELS: Record<ReceiptHeatmapZone, string> = {
  header: "Header",
  identity: "Identity",
  items: "Items",
  totals: "Totals",
  footer: "Footer",
};

interface PatternHeatmapPanelProps {
  pairs: ConfusionPair[];
}

const PatternHeatmapPanel: React.FC<PatternHeatmapPanelProps> = ({
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

interface PatternHeatmapCellRowProps {
  cell: PatternHeatmapCell;
}

const PatternHeatmapCellRow: React.FC<PatternHeatmapCellRowProps> = ({
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
            y1={F1_CHART_H - F1_PAD_BOTTOM}
            y2={F1_CHART_H - F1_PAD_BOTTOM}
            stroke={SKELETON_BG}
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
          <line
            x1={SVG_PAD_X}
            x2={SVG_W_FALLBACK - SVG_PAD_X}
            y1={LOSS_STRIP_TOP - 7}
            y2={LOSS_STRIP_TOP - 7}
            stroke={SKELETON_BG}
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>
      <div className={styles.synthesisEvidencePanel} aria-hidden="true">
        <div className={styles.synthesisEvidenceStrip}>
          {Array.from({ length: 15 }, (_, idx) => (
            <div key={idx} className={styles.synthesisEvidenceMetric}>
              <span
                className={styles.confusionPairSkeletonText}
                style={{ width: idx === 3 ? 58 : 42 }}
              />
              <strong
                className={styles.confusionPairSkeletonText}
                style={{ width: idx === 3 ? 88 : 54 }}
              />
            </div>
          ))}
        </div>
        <div className={styles.synthesisMerchantQuality}>
          {[0, 1, 2].map((idx) => (
            <div key={idx} className={styles.synthesisMerchantCard}>
              <div className={styles.synthesisMerchantHeader}>
                <strong
                  className={styles.confusionPairSkeletonText}
                  style={{ width: idx === 1 ? 112 : 84 }}
                />
                <span
                  className={styles.confusionPairSkeletonText}
                  style={{ width: 42 }}
                />
              </div>
              <div className={styles.synthesisMerchantMeta}>
                <span
                  className={styles.confusionPairSkeletonText}
                  style={{ width: 64 }}
                />
                <span
                  className={styles.confusionPairSkeletonText}
                  style={{ width: 42 }}
                />
              </div>
              <div
                className={styles.confusionPairSkeletonText}
                style={{ width: "68%" }}
              />
            </div>
          ))}
        </div>
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
        <div className={styles.confusionPairs} aria-hidden="true">
          <div className={styles.confusionPairsHeader}>
            <span>Top Confusions</span>
            <span>Pattern Target</span>
          </div>
          {[0, 1, 2].map((row) => (
            <div key={row} className={styles.confusionPairRow}>
              <div className={styles.confusionPairMetric}>
                <span
                  className={styles.confusionPairSkeletonText}
                  style={{ width: row === 0 ? "82%" : row === 1 ? "68%" : "74%" }}
                />
                <div className={styles.confusionPairBar}>
                  <span
                    style={{
                      width: row === 0 ? "100%" : row === 1 ? "64%" : "42%",
                      background: SKELETON_BG,
                    }}
                  />
                </div>
              </div>
              <div className={styles.confusionPairTarget}>
                <span
                  className={styles.confusionPairSkeletonText}
                  style={{ width: "76%" }}
                />
                <span
                  className={styles.confusionPairSkeletonText}
                  style={{ width: "56%" }}
                />
              </div>
            </div>
          ))}
        </div>
        <div className={styles.patternMiningPanel} aria-hidden="true">
          <svg
            className={styles.receiptHeatmap}
            viewBox="0 0 190 310"
            role="img"
          >
            <rect
              className={styles.receiptHeatmapPaper}
              x="22"
              y="8"
              width="146"
              height="294"
              rx="4"
              style={{ opacity: 0.55 }}
            />
            {RECEIPT_HEATMAP_BANDS.map((band) => (
              <rect
                key={band.zone}
                x="34"
                y={band.y}
                width="122"
                height={band.height}
                rx="3"
                style={{ fill: SKELETON_BG }}
              />
            ))}
            <path
              className={styles.receiptHeatmapFold}
              d="M34 292 L48 278 L62 292 L76 278 L90 292 L104 278 L118 292 L132 278 L156 292"
            />
          </svg>
          <div className={styles.patternMiningSummary}>
            <span
              className={styles.confusionPairSkeletonText}
              style={{ width: 92 }}
            />
            <span
              className={styles.confusionPairSkeletonText}
              style={{ width: 118, opacity: 0.7 }}
            />
            {[0, 1, 2].map((row) => (
              <div key={row} className={styles.patternHeatmapCellRow}>
                <div className={styles.patternHeatmapCellHeader}>
                  <span
                    className={styles.confusionPairSkeletonText}
                    style={{ width: row === 0 ? 76 : 62 }}
                  />
                  <span
                    className={styles.confusionPairSkeletonText}
                    style={{ width: 24 }}
                  />
                </div>
                <div className={styles.patternHeatmapCellBar}>
                  <span
                    style={{
                      width: row === 0 ? "92%" : row === 1 ? "64%" : "42%",
                      background: SKELETON_BG,
                    }}
                  />
                </div>
                <span
                  className={styles.confusionPairSkeletonText}
                  style={{ width: "88%" }}
                />
                <span
                  className={styles.confusionPairSkeletonText}
                  style={{ width: "72%", opacity: 0.65 }}
                />
              </div>
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
    fallbackInView: true,
  });
  const { ref: animRef, inView } = useInView({
    threshold: 0.3,
    triggerOnce: true,
    fallbackInView: true,
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
  const [synthesis, setSynthesis] = useState<TrainingSynthesisSummary | null>(
    null
  );
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
        setSynthesis(data.synthesis ?? null);
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
        synthesis={synthesis}
      />

      <div className={styles.leftPanel}>
        <F1Gauge value={currentEpoch.metrics.val_f1} />
        <PerLabelBars perLabel={currentEpoch.per_label} />
        <BarLegend />
      </div>

      <div className={styles.rightPanel}>
        {currentEpoch.confusion_matrix && (
          <>
            <ConfusionMatrix
              labels={currentEpoch.confusion_matrix.labels}
              matrix={currentEpoch.confusion_matrix.matrix}
            />
            <TopConfusionPairs
              labels={currentEpoch.confusion_matrix.labels}
              matrix={currentEpoch.confusion_matrix.matrix}
            />
          </>
        )}
      </div>
    </div>
  );
};

export default TrainingMetricsAnimation;
