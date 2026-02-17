import React from "react";
import { ReceiptFlowShell } from "./ReceiptFlowShell";
import styles from "./ReceiptFlowLoadingShell.module.css";

export type ReceiptFlowLoadingVariant =
  | "within"
  | "financial"
  | "between"
  | "layoutlm";

const DEFAULT_LAYOUT_VARS = {
  "--rf-queue-width": "120px",
  "--rf-queue-height": "400px",
  "--rf-center-max-width": "350px",
  "--rf-center-height": "500px",
  "--rf-mobile-center-height": "400px",
  "--rf-mobile-center-height-sm": "320px",
  "--rf-gap": "1.5rem",
  "--rf-align-items": "flex-start",
} as React.CSSProperties;

interface ReceiptFlowLoadingShellProps {
  layoutVars?: React.CSSProperties;
  variant?: ReceiptFlowLoadingVariant;
  /** Shown in center when provided (error/empty state); otherwise skeleton. */
  message?: string;
  /** When true, center shows message with error styling. */
  isError?: boolean;
}

function QueueSkeleton() {
  return (
    <div className={styles.queueSkeleton} data-rf-queue>
      {[1, 2, 3, 4, 5].map((i) => (
        <div key={i} className={styles.queuedPlaceholder} />
      ))}
    </div>
  );
}

function CenterSkeleton({ message, isError }: { message?: string; isError?: boolean }) {
  if (message) {
    return (
      <div
        className={`${styles.centerMessage} ${isError ? styles.error : ""}`}
        role={isError ? "alert" : undefined}
      >
        {message}
      </div>
    );
  }
  return (
    <div className={styles.centerSkeleton}>
      <div className={styles.receiptPlaceholder} />
    </div>
  );
}

function LegendSkeleton({ variant }: { variant: ReceiptFlowLoadingVariant }) {
  switch (variant) {
    case "within":
      return (
        <div className={`${styles.legendSkeleton} ${styles.legendWithin}`}>
          <div className={styles.passDotsSkeleton}>
            <div className={styles.passDotSkeleton} />
            <div className={styles.passDotSkeleton} />
          </div>
          <div className={styles.cardBlockSkeleton}>
            <div className={`${styles.cardLineSkeleton} ${styles.short}`} />
            <div className={`${styles.cardLineSkeleton} ${styles.medium}`} />
            <div className={`${styles.cardLineSkeleton} ${styles.long}`} />
            <div className={`${styles.cardLineSkeleton} ${styles.medium}`} />
          </div>
        </div>
      );
    case "financial":
      return (
        <div className={`${styles.legendSkeleton} ${styles.legendFinancial}`}>
          {[1, 2, 3].map((i) => (
            <div key={i} className={styles.equationCardSkeleton} />
          ))}
        </div>
      );
    case "between":
      return (
        <div className={`${styles.legendSkeleton} ${styles.legendBetween}`}>
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className={styles.evidenceCardSkeleton} />
          ))}
        </div>
      );
    case "layoutlm":
      return (
        <div className={`${styles.legendSkeleton} ${styles.legendLayoutLM}`}>
          {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
            <div key={i} className={styles.legendRowSkeleton}>
              <div className={styles.legendDotSkeleton} />
              <div className={styles.legendLabelSkeleton} />
            </div>
          ))}
          <div className={styles.inferenceTimeSkeleton}>
            <div className={`${styles.cardLineSkeleton} ${styles.short}`} />
            <div className={styles.cardLineSkeleton} />
          </div>
        </div>
      );
    default:
      return <div className={styles.legendSkeleton} />;
  }
}

export const ReceiptFlowLoadingShell: React.FC<ReceiptFlowLoadingShellProps> = ({
  layoutVars,
  variant = "within",
  message,
  isError = false,
}) => {
  const mergedLayoutVars = { ...DEFAULT_LAYOUT_VARS, ...layoutVars };

  return (
    <ReceiptFlowShell
      layoutVars={mergedLayoutVars}
      isTransitioning={false}
      queue={<QueueSkeleton />}
      center={<CenterSkeleton message={message} isError={isError} />}
      legend={<LegendSkeleton variant={variant} />}
    />
  );
};
