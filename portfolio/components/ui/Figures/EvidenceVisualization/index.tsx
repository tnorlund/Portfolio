import React, { useEffect, useState } from "react";
import { api } from "../../../../services/api";
import {
  EvidenceReceipt,
  EvidenceIssue,
  EvidenceItem,
} from "../../../../types/api";
import styles from "./EvidenceVisualization.module.css";

// Decision badge component
const DecisionBadge: React.FC<{ decision: string }> = ({ decision }) => {
  const cls =
    decision === "VALID"
      ? styles.badgeValid
      : decision === "INVALID"
        ? styles.badgeInvalid
        : styles.badgeNeedsReview;
  return <span className={`${styles.badge} ${cls}`}>{decision}</span>;
};

// Confidence badge component
const ConfidenceBadge: React.FC<{ confidence: string }> = ({ confidence }) => {
  const cls =
    confidence === "high"
      ? styles.badgeHigh
      : confidence === "medium"
        ? styles.badgeMedium
        : styles.badgeLow;
  return <span className={`${styles.badge} ${cls}`}>{confidence}</span>;
};

// Consensus gauge: horizontal gradient bar from -1 (red) to +1 (green)
const ConsensusGauge: React.FC<{ score: number }> = ({ score }) => {
  // Map score from [-1, 1] to [0%, 100%]
  const position = ((score + 1) / 2) * 100;

  return (
    <div className={styles.consensusSection}>
      <div className={styles.consensusLabel}>
        <span>Consensus</span>
        <span className={styles.consensusScore}>{score.toFixed(3)}</span>
      </div>
      <div className={styles.consensusGauge}>
        <div className={styles.consensusTrack} />
        <div
          className={styles.consensusMarker}
          style={{ left: `${position}%` }}
        />
      </div>
    </div>
  );
};

// Evidence row component
const EvidenceRow: React.FC<{ item: EvidenceItem }> = ({ item }) => {
  return (
    <div className={styles.evidenceRow}>
      <span
        className={`${styles.validityIndicator} ${item.label_valid ? styles.validitySupports : styles.validityContradicts}`}
      >
        {item.label_valid ? "\u2713" : "\u2717"}
      </span>
      <span className={styles.evidenceWordText} title={item.word_text}>
        {item.word_text}
      </span>
      <div className={styles.similarityBarWrapper}>
        <div
          className={styles.similarityBarFill}
          style={{ width: `${item.similarity_score * 100}%` }}
        />
      </div>
      <span className={styles.similarityScore}>
        {item.similarity_score.toFixed(3)}
      </span>
      {item.is_same_merchant && (
        <span className={`${styles.evidenceBadge} ${styles.sameMerchantBadge}`}>
          Same merchant
        </span>
      )}
      <span className={`${styles.evidenceBadge} ${styles.sourceBadge}`}>
        {item.evidence_source}
      </span>
    </div>
  );
};

// Issue card component
const IssueCard: React.FC<{ issue: EvidenceIssue }> = ({ issue }) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={styles.issueCard}>
      <div className={styles.issueHeader}>
        <span className={styles.wordText}>{issue.word_text}</span>
        <div className={styles.labelArrow}>
          <span className={styles.labelCurrent}>
            {issue.current_label ?? "none"}
          </span>
          <span className={styles.arrowIcon}>&rarr;</span>
          <span className={styles.labelSuggested}>
            {issue.suggested_label}
          </span>
        </div>
        <div className={styles.badgeRow}>
          <DecisionBadge decision={issue.decision} />
          <ConfidenceBadge confidence={issue.confidence} />
        </div>
      </div>

      <ConsensusGauge score={issue.consensus_score} />

      {issue.evidence.length > 0 ? (
        <div className={styles.evidenceSection}>
          <div className={styles.evidenceTitle}>
            Evidence ({issue.evidence.length} of {issue.similar_word_count}{" "}
            similar)
          </div>
          <div className={styles.evidenceList}>
            {issue.evidence.map((item, idx) => (
              <EvidenceRow key={idx} item={item} />
            ))}
          </div>
        </div>
      ) : (
        <div className={styles.noEvidence}>No evidence available</div>
      )}

      <button
        className={styles.reasoningToggle}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "Hide reasoning" : "Show reasoning"}
      </button>
      {expanded && (
        <div className={styles.reasoningText}>{issue.reasoning}</div>
      )}
    </div>
  );
};

const EvidenceVisualization: React.FC = () => {
  const [receipts, setReceipts] = useState<EvidenceReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(0);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.fetchLabelEvaluatorEvidence();
        if (response && response.receipts) {
          setReceipts(response.receipts);
        }
      } catch (err) {
        console.error("Failed to fetch evidence data:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  if (loading) {
    return <div className={styles.loading}>Loading evidence data...</div>;
  }

  if (error) {
    return <div className={styles.error}>Error: {error}</div>;
  }

  if (receipts.length === 0) {
    return <div className={styles.loading}>No evidence data available</div>;
  }

  const selected = receipts[selectedIndex];

  // Aggregate stats across all receipts
  const totalIssues = receipts.reduce(
    (sum, r) => sum + r.summary.total_issues_reviewed,
    0
  );
  const allDecisions = receipts.reduce(
    (acc, r) => {
      acc.VALID += r.summary.decisions.VALID;
      acc.INVALID += r.summary.decisions.INVALID;
      acc.NEEDS_REVIEW += r.summary.decisions.NEEDS_REVIEW;
      return acc;
    },
    { VALID: 0, INVALID: 0, NEEDS_REVIEW: 0 }
  );
  const avgConsensus =
    receipts.length > 0
      ? receipts.reduce((sum, r) => sum + r.summary.avg_consensus_score, 0) /
        receipts.length
      : 0;

  return (
    <div className={styles.container}>
      {/* Summary stats */}
      <div className={styles.summaryBar}>
        <div className={styles.summaryItem}>
          <span className={styles.summaryValue}>{totalIssues}</span>
          <span className={styles.summaryLabel}>Issues Reviewed</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={styles.summaryValue}>
            {avgConsensus.toFixed(3)}
          </span>
          <span className={styles.summaryLabel}>Avg Consensus</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={styles.summaryValue}>{allDecisions.VALID}</span>
          <span className={styles.summaryLabel}>Valid</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={styles.summaryValue}>{allDecisions.INVALID}</span>
          <span className={styles.summaryLabel}>Invalid</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={styles.summaryValue}>
            {allDecisions.NEEDS_REVIEW}
          </span>
          <span className={styles.summaryLabel}>Needs Review</span>
        </div>
      </div>

      {/* Receipt selector */}
      <div className={styles.receiptSelector}>
        {receipts.map((r, idx) => (
          <button
            key={`${r.image_id}-${r.receipt_id}`}
            className={`${styles.receiptTab} ${idx === selectedIndex ? styles.receiptTabActive : ""}`}
            onClick={() => setSelectedIndex(idx)}
          >
            {r.merchant_name}
            <span className={styles.receiptTabCount}>
              ({r.issues_with_evidence.length})
            </span>
          </button>
        ))}
      </div>

      {/* Issue cards for selected receipt */}
      <div className={styles.issueList}>
        {selected.issues_with_evidence.map((issue) => (
          <IssueCard
            key={`${issue.line_id}-${issue.word_id}`}
            issue={issue}
          />
        ))}
      </div>
    </div>
  );
};

export default EvidenceVisualization;
