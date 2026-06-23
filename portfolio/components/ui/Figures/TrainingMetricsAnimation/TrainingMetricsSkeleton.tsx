import React from "react";
import styles from "./TrainingMetricsAnimation.module.css";
import { RECEIPT_HEATMAP_BANDS } from "./ConfusionViz";
import {
  F1_CHART_H,
  F1_PAD_BOTTOM,
  LOSS_STRIP_TOP,
  SVG_H,
  SVG_PAD_X,
  SVG_W_FALLBACK,
} from "./EpochSparkline";
import { BarLegend } from "./LabelStats";

// Skeleton placeholder labels (match the 8 entity labels in the loaded state)
export const SKELETON_LABELS = [
  "Address", "Amount", "Date", "Merchant Name",
  "Payment Method", "Store Hours", "Time", "Website",
];

// 9 labels for the confusion matrix (8 entity + O)
export const SKELETON_MATRIX_LABELS = ["Addr", "Amt", "Date", "Merch", "Pay", "Hours", "Time", "Web", "O"];

export const SKELETON_BG = "rgba(var(--text-color-rgb, 0, 0, 0), 0.08)";

// Skeleton that mirrors the loaded layout exactly
export const TrainingMetricsSkeleton: React.FC = () => {
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
