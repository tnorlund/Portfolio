import React, { useEffect, useState } from "react";
import { api } from "../../../../services/api";
import { DiffReceipt, DiffWord } from "../../../../types/api";
import styles from "./DiffVisualization.module.css";

const CHANGE_SOURCE_COLORS: Record<string, string> = {
  financial_validation: "#3b82f6",
  currency_evaluation: "#10b981",
  metadata_evaluation: "#8b5cf6",
  flag_geometric_anomalies: "#f59e0b",
};

const CHANGE_SOURCE_LABELS: Record<string, string> = {
  financial_validation: "Financial",
  currency_evaluation: "Currency",
  metadata_evaluation: "Metadata",
  flag_geometric_anomalies: "Geometric",
};

function LabelDisplay({ label }: { label: string | null }) {
  if (label === null) {
    return <span className={styles.labelNull}>none</span>;
  }
  return <span>{label}</span>;
}

function SourceTag({ source }: { source: string }) {
  const color = CHANGE_SOURCE_COLORS[source] || "var(--text-color)";
  const label = CHANGE_SOURCE_LABELS[source] || source;
  return (
    <span className={styles.sourceTag} style={{ backgroundColor: color }}>
      {label}
    </span>
  );
}

interface WordRowProps {
  word: DiffWord;
  expandedKey: string | null;
  onToggle: (key: string) => void;
}

function WordRow({ word, expandedKey, onToggle }: WordRowProps) {
  const key = `${word.line_id}_${word.word_id}`;
  const isExpanded = expandedKey === key;

  if (!word.changed) {
    return (
      <tr className={styles.wordRowUnchanged}>
        <td className={styles.wordText}>{word.text}</td>
        <td className={styles.labelCell}>
          <LabelDisplay label={word.before_label} />
        </td>
        <td className={styles.arrow}></td>
        <td className={styles.labelCell}>
          <LabelDisplay label={word.after_label} />
        </td>
        <td></td>
      </tr>
    );
  }

  const bgColor = word.change_source
    ? `${CHANGE_SOURCE_COLORS[word.change_source]}18`
    : undefined;

  return (
    <>
      <tr
        className={styles.wordRowChanged}
        style={{ backgroundColor: bgColor }}
        onClick={() => onToggle(key)}
      >
        <td className={styles.wordText}>{word.text}</td>
        <td className={styles.labelCell}>
          <LabelDisplay label={word.before_label} />
        </td>
        <td className={styles.arrow}>&rarr;</td>
        <td className={styles.labelCell}>
          <LabelDisplay label={word.after_label} />
        </td>
        <td>
          {word.change_source && <SourceTag source={word.change_source} />}
        </td>
      </tr>
      {isExpanded && word.reasoning && (
        <tr className={styles.reasoningRow}>
          <td colSpan={5}>
            <div className={styles.reasoningContent}>{word.reasoning}</div>
          </td>
        </tr>
      )}
    </>
  );
}

interface ReceiptCardProps {
  receipt: DiffReceipt;
  isOpen: boolean;
  onToggle: () => void;
  showOnlyChanges: boolean;
  sourceFilter: string | null;
}

function ReceiptCard({
  receipt,
  isOpen,
  onToggle,
  showOnlyChanges,
  sourceFilter,
}: ReceiptCardProps) {
  const [expandedWord, setExpandedWord] = useState<string | null>(null);

  const filteredWords = receipt.words.filter((w) => {
    if (showOnlyChanges && !w.changed) return false;
    if (sourceFilter && w.changed && w.change_source !== sourceFilter)
      return false;
    if (sourceFilter && !w.changed) return false;
    return true;
  });

  const handleToggleWord = (key: string) => {
    setExpandedWord((prev) => (prev === key ? null : key));
  };

  return (
    <div
      className={`${styles.receiptCard} ${isOpen ? styles.receiptCardSelected : ""}`}
    >
      <div className={styles.receiptHeader} onClick={onToggle}>
        <span className={styles.merchantName}>{receipt.merchant_name}</span>
        <div className={styles.receiptStats}>
          <span>{receipt.word_count} words</span>
          <span
            className={styles.changeBadge}
            style={{
              backgroundColor: receipt.change_count > 0 ? "#f59e0b22" : undefined,
              color: receipt.change_count > 0 ? "#f59e0b" : undefined,
            }}
          >
            {receipt.change_count} changed
          </span>
        </div>
      </div>
      {isOpen && (
        <div className={styles.wordTableWrapper}>
          <table className={styles.wordTable}>
            <thead>
              <tr>
                <th>Word</th>
                <th>Before</th>
                <th></th>
                <th>After</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {filteredWords.map((word) => (
                <WordRow
                  key={`${word.line_id}_${word.word_id}`}
                  word={word}
                  expandedKey={expandedWord}
                  onToggle={handleToggleWord}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function DiffVisualization() {
  const [receipts, setReceipts] = useState<DiffReceipt[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [seed, setSeed] = useState<number | undefined>(undefined);
  const [offset, setOffset] = useState(0);
  const [totalCount, setTotalCount] = useState(0);

  const [openReceipt, setOpenReceipt] = useState<string | null>(null);
  const [showOnlyChanges, setShowOnlyChanges] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);

  const loadData = async (loadOffset: number, existingSeed?: number) => {
    try {
      const data = await api.fetchLabelEvaluatorDiff(
        20,
        existingSeed,
        loadOffset
      );
      return data;
    } catch (err) {
      throw err;
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await loadData(0);
        if (cancelled) return;
        setReceipts(data.receipts as unknown as DiffReceipt[]);
        setHasMore(data.has_more);
        setSeed(data.seed);
        setOffset(data.receipts.length);
        setTotalCount(data.total_count);
      } catch (err) {
        if (!cancelled) setError(String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLoadMore = async () => {
    setLoadingMore(true);
    try {
      const data = await loadData(offset, seed);
      setReceipts((prev) => [
        ...prev,
        ...(data.receipts as unknown as DiffReceipt[]),
      ]);
      setHasMore(data.has_more);
      setOffset((prev) => prev + data.receipts.length);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoadingMore(false);
    }
  };

  const totalWords = receipts.reduce((sum, r) => sum + r.word_count, 0);
  const totalChanges = receipts.reduce((sum, r) => sum + r.change_count, 0);
  const changePercent =
    totalWords > 0 ? ((totalChanges / totalWords) * 100).toFixed(1) : "0";

  // Collect active sources
  const activeSources = new Set<string>();
  receipts.forEach((r) =>
    r.words.forEach((w) => {
      if (w.changed && w.change_source) activeSources.add(w.change_source);
    })
  );

  if (loading) return <div className={styles.loading}>Loading diff data...</div>;
  if (error) return <div className={styles.error}>Error: {error}</div>;

  return (
    <div className={styles.container}>
      <div className={styles.summaryBar}>
        <span className={styles.summaryItem}>
          <span className={styles.summaryValue}>{totalCount}</span> receipts
        </span>
        <span className={styles.summaryItem}>
          <span className={styles.summaryValue}>{totalWords}</span> words
        </span>
        <span className={styles.summaryItem}>
          <span className={styles.summaryValue}>{totalChanges}</span> changed (
          {changePercent}%)
        </span>
      </div>

      <div className={styles.filters}>
        <button
          className={`${styles.toggleButton} ${showOnlyChanges ? styles.toggleButtonActive : ""}`}
          onClick={() => setShowOnlyChanges((v) => !v)}
        >
          Show only changes
        </button>
        {Array.from(activeSources)
          .sort()
          .map((source) => (
            <button
              key={source}
              className={`${styles.filterButton} ${sourceFilter === source ? styles.filterButtonActive : ""}`}
              style={{ color: CHANGE_SOURCE_COLORS[source] }}
              onClick={() =>
                setSourceFilter((prev) => (prev === source ? null : source))
              }
            >
              {CHANGE_SOURCE_LABELS[source] || source}
            </button>
          ))}
      </div>

      <div className={styles.receiptList}>
        {receipts.map((receipt) => {
          const key = `${receipt.image_id}_${receipt.receipt_id}`;
          return (
            <ReceiptCard
              key={key}
              receipt={receipt}
              isOpen={openReceipt === key}
              onToggle={() =>
                setOpenReceipt((prev) => (prev === key ? null : key))
              }
              showOnlyChanges={showOnlyChanges}
              sourceFilter={sourceFilter}
            />
          );
        })}
      </div>

      {hasMore && (
        <div className={styles.loadMore}>
          <button
            className={styles.loadMoreButton}
            onClick={handleLoadMore}
            disabled={loadingMore}
          >
            {loadingMore ? "Loading..." : "Load More"}
          </button>
        </div>
      )}
    </div>
  );
}
