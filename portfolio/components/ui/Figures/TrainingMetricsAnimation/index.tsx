import { useEffect, useState, useMemo, useRef } from "react";
import { animated, useSpring, config } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import { TrainingMetricsEpoch } from "../../../../types/api";
import styles from "./TrainingMetricsAnimation.module.css";

// Label color mapping
const LABEL_COLORS: Record<string, string> = {
  ADDRESS: "var(--color-red)",
  AMOUNT: "var(--color-green)",
  DATE: "var(--color-blue)",
  MERCHANT_NAME: "var(--color-yellow)",
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

// Spring config for smooth animations
const SPRING_CONFIG = { tension: 120, friction: 14 };

// Epoch Timeline Component
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

  return (
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
    to: { width: value * 100, displayValue: value * 100 },
    config: SPRING_CONFIG,
  });

  return (
    <div className={styles.labelRow}>
      <span className={styles.labelName}>{formatLabel(label)}</span>
      <div className={styles.labelBarContainer}>
        <animated.div
          className={styles.labelBar}
          style={{
            width: spring.width.to((w) => `${w}%`),
            backgroundColor: getLabelColor(label),
          }}
        />
        <animated.span className={styles.labelBarValue}>
          {spring.displayValue.to((v) => `${v.toFixed(0)}%`)}
        </animated.span>
      </div>
    </div>
  );
};

// Confusion Matrix Heatmap Component
interface ConfusionMatrixProps {
  labels: string[];
  matrix: number[][];
}

const ConfusionMatrix: React.FC<ConfusionMatrixProps> = ({ labels, matrix }) => {
  // Calculate row sums for row-normalized coloring
  const rowSums = useMemo(() => {
    return matrix.map((row) => row.reduce((sum, val) => sum + val, 0) || 1);
  }, [matrix]);

  // Grid template: first column for Y labels, then fixed-size columns for cells
  const cellSize = "50px";
  const gridTemplateColumns = `45px repeat(${labels.length}, ${cellSize})`;
  const gridTemplateRows = `35px repeat(${labels.length}, ${cellSize})`;

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
          <div key={`x-${label}`} className={styles.matrixAxisLabel}>
            {formatLabel(label)}
          </div>
        ))}

        {/* Matrix rows */}
        {matrix.map((row, i) => (
          <>
            {/* Y-axis label */}
            <div
              key={`y-${labels[i]}`}
              className={`${styles.matrixAxisLabel} ${styles.matrixAxisLabelY}`}
            >
              {formatLabel(labels[i])}
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
          </>
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
