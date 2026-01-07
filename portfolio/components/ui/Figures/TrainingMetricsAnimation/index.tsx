import React, { useEffect, useState, useMemo, useRef } from "react";
import { animated, useSpring, config } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import { DatasetMetrics, TrainingMetricsEpoch } from "../../../../types/api";
import styles from "./TrainingMetricsAnimation.module.css";

// Label color mapping for 8-label hybrid model
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  AMOUNT: "var(--color-green)",
  ADDRESS: "var(--color-red)",
  WEBSITE: "var(--color-purple)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-orange)",
  O: "var(--color-purple)",
};

const getLabelColor = (label: string): string => {
  return LABEL_COLORS[label] || "var(--color-gray, #888)";
};

// Format label: "MERCHANT_NAME" -> "Merchant Name", "O" -> "None"
const formatLabel = (label: string): string => {
  if (label === "O") return "None";
  return label
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
};

// Abbreviated labels for confusion matrix
const LABEL_ABBREV: Record<string, string> = {
  MERCHANT_NAME: "Merch",
  DATE: "Date",
  TIME: "Time",
  AMOUNT: "Amt",
  ADDRESS: "Addr",
  WEBSITE: "Web",
  STORE_HOURS: "Hours",
  PAYMENT_METHOD: "Pay",
  O: "O",
};

const formatLabelAbbrev = (label: string): string => {
  return LABEL_ABBREV[label] || label.slice(0, 4);
};

// Spring config for smooth animations
const SPRING_CONFIG = { tension: 120, friction: 14 };

// Epoch Timeline Component (Desktop - dots)
interface EpochTimelineProps {
  epochs: TrainingMetricsEpoch[];
  currentIndex: number;
  onSelectEpoch: (index: number) => void;
  showBestLabel: boolean;
}

const EpochTimeline: React.FC<EpochTimelineProps> = ({
  epochs,
  currentIndex,
  onSelectEpoch,
  showBestLabel,
}) => {
  const nodesRef = useRef<HTMLDivElement>(null);
  const [lineStyle, setLineStyle] = useState<{ left: number; width: number } | null>(null);

  useEffect(() => {
    if (!nodesRef.current || epochs.length < 2) return;

    const updateLine = () => {
      const container = nodesRef.current;
      if (!container) return;

      const dots = container.querySelectorAll(`.${styles.timelineNodeDot}`);
      if (dots.length < 2) return;

      const firstDot = dots[0] as HTMLElement;
      const lastDot = dots[dots.length - 1] as HTMLElement;
      const containerRect = container.getBoundingClientRect();
      const firstRect = firstDot.getBoundingClientRect();
      const lastRect = lastDot.getBoundingClientRect();

      const left = firstRect.left - containerRect.left + firstRect.width / 2;
      const right = lastRect.left - containerRect.left + lastRect.width / 2;

      setLineStyle({ left, width: right - left });
    };

    updateLine();
    window.addEventListener('resize', updateLine);
    return () => window.removeEventListener('resize', updateLine);
  }, [epochs.length]);

  const currentEpoch = epochs[currentIndex];
  const isBest = currentEpoch?.is_best && showBestLabel;

  return (
    <>
      {/* Desktop timeline with dots */}
      <div className={styles.timeline}>
        <div ref={nodesRef} className={styles.timelineNodes}>
          {lineStyle && (
            <div
              className={styles.timelineLine}
              style={{ left: lineStyle.left, width: lineStyle.width }}
            />
          )}
          {epochs.map((epoch, index) => (
            <button
              key={epoch.epoch}
              className={`${styles.timelineNode} ${
                index === currentIndex ? styles.timelineNodeActive : ""
              }`}
              onClick={() => onSelectEpoch(index)}
              title={`Epoch ${epoch.epoch}${epoch.is_best ? " (Best)" : ""}`}
            >
              {epoch.is_best && showBestLabel && (
                <span className={styles.timelineBestLabel}>Best</span>
              )}
              <span className={styles.timelineNodeDot} />
              <span className={styles.timelineNodeLabel}>{epoch.epoch}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Mobile timeline with arrows */}
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
          {isBest && <span className={styles.timelineBestLabelMobile}>Best</span>}
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

  return (
    <div className={styles.perLabelContainer}>
      {labels.map((label) => {
        const f1 = perLabel[label]?.f1 || 0;
        return <LabelBar key={label} label={label} value={f1} />;
      })}
    </div>
  );
};

interface LabelBarProps {
  label: string;
  value: number;
}

const LabelBar: React.FC<LabelBarProps> = ({ label, value }) => {
  const spring = useSpring({
    to: { width: value * 100, displayValue: value },
    config: SPRING_CONFIG,
  });

  return (
    <div className={styles.labelRow}>
      <span className={styles.labelName}>{formatLabel(label)}</span>
      <div className={styles.labelBarSegmented}>
        <animated.div
          className={styles.labelBarFilled}
          style={{ width: spring.width.to((w) => `${w}%`) }}
        />
        <animated.div
          className={styles.labelBarEmpty}
          style={{ width: spring.width.to((w) => `${100 - w}%`) }}
        />
      </div>
      <animated.span className={styles.labelBarValue}>
        {spring.displayValue.to((v) => v.toFixed(2))}
      </animated.span>
    </div>
  );
};

// Confusion Matrix Heatmap Component
interface ConfusionMatrixProps {
  labels: string[];
  matrix: number[][];
}

const ConfusionMatrix: React.FC<ConfusionMatrixProps> = ({ labels, matrix }) => {
  // Track window width for responsive cell sizing
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const checkMobile = () => setIsMobile(window.innerWidth < 600);
    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => window.removeEventListener("resize", checkMobile);
  }, []);

  // Calculate row sums for row-normalized coloring
  const rowSums = useMemo(() => {
    return matrix.map((row) => row.reduce((sum, val) => sum + val, 0) || 1);
  }, [matrix]);

  // Grid template: responsive cell sizes for mobile
  const cellSize = isMobile ? "28px" : "34px";
  const labelColWidth = isMobile ? "36px" : "42px";
  const headerRowHeight = isMobile ? "28px" : "32px";
  const gridTemplateColumns = `${labelColWidth} repeat(${labels.length}, ${cellSize})`;
  const gridTemplateRows = `${headerRowHeight} repeat(${labels.length}, ${cellSize})`;

  return (
    <div className={styles.matrixContainer}>
      <div
        className={styles.matrixGrid}
        style={{ gridTemplateColumns, gridTemplateRows }}
      >
        {/* Corner cell */}
        <div className={styles.matrixCorner} />

        {/* X-axis labels (top) */}
        {labels.map((label) => (
          <div
            key={`x-${label}`}
            className={styles.matrixAxisLabel}
            title={formatLabel(label)}
          >
            {formatLabelAbbrev(label)}
          </div>
        ))}

        {/* Matrix rows */}
        {matrix.map((row, i) => (
          <React.Fragment key={`row-${i}-${labels[i]}`}>
            {/* Y-axis label */}
            <div
              className={`${styles.matrixAxisLabel} ${styles.matrixAxisLabelY}`}
              title={formatLabel(labels[i])}
            >
              {formatLabelAbbrev(labels[i])}
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
        ))}
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

  const spring = useSpring({
    to: { intensity, displayValue: value },
    config: SPRING_CONFIG,
  });

  // Use green for diagonal (correct predictions), red for off-diagonal (errors)
  // Empty cells (value = 0) use transparent background
  const colorVar = isDiagonal ? "--color-green-rgb" : "--color-red-rgb";

  return (
    <animated.div
      className={styles.matrixCell}
      style={{
        backgroundColor: spring.intensity.to((i) =>
          i < 0.01 ? "transparent" : `rgba(var(${colorVar}), ${0.2 + i * 0.8})`
        ),
      }}
    >
      <animated.span>
        {spring.displayValue.to((v) =>
          v > 0.5 ? Math.round(v).toLocaleString() : ""
        )}
      </animated.span>
    </animated.div>
  );
};

// Main Component
const TrainingMetricsAnimation: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: true,
  });
  const [epochs, setEpochs] = useState<TrainingMetricsEpoch[]>([]);
  const [datasetMetrics, setDatasetMetrics] = useState<DatasetMetrics | undefined>();
  const [currentEpochIndex, setCurrentEpochIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [showBestLabel, setShowBestLabel] = useState(false);
  const hasStartedAnimation = useRef(false);

  // Fetch data on mount
  useEffect(() => {
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
  }, []);

  // Autoplay animation when in view and data is loaded
  useEffect(() => {
    if (!inView || hasStartedAnimation.current || epochs.length === 0 || isLoading) {
      return;
    }

    hasStartedAnimation.current = true;
    setShowBestLabel(false);

    // Find the best epoch index
    const bestIndex = epochs.findIndex((e) => e.is_best);

    // Start from epoch 0, animate through ALL epochs, then land on best
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
    }, 1200);

    return () => clearInterval(interval);
  }, [inView, epochs, isLoading]);

  const currentEpoch = epochs[currentEpochIndex];

  if (isLoading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading training metrics...
      </div>
    );
  }

  if (!currentEpoch) {
    return (
      <div ref={ref} className={styles.loading}>
        No training data available
      </div>
    );
  }

  const handleSelectEpoch = (index: number) => {
    setCurrentEpochIndex(index);
  };

  return (
    <animated.div ref={ref} className={styles.container}>
      <DatasetStats datasetMetrics={datasetMetrics} />
      <EpochTimeline
        epochs={epochs}
        currentIndex={currentEpochIndex}
        onSelectEpoch={handleSelectEpoch}
        showBestLabel={showBestLabel}
      />

      <div className={styles.leftPanel}>
        <F1Gauge value={currentEpoch.metrics.val_f1} />
        <PerLabelBars perLabel={currentEpoch.per_label} />
      </div>

      <div className={styles.rightPanel}>
        {currentEpoch.confusion_matrix && (
          <ConfusionMatrix
            labels={currentEpoch.confusion_matrix.labels}
            matrix={currentEpoch.confusion_matrix.matrix}
          />
        )}
      </div>
    </animated.div>
  );
};

export default TrainingMetricsAnimation;
