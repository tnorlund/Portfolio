import React from "react";
import styles from "./TrainingMetricsAnimation.module.css";

const LABELS = [
  "Address",
  "Amount",
  "Date",
  "Merchant Name",
  "Payment Method",
  "Store Hours",
  "Time",
  "Website",
];
const MATRIX_LABELS = ["Addr", "Amt", "Date", "Merch", "Pay", "Hours", "Time", "Web", "O"];
const SKELETON_BG = "rgba(var(--text-color-rgb, 0, 0, 0), 0.08)";
const SVG_WIDTH = 600;
const SVG_HEIGHT = 80;
const SVG_PAD_X = 8;
const SVG_PAD_BOTTOM = 14;

const BarLegendSkeleton = () => (
  <div className={styles.barLegend}>
    <div className={styles.legendEntry}>
      <span className={styles.legendSwatch} style={{ background: "var(--text-color)" }} />
      <span className={styles.legendLabel}>F1 Score</span>
    </div>
    <div className={styles.legendEntry}>
      <span className={styles.legendSwatch} style={{ background: "var(--color-blue)" }} />
      <span className={styles.legendLabel}>Support</span>
    </div>
  </div>
);

/** Shared by the dynamic-import boundary and the API-loading state. */
export const TrainingMetricsSkeleton: React.FC = () => {
  const count = MATRIX_LABELS.length;
  const gridTemplateColumns = `var(--matrix-label-col) repeat(${count}, var(--matrix-cell-size))`;
  const gridTemplateRows = `var(--matrix-header-row) repeat(${count}, var(--matrix-cell-size))`;

  return (
    <>
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

      <div className={styles.timeline}>
        <svg
          viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
          preserveAspectRatio="none"
          className={styles.sparkline}
          aria-hidden="true"
        >
          <line
            x1={SVG_PAD_X}
            x2={SVG_WIDTH - SVG_PAD_X}
            y1={SVG_HEIGHT - SVG_PAD_BOTTOM}
            y2={SVG_HEIGHT - SVG_PAD_BOTTOM}
            stroke={SKELETON_BG}
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>

      <div className={styles.timelineMobile}>
        <div className={styles.timelineArrow} style={{ opacity: 0.25 }}>‹</div>
        <div className={styles.timelineMobileCenter}>
          <span className={styles.timelineMobileText} style={{ opacity: 0.3 }}>— / —</span>
        </div>
        <div className={styles.timelineArrow} style={{ opacity: 0.25 }}>›</div>
      </div>

      <div className={styles.leftPanel}>
        <div className={styles.gaugeContainer}>
          <div style={{ width: 80, height: 32, background: SKELETON_BG, borderRadius: 4 }} />
          <div className={styles.gaugeBar} />
        </div>
        <div className={styles.perLabelContainer}>
          {LABELS.map((label) => (
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
        <BarLegendSkeleton />
      </div>

      <div className={styles.rightPanel}>
        <div className={styles.matrixContainer}>
          <div className={styles.matrixGrid} style={{ gridTemplateColumns, gridTemplateRows }}>
            <div className={styles.matrixCorner} />
            {MATRIX_LABELS.map((label) => (
              <div key={`x-${label}`} className={styles.matrixAxisLabel} style={{ opacity: 0.3 }}>
                {label}
              </div>
            ))}
            {MATRIX_LABELS.map((rowLabel, rowIndex) => (
              <React.Fragment key={`row-${rowLabel}`}>
                <div className={`${styles.matrixAxisLabel} ${styles.matrixAxisLabelY}`} style={{ opacity: 0.3 }}>
                  {rowLabel}
                </div>
                {MATRIX_LABELS.map((_, columnIndex) => (
                  <div
                    key={`${rowIndex}-${columnIndex}`}
                    className={styles.matrixCell}
                    style={{ backgroundColor: rowIndex === columnIndex ? SKELETON_BG : "transparent" }}
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

export const TrainingMetricsLoadingShell: React.FC = () => (
  <div className={styles.container} aria-busy="true" aria-label="Loading training metrics">
    <TrainingMetricsSkeleton />
  </div>
);

export default TrainingMetricsLoadingShell;
