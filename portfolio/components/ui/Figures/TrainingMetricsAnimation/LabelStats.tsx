import React from "react";
import { animated, useSpring } from "@react-spring/web";
import { DatasetMetrics } from "../../../../types/api";
import styles from "./TrainingMetricsAnimation.module.css";
import { formatLabel } from "./confusionMatrixView";
import { SPRING_CONFIG } from "./EpochSparkline";

// Dataset Stats Component with segmented bars
export interface DatasetStatsProps {
  datasetMetrics?: DatasetMetrics;
}

export const DatasetStats: React.FC<DatasetStatsProps> = ({ datasetMetrics }) => {
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
export interface F1GaugeProps {
  value: number;
}

export const F1Gauge: React.FC<F1GaugeProps> = ({ value }) => {
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
export interface PerLabelBarsProps {
  perLabel: Record<string, { f1: number; precision: number; recall: number; support: number }>;
}

export const PerLabelBars: React.FC<PerLabelBarsProps> = ({ perLabel }) => {
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

export interface LabelBarProps {
  label: string;
  value: number;
  support: number;
  maxSupport: number;
}

export const LabelBar: React.FC<LabelBarProps> = ({ label, value, support, maxSupport }) => {
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
export const BarLegend: React.FC = () => (
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
