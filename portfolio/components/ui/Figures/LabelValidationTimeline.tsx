import { Fragment, useEffect, useState } from "react";
import { useSpring, animated, SpringValue } from "@react-spring/web";
import { useOptimizedInView } from "../../../hooks/useOptimizedInView";
import { api } from "../../../services/api";
import {
  LabelValidationTimelineResponse,
  LabelValidationKeyframe,
  LabelValidationStatusCounts,
} from "../../../types/api";
import styles from "./LabelValidationTimeline.module.css";

const STATUSES = ["VALID", "INVALID", "PENDING", "NEEDS_REVIEW", "NONE"] as const;
type StatusType = (typeof STATUSES)[number];

const STATUS_COLORS: Record<StatusType, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  PENDING: "var(--color-blue)",
  NEEDS_REVIEW: "var(--color-yellow)",
  NONE: "var(--text-color)",
};

/**
 * Format label for display: Title Case with "ID" special case
 */
function formatLabel(text: string): string {
  return text
    .toLowerCase()
    .replace(/_/g, " ")
    .split(" ")
    .map((word) =>
      word === "id" ? "ID" : word.charAt(0).toUpperCase() + word.slice(1)
    )
    .join(" ");
}

/**
 * Interpolate between keyframes based on progress [0, 1]
 * Returns smoothly interpolated label counts
 */
function interpolateKeyframes(
  keyframes: LabelValidationKeyframe[],
  progress: number
): LabelValidationKeyframe {
  if (keyframes.length === 0) {
    return {
      progress: 0,
      timestamp: "",
      records_processed: 0,
      labels: {},
    };
  }

  // Clamp progress
  const p = Math.max(0, Math.min(1, progress));

  // Find surrounding keyframes
  let lower = keyframes[0];
  let upper = keyframes[keyframes.length - 1];

  for (let i = 0; i < keyframes.length - 1; i++) {
    if (keyframes[i].progress <= p && keyframes[i + 1].progress >= p) {
      lower = keyframes[i];
      upper = keyframes[i + 1];
      break;
    }
  }

  // Linear interpolation factor between keyframes
  const range = upper.progress - lower.progress;
  const t = range > 0 ? (p - lower.progress) / range : 0;

  // Interpolate counts
  const interpolatedLabels: { [key: string]: LabelValidationStatusCounts } = {};
  const allLabelNames = Array.from(new Set([
    ...Object.keys(lower.labels),
    ...Object.keys(upper.labels),
  ]));

  const emptyStatus: LabelValidationStatusCounts = {
    VALID: 0,
    INVALID: 0,
    PENDING: 0,
    NEEDS_REVIEW: 0,
    NONE: 0,
    total: 0,
  };

  for (const labelName of allLabelNames) {
    const lowerCounts = lower.labels[labelName] || emptyStatus;
    const upperCounts = upper.labels[labelName] || emptyStatus;

    interpolatedLabels[labelName] = {
      VALID: Math.round(
        lowerCounts.VALID + t * (upperCounts.VALID - lowerCounts.VALID)
      ),
      INVALID: Math.round(
        lowerCounts.INVALID + t * (upperCounts.INVALID - lowerCounts.INVALID)
      ),
      PENDING: Math.round(
        lowerCounts.PENDING + t * (upperCounts.PENDING - lowerCounts.PENDING)
      ),
      NEEDS_REVIEW: Math.round(
        lowerCounts.NEEDS_REVIEW +
          t * (upperCounts.NEEDS_REVIEW - lowerCounts.NEEDS_REVIEW)
      ),
      NONE: Math.round(
        lowerCounts.NONE + t * (upperCounts.NONE - lowerCounts.NONE)
      ),
      total: Math.round(
        lowerCounts.total + t * (upperCounts.total - lowerCounts.total)
      ),
    };
  }

  return {
    progress: p,
    timestamp: t < 0.5 ? lower.timestamp : upper.timestamp,
    records_processed: Math.round(
      lower.records_processed +
        t * (upper.records_processed - lower.records_processed)
    ),
    labels: interpolatedLabels,
  };
}

interface AnimatedLabelRowProps {
  labelName: string;
  keyframes: LabelValidationKeyframe[];
  progress: SpringValue<number>;
  maxTotal: number;
}

const AnimatedLabelRow: React.FC<AnimatedLabelRowProps> = ({
  labelName,
  keyframes,
  progress,
  maxTotal,
}) => {
  return (
    <Fragment>
      <div className={styles.labelName}>
        {formatLabel(labelName)}
      </div>

      {/* Bar container - width scales to max */}
      <animated.div
        className={styles.barWrapper}
        style={{
          width: progress.to((p) => {
            const frame = interpolateKeyframes(keyframes, p);
            const labelData = frame.labels[labelName];
            if (!labelData || maxTotal === 0) return "0%";
            return `${(labelData.total / maxTotal) * 100}%`;
          }),
        }}
      >
        {/* Status segments within bar */}
        {STATUSES.map((status) => (
          <animated.div
            key={status}
            className={styles.barSegment}
            style={{
              backgroundColor: STATUS_COLORS[status],
              width: progress.to((p) => {
                const frame = interpolateKeyframes(keyframes, p);
                const labelData = frame.labels[labelName];
                if (!labelData || labelData.total === 0) return "0%";
                return `${(labelData[status] / labelData.total) * 100}%`;
              }),
            }}
          />
        ))}
      </animated.div>

      <animated.div className={styles.total}>
        {progress.to((p) => {
          const frame = interpolateKeyframes(keyframes, p);
          const labelData = frame.labels[labelName];
          return labelData ? labelData.total.toLocaleString() : "0";
        })}
      </animated.div>
    </Fragment>
  );
};

export default function LabelValidationTimeline() {
  const [ref, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: true,
  });
  const [timeline, setTimeline] =
    useState<LabelValidationTimelineResponse | null>(null);
  const [hasAnimated, setHasAnimated] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .fetchLabelValidationTimeline()
      .then(setTimeline)
      .catch((err) => {
        console.error("Failed to fetch timeline:", err);
        setError("Failed to load timeline data");
      });
  }, []);

  useEffect(() => {
    if (inView && timeline && !hasAnimated) {
      setHasAnimated(true);
    }
  }, [inView, timeline, hasAnimated]);

  // Animate progress 0 → 1 with spring physics
  const { progress } = useSpring({
    progress: hasAnimated ? 1 : 0,
    config: { mass: 1, tension: 20, friction: 14 },
  });

  if (error) {
    return <div className={styles.error}>{error}</div>;
  }

  if (!timeline) {
    return <div className={styles.loading}>Loading timeline...</div>;
  }

  if (timeline.keyframes.length === 0) {
    return <div className={styles.loading}>No timeline data available</div>;
  }

  // Get final state for computing max values (for bar scaling)
  const finalFrame = timeline.keyframes[timeline.keyframes.length - 1];
  const maxTotal = Math.max(
    ...Object.values(finalFrame.labels).map((l) => l.total),
    1
  );

  // Sort labels alphabetically
  const sortedLabels = Object.keys(finalFrame.labels).sort();

  return (
    <div ref={ref} className={styles.container}>
      <h2 className={styles.title}>Label Validation</h2>

      {/* Animated subtitle with month/year and label count */}
      <animated.p className={styles.subtitle}>
        {progress.to((p) => {
          const frame = interpolateKeyframes(timeline.keyframes, p);
          if (!frame.timestamp) return "";
          const date = new Date(frame.timestamp);
          const month = date.toLocaleString("default", { month: "long" });
          const year = date.getFullYear();
          return `${month} ${year} · ${frame.records_processed.toLocaleString()} labels`;
        })}
      </animated.p>

      {/* Animated bars */}
      <div className={styles.barsContainer}>
        {sortedLabels.map((labelName) => (
          <AnimatedLabelRow
            key={labelName}
            labelName={labelName}
            keyframes={timeline.keyframes}
            progress={progress}
            maxTotal={maxTotal}
          />
        ))}
      </div>

      {/* Legend */}
      <div className={styles.legend}>
        {STATUSES.map((status) => (
          <div key={status} className={styles.legendItem}>
            <span
              className={styles.legendDot}
              style={{ backgroundColor: STATUS_COLORS[status] }}
            />
            <span>
              {status
                .toLowerCase()
                .replace(/_/g, " ")
                .split(" ")
                .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
                .join(" ")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
