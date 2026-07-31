import React, { useMemo } from "react";
import styles from "./Validation.module.css";
import { ReviewEntry } from "./types";

interface ReviewGroup {
  key: string;
  imageId: string;
  receiptId: number;
  merchant: string;
  entries: ReviewEntry[];
  latest: ReviewEntry;
  resolved: boolean;
}

interface ReviewLogProps {
  entries: ReviewEntry[];
  currentImageId?: string;
  currentReceiptId?: number;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onJump: (entry: ReviewEntry) => void;
}

const displayTime = (timestamp: string): string => {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp || "unknown time";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
};

export const ReviewLog: React.FC<ReviewLogProps> = ({
  entries,
  currentImageId,
  currentReceiptId,
  loading = false,
  error = null,
  onRetry,
  onJump,
}) => {
  const groups = useMemo(() => {
    const byReceipt = new Map<string, ReviewEntry[]>();
    for (const entry of entries) {
      const key = `${entry.image_id}:${entry.receipt_id}`;
      const existing = byReceipt.get(key);
      if (existing) existing.push(entry);
      else byReceipt.set(key, [entry]);
    }

    return Array.from(byReceipt, ([key, history]): ReviewGroup => {
      const sorted = [...history].sort((left, right) =>
        right.ts.localeCompare(left.ts),
      );
      const latest = sorted[0];
      return {
        key,
        imageId: latest.image_id,
        receiptId: latest.receipt_id,
        merchant: latest.merchant || "Unknown merchant",
        entries: sorted,
        latest,
        resolved: latest.verdict === "resolved",
      };
    }).sort((left, right) => right.latest.ts.localeCompare(left.latest.ts));
  }, [entries]);

  const resolvedCount = groups.filter((group) => group.resolved).length;

  return (
    <section className={styles.reviewLogPanel} data-testid="review-log-panel">
      <header className={styles.reviewLogHeader}>
        <div>
          <span className={styles.eyebrow}>Review log</span>
          <strong>Receipt history</strong>
        </div>
        <div className={styles.logCounts}>
          <span>{groups.length} receipts</span>
          <span data-kind="resolved">{resolvedCount} resolved</span>
        </div>
      </header>

      {error ? (
        <div className={styles.compactState} role="alert">
          <span>{error}</span>
          {onRetry ? (
            <button type="button" onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
      ) : null}

      {loading ? (
        <div className={styles.inlineLoading}>Loading review history…</div>
      ) : groups.length === 0 && !error ? (
        <div className={styles.inlineLoading}>No reviews recorded yet.</div>
      ) : (
        <ul className={styles.reviewGroups}>
          {groups.map((group) => {
            const active =
              group.imageId === currentImageId &&
              group.receiptId === currentReceiptId;
            return (
              <li key={group.key} data-active={active}>
                <button
                  type="button"
                  className={styles.reviewJump}
                  onClick={() => onJump(group.latest)}
                  aria-label={`Jump to ${group.merchant} receipt ${group.receiptId}`}
                >
                  <span>
                    <strong>{group.merchant}</strong>
                    <small>Receipt {group.receiptId}</small>
                  </span>
                  <span className={styles.historyBadge}>
                    {group.entries.length} {group.entries.length === 1 ? "event" : "events"}
                  </span>
                  <span className={styles.logVerdict} data-status={group.latest.verdict}>
                    {group.latest.verdict}
                  </span>
                </button>
                <ul className={styles.reviewHistory}>
                  {group.entries.slice(0, 3).map((entry) => (
                    <li key={`${entry.ts}-${entry.verdict}`}>
                      <button type="button" onClick={() => onJump(entry)}>
                        <span data-status={entry.verdict}>{entry.verdict}</span>
                        <span>{entry.note || "No note"}</span>
                        <small>{displayTime(entry.ts)}</small>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};

export default ReviewLog;
