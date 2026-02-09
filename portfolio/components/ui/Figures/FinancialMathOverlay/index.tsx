import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../../../services/api";
import {
  FinancialMathReceipt,
  FinancialMathEquation,
  FinancialMathResponse,
} from "../../../../types/api";
import styles from "./FinancialMathOverlay.module.css";

function getEquationBorderClass(equation: FinancialMathEquation): string {
  const hasInvalid = equation.involved_words.some(
    (w) => w.decision === "INVALID"
  );
  const hasReview = equation.involved_words.some(
    (w) => w.decision === "NEEDS_REVIEW"
  );
  if (hasInvalid) return styles.borderInvalid;
  if (hasReview) return styles.borderReview;
  return styles.borderValid;
}

function getDecisionClass(decision: string): string {
  if (decision === "INVALID") return styles.decisionInvalid;
  if (decision === "NEEDS_REVIEW") return styles.decisionReview;
  return styles.decisionValid;
}

function getBadgeStyle(equation: FinancialMathEquation): React.CSSProperties {
  const hasInvalid = equation.involved_words.some(
    (w) => w.decision === "INVALID"
  );
  const hasReview = equation.involved_words.some(
    (w) => w.decision === "NEEDS_REVIEW"
  );
  if (hasInvalid)
    return { background: "var(--color-red)", color: "#fff" };
  if (hasReview)
    return { background: "var(--color-yellow)", color: "#000" };
  return { background: "var(--color-green)", color: "#fff" };
}

function formatValue(v: number | string): string {
  if (typeof v === "number") return v.toFixed(2);
  return String(v);
}

function EquationCard({ equation }: { equation: FinancialMathEquation }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`${styles.equationCard} ${getEquationBorderClass(equation)}`}>
      <div
        className={styles.equationHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={styles.issueBadge} style={getBadgeStyle(equation)}>
          {equation.issue_type.replace(/_/g, " ")}
        </span>
        <span className={styles.equationDescription}>
          {equation.description}
        </span>
        <span className={`${styles.chevron} ${expanded ? styles.chevronOpen : ""}`}>
          &#9654;
        </span>
      </div>

      {expanded && (
        <div className={styles.equationDetails}>
          <div className={styles.equationValues}>
            <div>
              <div className={styles.valueLabel}>Expected</div>
              <div className={styles.valueNumber}>
                {formatValue(equation.expected_value)}
              </div>
            </div>
            <div>
              <div className={styles.valueLabel}>Actual</div>
              <div className={styles.valueNumber}>
                {formatValue(equation.actual_value)}
              </div>
            </div>
            <div>
              <div className={styles.valueLabel}>Diff</div>
              <div
                className={`${styles.valueNumber} ${
                  typeof equation.difference === "number" && equation.difference > 0
                    ? styles.diffPositive
                    : styles.diffNegative
                }`}
              >
                {typeof equation.difference === "number"
                  ? (equation.difference > 0 ? "+" : "") +
                    equation.difference.toFixed(2)
                  : equation.difference}
              </div>
            </div>
          </div>

          <div className={styles.wordGrid} style={{ marginTop: "0.75rem" }}>
            {equation.involved_words.map((word) => (
              <div className={styles.wordRow} key={`${word.line_id}-${word.word_id}`}>
                <span className={styles.wordText}>{word.word_text}</span>
                <span className={styles.wordLabel}>{word.current_label}</span>
                <span className={`${styles.wordDecision} ${getDecisionClass(word.decision)}`}>
                  {word.decision}
                </span>
                <span className={styles.wordConfidence}>{word.confidence}</span>
                <div className={styles.wordReasoning}>{word.reasoning}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ReceiptCard({ receipt }: { receipt: FinancialMathReceipt }) {
  const [expanded, setExpanded] = useState(true);

  const statusColor = receipt.summary.has_invalid
    ? "var(--color-red)"
    : receipt.summary.has_needs_review
    ? "var(--color-yellow)"
    : "var(--color-green)";

  return (
    <div className={styles.receiptCard}>
      <div
        className={styles.receiptHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={styles.merchantName}>{receipt.merchant_name}</span>
        <div className={styles.receiptMeta}>
          <span
            className={styles.statusDot}
            style={{ background: statusColor }}
          />
          <span className={styles.equationCount}>
            {receipt.summary.total_equations} equation
            {receipt.summary.total_equations !== 1 ? "s" : ""}
          </span>
          <span className={`${styles.chevron} ${expanded ? styles.chevronOpen : ""}`}>
            &#9654;
          </span>
        </div>
      </div>

      {expanded && (
        <div className={styles.equationList}>
          {receipt.equations.map((eq, i) => (
            <EquationCard key={i} equation={eq} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function FinancialMathOverlay() {
  const [data, setData] = useState<FinancialMathResponse | null>(null);
  const [receipts, setReceipts] = useState<FinancialMathReceipt[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [seed, setSeed] = useState<number | undefined>(undefined);

  const fetchData = useCallback(
    async (currentOffset: number, currentSeed?: number) => {
      try {
        const result = await api.fetchLabelEvaluatorFinancialMath(
          20,
          currentSeed,
          currentOffset
        );
        return result;
      } catch (err) {
        throw err;
      }
    },
    []
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchData(0)
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setReceipts(result.receipts);
        setSeed(result.seed);
        setOffset(result.receipts.length);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load data");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fetchData]);

  const handleLoadMore = useCallback(async () => {
    if (loadingMore || !data?.has_more) return;
    setLoadingMore(true);
    try {
      const result = await fetchData(offset, seed);
      setReceipts((prev) => [...prev, ...result.receipts]);
      setData(result);
      setOffset((prev) => prev + result.receipts.length);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load more");
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, data, offset, seed, fetchData]);

  if (loading) {
    return <div className={styles.loading}>Loading financial math checks...</div>;
  }

  if (error) {
    return <div className={styles.error}>{error}</div>;
  }

  if (!receipts.length) {
    return <div className={styles.loading}>No financial math data available.</div>;
  }

  const totalEquations = receipts.reduce(
    (sum, r) => sum + r.summary.total_equations,
    0
  );
  const receiptsWithErrors = receipts.filter(
    (r) => r.summary.has_invalid || r.summary.has_needs_review
  ).length;

  return (
    <div className={styles.container}>
      <div className={styles.summary}>
        <div className={styles.summaryCard}>
          <div className={styles.summaryLabel}>Receipts</div>
          <div className={styles.summaryValue}>{receipts.length}</div>
        </div>
        <div className={styles.summaryCard}>
          <div className={styles.summaryLabel}>Equations</div>
          <div className={styles.summaryValue}>{totalEquations}</div>
        </div>
        <div className={styles.summaryCard}>
          <div className={styles.summaryLabel}>With Issues</div>
          <div className={styles.summaryValue}>{receiptsWithErrors}</div>
        </div>
      </div>

      {receipts.map((receipt) => (
        <ReceiptCard
          key={`${receipt.image_id}-${receipt.receipt_id}`}
          receipt={receipt}
        />
      ))}

      {data?.has_more && (
        <button
          className={styles.loadMore}
          onClick={handleLoadMore}
          disabled={loadingMore}
        >
          {loadingMore ? "Loading..." : "Load More"}
        </button>
      )}
    </div>
  );
}
