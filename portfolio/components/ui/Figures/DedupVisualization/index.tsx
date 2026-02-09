import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../../../services/api";
import { DedupReceipt, DedupResolution, DedupResponse } from "../../../../types/api";
import styles from "./DedupVisualization.module.css";

// Decision badge component
const DecisionBadge: React.FC<{ decision: string }> = ({ decision }) => {
  const normalized = decision.toUpperCase();
  let className = styles.decisionBadge;
  if (normalized === "VALID") className += ` ${styles.valid}`;
  else if (normalized === "INVALID") className += ` ${styles.invalid}`;
  else className += ` ${styles.needsReview}`;
  return <span className={className}>{decision}</span>;
};

// Confidence badge component
const ConfidenceBadge: React.FC<{ confidence: string }> = ({ confidence }) => {
  const normalized = confidence.toLowerCase();
  let className = styles.confidenceBadge;
  if (normalized === "high") className += ` ${styles.high}`;
  else if (normalized === "medium") className += ` ${styles.medium}`;
  else className += ` ${styles.low}`;
  return <span className={className}>{confidence}</span>;
};

// Resolution reason badge
const ReasonBadge: React.FC<{ reason: string }> = ({ reason }) => {
  let className = styles.reasonBadge;
  if (reason === "higher_confidence") className += ` ${styles.higherConfidence}`;
  else if (reason === "financial_label_priority") className += ` ${styles.financialLabelPriority}`;
  else className += ` ${styles.currencyPriorityDefault}`;

  const label = reason.replace(/_/g, " ");
  return <span className={className}>{label}</span>;
};

// Resolution card for a single conflict word
const ResolutionCard: React.FC<{ resolution: DedupResolution }> = ({ resolution }) => {
  const currencyWins = resolution.winner === "currency";

  return (
    <div className={styles.resolutionCard}>
      <div className={styles.resolutionHeader}>
        <div className={styles.wordInfo}>
          <span className={styles.wordText}>{resolution.word_text}</span>
          <span className={styles.currentLabel}>{resolution.current_label}</span>
        </div>
        <span className={styles.appliedLabel}>{resolution.applied_label}</span>
      </div>

      <div className={styles.evaluatorColumns}>
        <div className={`${styles.evaluatorColumn} ${styles.currency} ${currencyWins ? styles.winner : ""}`}>
          <div className={styles.evaluatorLabel}>
            {currencyWins && <span className={styles.winnerIcon}>&#9733;</span>}
            Currency
          </div>
          <div className={styles.evaluatorDetail}>
            <div className={styles.detailRow}>
              <span className={styles.detailKey}>Decision</span>
              <DecisionBadge decision={resolution.currency_decision} />
            </div>
            <div className={styles.detailRow}>
              <span className={styles.detailKey}>Confidence</span>
              <ConfidenceBadge confidence={resolution.currency_confidence} />
            </div>
          </div>
        </div>

        <div className={`${styles.evaluatorColumn} ${styles.metadata} ${!currencyWins ? styles.winner : ""}`}>
          <div className={styles.evaluatorLabel}>
            {!currencyWins && <span className={styles.winnerIcon}>&#9733;</span>}
            Metadata
          </div>
          <div className={styles.evaluatorDetail}>
            <div className={styles.detailRow}>
              <span className={styles.detailKey}>Decision</span>
              <DecisionBadge decision={resolution.metadata_decision} />
            </div>
            <div className={styles.detailRow}>
              <span className={styles.detailKey}>Confidence</span>
              <ConfidenceBadge confidence={resolution.metadata_confidence} />
            </div>
          </div>
        </div>
      </div>

      <div className={styles.resolutionFooter}>
        <ReasonBadge reason={resolution.resolution_reason} />
      </div>
    </div>
  );
};

// Donut chart for winner breakdown
const WinnerDonut: React.FC<{ currency: number; metadata: number }> = ({ currency, metadata }) => {
  const total = currency + metadata;
  if (total === 0) {
    return (
      <div className={styles.winnerDonut}>
        <svg width="60" height="60" viewBox="0 0 60 60" className={styles.donutSvg}>
          <circle cx="30" cy="30" r="24" fill="none" stroke="var(--text-color)" strokeWidth="8" opacity="0.1" />
        </svg>
        <div className={styles.donutLegend}>
          <span className={styles.donutLegendItem}>No winners</span>
        </div>
      </div>
    );
  }

  const currencyAngle = (currency / total) * 360;
  const r = 24;
  const cx = 30;
  const cy = 30;
  const circumference = 2 * Math.PI * r;
  const currencyDash = (currencyAngle / 360) * circumference;
  const metadataDash = circumference - currencyDash;

  return (
    <div className={styles.winnerDonut}>
      <svg width="60" height="60" viewBox="0 0 60 60" className={styles.donutSvg}>
        <circle
          cx={cx} cy={cy} r={r}
          fill="none"
          stroke="var(--color-green)"
          strokeWidth="8"
          strokeDasharray={`${currencyDash} ${metadataDash}`}
          strokeDashoffset={circumference / 4}
          strokeLinecap="butt"
        />
        <circle
          cx={cx} cy={cy} r={r}
          fill="none"
          stroke="var(--color-purple)"
          strokeWidth="8"
          strokeDasharray={`${metadataDash} ${currencyDash}`}
          strokeDashoffset={circumference / 4 - currencyDash}
          strokeLinecap="butt"
        />
      </svg>
      <div className={styles.donutLegend}>
        <div className={styles.donutLegendItem}>
          <span className={styles.donutDot} style={{ background: "var(--color-green)" }} />
          Currency: {currency}
        </div>
        <div className={styles.donutLegendItem}>
          <span className={styles.donutDot} style={{ background: "var(--color-purple)" }} />
          Metadata: {metadata}
        </div>
      </div>
    </div>
  );
};

// Detail panel for a selected receipt
const ReceiptDetail: React.FC<{ receipt: DedupReceipt }> = ({ receipt }) => {
  const stats = receipt.dedup_stats;
  const summary = receipt.summary;
  const resolutions = receipt.resolutions;

  const maxBreakdown = Math.max(
    summary.resolution_breakdown.higher_confidence,
    summary.resolution_breakdown.financial_label_priority,
    summary.resolution_breakdown.currency_priority_default,
    1
  );

  return (
    <div className={styles.detailPanel}>
      {/* Stats Grid */}
      {stats && (
        <div className={styles.statsGrid}>
          <div className={`${styles.statCard} ${stats.conflicting_words > 0 ? styles.highlight : ""}`}>
            <span className={styles.statLabel}>Conflicting Words</span>
            <span className={styles.statValue}>{stats.conflicting_words}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statLabel}>Overlapping Words</span>
            <span className={styles.statValue}>{stats.overlapping_words}</span>
          </div>
          <div className={`${styles.statCard} ${stats.total_corrections_applied > 0 ? styles.highlight : ""}`}>
            <span className={styles.statLabel}>Corrections Applied</span>
            <span className={styles.statValue}>{stats.total_corrections_applied}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statLabel}>Currency Invalid</span>
            <span className={styles.statValue}>{stats.currency_invalid_count}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statLabel}>Metadata Invalid</span>
            <span className={styles.statValue}>{stats.metadata_invalid_count}</span>
          </div>
          <div className={styles.statCard}>
            <span className={styles.statLabel}>Dedup Removed</span>
            <span className={styles.statValue}>{stats.dedup_removed}</span>
          </div>
        </div>
      )}

      {/* Resolution Cards */}
      {resolutions.length > 0 ? (
        <div className={styles.resolutionsSection}>
          <h4 className={styles.sectionTitle}>Conflict Resolutions ({resolutions.length})</h4>
          {resolutions.map((r, idx) => (
            <ResolutionCard key={`${r.line_id}-${r.word_id}-${idx}`} resolution={r} />
          ))}
        </div>
      ) : (
        <div className={styles.noConflicts}>
          No conflicts -- all evaluators agreed
        </div>
      )}

      {/* Summary */}
      {summary.has_conflicts && (
        <div className={styles.summarySection}>
          <h4 className={styles.sectionTitle}>Summary</h4>
          <div className={styles.summaryGrid}>
            <div className={styles.summaryCard}>
              <div className={styles.summaryCardTitle}>Resolution Breakdown</div>
              <div className={styles.breakdownBar}>
                <span className={styles.breakdownLabel}>Higher confidence</span>
                <div className={styles.breakdownTrack}>
                  <div
                    className={`${styles.breakdownFill} ${styles.blue}`}
                    style={{ width: `${(summary.resolution_breakdown.higher_confidence / maxBreakdown) * 100}%` }}
                  />
                </div>
                <span className={styles.breakdownValue}>{summary.resolution_breakdown.higher_confidence}</span>
              </div>
              <div className={styles.breakdownBar}>
                <span className={styles.breakdownLabel}>Financial priority</span>
                <div className={styles.breakdownTrack}>
                  <div
                    className={`${styles.breakdownFill} ${styles.green}`}
                    style={{ width: `${(summary.resolution_breakdown.financial_label_priority / maxBreakdown) * 100}%` }}
                  />
                </div>
                <span className={styles.breakdownValue}>{summary.resolution_breakdown.financial_label_priority}</span>
              </div>
              <div className={styles.breakdownBar}>
                <span className={styles.breakdownLabel}>Currency default</span>
                <div className={styles.breakdownTrack}>
                  <div
                    className={`${styles.breakdownFill} ${styles.gray}`}
                    style={{ width: `${(summary.resolution_breakdown.currency_priority_default / maxBreakdown) * 100}%` }}
                  />
                </div>
                <span className={styles.breakdownValue}>{summary.resolution_breakdown.currency_priority_default}</span>
              </div>
            </div>

            <div className={styles.summaryCard}>
              <div className={styles.summaryCardTitle}>Winner Breakdown</div>
              <WinnerDonut
                currency={summary.winner_breakdown.currency}
                metadata={summary.winner_breakdown.metadata}
              />
            </div>
          </div>

          {summary.labels_affected.length > 0 && (
            <div className={styles.labelsAffected}>
              {summary.labels_affected.map((label) => (
                <span key={label} className={styles.labelTag}>{label}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const DedupVisualization: React.FC = () => {
  const [data, setData] = useState<DedupResponse | null>(null);
  const [receipts, setReceipts] = useState<DedupReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const [conflictsOnly, setConflictsOnly] = useState(false);
  const [seed, setSeed] = useState<number | undefined>(undefined);

  // Fetch initial data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.fetchLabelEvaluatorDedup(20);
        if (response && response.receipts) {
          setReceipts(response.receipts);
          setData(response);
          setSeed(response.seed);
        }
      } catch (err) {
        console.error("Failed to fetch dedup data:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  // Load more
  const loadMore = async () => {
    if (!data?.has_more || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await api.fetchLabelEvaluatorDedup(20, seed, receipts.length);
      if (response && response.receipts) {
        setReceipts((prev) => [...prev, ...response.receipts]);
        setData(response);
      }
    } catch (err) {
      console.error("Failed to load more dedup data:", err);
    } finally {
      setLoadingMore(false);
    }
  };

  // Filter receipts
  const filteredReceipts = useMemo(() => {
    if (!conflictsOnly) return receipts;
    return receipts.filter(
      (r) => r.summary.has_conflicts || r.resolutions.length > 0
    );
  }, [receipts, conflictsOnly]);

  // Clamp selectedIndex
  useEffect(() => {
    if (selectedIndex >= filteredReceipts.length && filteredReceipts.length > 0) {
      setSelectedIndex(0);
    }
  }, [filteredReceipts.length, selectedIndex]);

  const selectedReceipt = filteredReceipts[selectedIndex] ?? null;

  if (loading) {
    return <div className={styles.loading}>Loading dedup data...</div>;
  }

  if (error) {
    return <div className={styles.error}>Error: {error}</div>;
  }

  if (receipts.length === 0) {
    return <div className={styles.loading}>No dedup data available</div>;
  }

  return (
    <div className={styles.container}>
      {/* Filter bar */}
      <div className={styles.filterBar}>
        <label className={styles.filterToggle}>
          <input
            type="checkbox"
            checked={conflictsOnly}
            onChange={(e) => setConflictsOnly(e.target.checked)}
          />
          Has conflicts only
        </label>
        <span className={styles.receiptCount}>
          {filteredReceipts.length} of {receipts.length} receipts
        </span>
      </div>

      {/* Receipt list */}
      <div className={styles.receiptList}>
        {filteredReceipts.map((receipt, idx) => {
          const corrections = receipt.dedup_stats?.total_corrections_applied ?? 0;
          const conflicts = receipt.dedup_stats?.conflicting_words ?? 0;

          return (
            <div
              key={`${receipt.image_id}-${receipt.receipt_id}`}
              className={`${styles.receiptItem} ${idx === selectedIndex ? styles.selected : ""} ${conflicts > 0 ? styles.hasConflicts : ""}`}
              onClick={() => setSelectedIndex(idx)}
            >
              <span className={styles.merchantName}>{receipt.merchant_name}</span>
              <div className={styles.receiptBadges}>
                {conflicts > 0 && (
                  <span className={styles.conflictBadge}>
                    {conflicts} conflict{conflicts !== 1 ? "s" : ""}
                  </span>
                )}
                {corrections > 0 && (
                  <span className={styles.correctionBadge}>
                    {corrections} correction{corrections !== 1 ? "s" : ""}
                  </span>
                )}
                {receipt.dedup_stats && (
                  <span className={styles.strategyBadge}>
                    {receipt.dedup_stats.resolution_strategy.replace(/_/g, " ")}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Load More */}
      {data?.has_more && (
        <div className={styles.loadMore}>
          <button
            className={styles.loadMoreButton}
            onClick={loadMore}
            disabled={loadingMore}
          >
            {loadingMore ? "Loading..." : "Load More"}
          </button>
        </div>
      )}

      {/* Detail panel for selected receipt */}
      {selectedReceipt && <ReceiptDetail receipt={selectedReceipt} />}
    </div>
  );
};

export default DedupVisualization;
