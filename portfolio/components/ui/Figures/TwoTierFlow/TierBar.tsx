import React from "react";
import { getPieSlicePath } from "./getPieSlicePath";
import { DecisionTally } from "./DecisionTally";
import type { RevealedDecision, TierConfig } from "./types";
import styles from "./TwoTierFlow.module.css";

interface TierBarProps {
  config: TierConfig;
  progress: number;
  isWaiting: boolean;
  isComplete: boolean;
  durationMs?: number;
  decisions: RevealedDecision[];
  totalDecisions: number;
  isTransitioning: boolean;
  nextTotalDecisions: number;
  showPlaceholders?: boolean;
}

export const TierBar: React.FC<TierBarProps> = ({
  config,
  progress,
  isWaiting,
  isComplete,
  durationMs,
  decisions,
  totalDecisions,
  isTransitioning,
  nextTotalDecisions,
  showPlaceholders = true,
}) => {
  const isActive = progress > 0 && progress < 100;

  return (
    <div
      className={`${styles.legendItem} ${isActive ? styles.active : ""} ${isComplete ? styles.complete : ""}`}
    >
      <div className={styles.legendIcon}>
        {isWaiting ? (
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            className={styles.hourglassIcon}
          >
            <path
              d="M4 2h8v3l-2.5 3L12 11v3H4v-3l2.5-3L4 5V2z"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              fill="none"
            />
            <path
              d="M6 3h4M6 13h4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        ) : (
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            className={styles.legendDot}
          >
            <circle
              cx="8"
              cy="8"
              r="6"
              fill="none"
              stroke={config.color}
              strokeWidth="1.5"
              opacity={0.3}
            />
            {progress > 0 && (
              <path
                d={getPieSlicePath(progress, 8, 8, 6)}
                fill={config.color}
                opacity={isComplete ? 1 : 0.8}
              />
            )}
          </svg>
        )}
      </div>
      <span className={styles.legendName}>{config.name}</span>
      <div className={styles.legendStatus}>
        {isComplete && durationMs !== undefined ? (
          <span className={styles.legendDuration}>
            {durationMs < 1000
              ? `${durationMs.toFixed(0)}ms`
              : `${(durationMs / 1000).toFixed(1)}s`}
          </span>
        ) : isWaiting ? (
          <span className={styles.legendWaiting}>waiting</span>
        ) : null}
      </div>
      <DecisionTally
        decisions={decisions}
        totalDecisions={totalDecisions}
        isTransitioning={isTransitioning}
        nextTotalDecisions={nextTotalDecisions}
        showPlaceholders={showPlaceholders}
      />
    </div>
  );
};
