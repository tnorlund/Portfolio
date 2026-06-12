import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  ReceiptHealthCheck,
  ReceiptHealthReceipt,
  ReceiptHealthStatus,
  WithinReceiptWordDecision,
} from "../../../../types/api";
import {
  getBestImageUrl,
  getJpegFallbackUrl,
  usePreloadReceiptImages,
} from "../../../../utils/imageFormat";
import {
  DEFAULT_LAYOUT_VARS,
  ReceiptFlowLoadingShell,
} from "../ReceiptFlow/ReceiptFlowLoadingShell";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import styles from "./ReceiptHealthExplorer.module.css";

type CheckId = ReceiptHealthCheck["id"];

interface EvidenceWord {
  key: string;
  text: string;
  label: string | null;
  decision: string | null;
  bbox: { x: number; y: number; width: number; height: number };
}

const BATCH_SIZE = 12;
const INITIAL_SEED = 29;
const MAX_ISSUE_FETCHES = 3;

const CHECK_ORDER: CheckId[] = [
  "merchant_identity",
  "receipt_format",
  "financial_math",
];

const CHECK_LABELS: Record<CheckId, string> = {
  merchant_identity: "Merchant",
  receipt_format: "Format",
  financial_math: "Math",
};

const STATUS_LABELS: Record<ReceiptHealthStatus, string> = {
  pass: "Pass",
  review: "Review",
  fail: "Fail",
  not_applicable: "N/A",
};

const STATUS_CLASS: Record<ReceiptHealthStatus, string> = {
  pass: styles.statusPass,
  review: styles.statusReview,
  fail: styles.statusFail,
  not_applicable: styles.statusNeutral,
};

const STATUS_COLOR: Record<ReceiptHealthStatus, string> = {
  pass: "var(--color-green)",
  review: "var(--color-yellow)",
  fail: "var(--color-red)",
  not_applicable: "var(--text-color)",
};

const DECISION_COLOR: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
  CORRECTED: "var(--color-blue)",
};

function receiptKey(receipt: ReceiptHealthReceipt): string {
  return `${receipt.image_id}-${receipt.receipt_id}`;
}

function secondsLabel(seconds: number | null | undefined): string {
  if (seconds == null) return "No timing";
  if (seconds < 0.01) return "<10ms";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  return `${seconds.toFixed(1)}s`;
}

function statusClass(status: ReceiptHealthStatus): string {
  return `${styles.statusBadge} ${STATUS_CLASS[status]}`;
}

function issueLabel(count: number): string {
  if (count === 1) return "1 issue";
  return `${count} issues`;
}

function normalizeDecisionWord(
  decision: WithinReceiptWordDecision,
  prefix: string,
): EvidenceWord | null {
  if (!decision.bbox) return null;
  return {
    key: `${prefix}-${decision.line_id}-${decision.word_id}`,
    text: decision.word_text,
    label: decision.current_label,
    decision: decision.decision,
    bbox: decision.bbox,
  };
}

function evidenceForCheck(
  receipt: ReceiptHealthReceipt,
  checkId: CheckId,
): EvidenceWord[] {
  if (checkId === "merchant_identity") {
    return receipt.place_validation.decisions
      .map((decision) => normalizeDecisionWord(decision, "place"))
      .filter((word): word is EvidenceWord => Boolean(word));
  }

  if (checkId === "receipt_format") {
    return receipt.format_validation.decisions
      .map((decision) => normalizeDecisionWord(decision, "format"))
      .filter((word): word is EvidenceWord => Boolean(word));
  }

  const byWord = new Map<string, EvidenceWord>();
  for (let equationIndex = 0; equationIndex < receipt.financial_math.equations.length; equationIndex += 1) {
    const equation = receipt.financial_math.equations[equationIndex];
    for (const word of equation.involved_words) {
      if (!word.bbox) continue;
      const key = `math-${word.line_id}-${word.word_id}`;
      const current = byWord.get(key);
      const next: EvidenceWord = {
        key: `${key}-${equationIndex}`,
        text: word.word_text,
        label: word.current_label,
        decision: word.decision,
        bbox: word.bbox,
      };
      if (!current || current.decision === "VALID") {
        byWord.set(key, next);
      }
    }
  }
  return Array.from(byWord.values());
}

function receiptTitle(receipt: ReceiptHealthReceipt): string {
  return receipt.merchant_name || `Receipt ${receipt.receipt_id}`;
}

function ReceiptImage({
  receipt,
  activeCheck,
  onImageUnavailable,
}: {
  receipt: ReceiptHealthReceipt;
  activeCheck: ReceiptHealthCheck;
  onImageUnavailable: (receipt: ReceiptHealthReceipt) => void;
}) {
  const formatSupport = useImageFormatSupport();
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport, "medium");
  }, [formatSupport, receipt]);

  const evidence = useMemo(
    () => evidenceForCheck(receipt, activeCheck.id),
    [activeCheck.id, receipt],
  );

  const width = naturalSize?.width ?? receipt.width;
  const height = naturalSize?.height ?? receipt.height;
  const color = STATUS_COLOR[activeCheck.status];

  useEffect(() => {
    setImageFailed(false);
    setNaturalSize(null);
  }, [receipt.image_id, receipt.receipt_id]);

  if (!imageUrl || imageFailed) {
    return <div className={styles.imageLoading}>Loading receipt</div>;
  }

  return (
    <div className={styles.receiptImageStage}>
      <div className={styles.receiptImageFrame}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={imageUrl}
          alt={receiptTitle(receipt)}
          className={styles.receiptImage}
          onLoad={(event) => {
            const image = event.currentTarget;
            if (image.naturalWidth > 0 && image.naturalHeight > 0) {
              setNaturalSize({
                width: image.naturalWidth,
                height: image.naturalHeight,
              });
            }
          }}
          onError={(event) => {
            const fallback = getJpegFallbackUrl(receipt);
            if (event.currentTarget.src !== fallback) {
              event.currentTarget.src = fallback;
              return;
            }
            setImageFailed(true);
            onImageUnavailable(receipt);
          }}
        />
        <svg
          className={styles.overlay}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {evidence.map((word) => {
            const boxColor = DECISION_COLOR[word.decision ?? ""] ?? color;
            const x = word.bbox.x * width;
            const y = (1 - word.bbox.y - word.bbox.height) * height;
            const w = word.bbox.width * width;
            const h = word.bbox.height * height;

            return (
              <rect
                key={word.key}
                x={x}
                y={y}
                width={w}
                height={h}
                rx={2}
                fill={boxColor}
                fillOpacity={0.22}
                stroke={boxColor}
                strokeWidth={2}
                strokeOpacity={0.72}
              />
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function HealthSummary({ receipt }: { receipt: ReceiptHealthReceipt }) {
  return (
    <div className={styles.summaryGrid} aria-label="Receipt health summary">
      <div className={styles.summaryCell}>
        <span className={styles.summaryValue}>{receipt.summary.passed}</span>
        <span className={styles.summaryLabel}>Pass</span>
      </div>
      <div className={styles.summaryCell}>
        <span className={styles.summaryValue}>{receipt.summary.needs_review}</span>
        <span className={styles.summaryLabel}>Review</span>
      </div>
      <div className={styles.summaryCell}>
        <span className={styles.summaryValue}>{receipt.summary.failed}</span>
        <span className={styles.summaryLabel}>Fail</span>
      </div>
      <div className={styles.summaryCell}>
        <span className={styles.summaryValue}>{receipt.summary.not_applicable}</span>
        <span className={styles.summaryLabel}>N/A</span>
      </div>
    </div>
  );
}

function CheckButton({
  check,
  active,
  onSelect,
}: {
  check: ReceiptHealthCheck;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`${styles.checkButton} ${active ? styles.checkButtonActive : ""}`}
      onClick={onSelect}
      aria-pressed={active}
    >
      <span className={styles.checkHeader}>
        <span className={styles.checkTitle}>{check.title}</span>
        <span className={statusClass(check.status)}>{STATUS_LABELS[check.status]}</span>
      </span>
      <span className={styles.checkResult}>{check.result}</span>
      <span className={styles.checkMeta}>
        <span>{check.evidence_count} evidence</span>
        <span>{secondsLabel(check.duration_seconds)}</span>
        <span>{check.is_llm ? "LLM" : "rule"}</span>
      </span>
    </button>
  );
}

function CheckDetails({ check }: { check: ReceiptHealthCheck }) {
  return (
    <div className={styles.checkDetails}>
      <div className={styles.checkQuestion}>{check.question}</div>
      <div className={styles.validatesList}>
        {check.what_it_validates.map((field) => (
          <span key={field} className={styles.fieldBadge}>
            {field}
          </span>
        ))}
      </div>
    </div>
  );
}

function Issues({ receipt }: { receipt: ReceiptHealthReceipt }) {
  if (receipt.primary_issues.length === 0) {
    return (
      <div className={styles.issueEmpty}>
        No primary issues for this receipt.
      </div>
    );
  }

  return (
    <div className={styles.issueList}>
      {receipt.primary_issues.map((issue) => (
        <div key={`${issue.check_id}-${issue.message}`} className={styles.issueItem}>
          <span className={statusClass(issue.status)}>{STATUS_LABELS[issue.status]}</span>
          <span className={styles.issueMessage}>{issue.message}</span>
        </div>
      ))}
    </div>
  );
}

function ReceiptRail({
  receipts,
  currentIndex,
  onSelect,
}: {
  receipts: ReceiptHealthReceipt[];
  currentIndex: number;
  onSelect: (index: number) => void;
}) {
  return (
    <div className={styles.receiptRail} aria-label="Loaded receipts">
      {receipts.map((receipt, index) => (
        <button
          key={`${receipt.image_id}-${receipt.receipt_id}`}
          type="button"
          className={`${styles.railDot} ${index === currentIndex ? styles.railDotActive : ""} ${STATUS_CLASS[receipt.overall_status]}`}
          onClick={() => onSelect(index)}
          aria-label={`${receiptTitle(receipt)} status ${STATUS_LABELS[receipt.overall_status]}`}
          aria-current={index === currentIndex ? "true" : undefined}
        />
      ))}
    </div>
  );
}

export default function ReceiptHealthExplorer() {
  const { ref, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "400px",
  });

  const [receipts, setReceipts] = useState<ReceiptHealthReceipt[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [activeCheckId, setActiveCheckId] = useState<CheckId>("merchant_identity");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [findingIssue, setFindingIssue] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [unavailableImageKeys, setUnavailableImageKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const fetchedInitial = useRef(false);
  const formatSupport = useImageFormatSupport();

  usePreloadReceiptImages(
    receipts.slice(currentIndex, currentIndex + 2),
    formatSupport,
  );

  const loadReceipts = useCallback(
    async (offset: number) => {
      const response = await api.fetchReceiptHealth(BATCH_SIZE, INITIAL_SEED, offset);
      setTotalCount(response.total_count);
      setHasMore(response.has_more);
      setReceipts((current) => {
        if (offset === 0) return response.receipts;
        const existing = new Set(
          current.map((receipt) => `${receipt.image_id}-${receipt.receipt_id}`),
        );
        const next = response.receipts.filter(
          (receipt) => !existing.has(`${receipt.image_id}-${receipt.receipt_id}`),
        );
        return [...current, ...next];
      });
      return response.receipts;
    },
    [],
  );

  useEffect(() => {
    if (!nearViewport || fetchedInitial.current) return;
    fetchedInitial.current = true;

    let cancelled = false;
    setLoading(true);
    loadReceipts(0)
      .catch((err) => {
        if (!cancelled) {
          console.error("Failed to fetch receipt health data:", err);
          setError(err instanceof Error ? err.message : "Failed to load receipt health data");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [loadReceipts, nearViewport]);

  const currentReceipt = receipts[currentIndex] ?? null;
  const orderedChecks = useMemo(() => {
    if (!currentReceipt) return [];
    return CHECK_ORDER
      .map((id) => currentReceipt.checks.find((check) => check.id === id))
      .filter((check): check is ReceiptHealthCheck => Boolean(check));
  }, [currentReceipt]);

  const activeCheck = useMemo(() => {
    return (
      orderedChecks.find((check) => check.id === activeCheckId) ??
      orderedChecks[0] ??
      null
    );
  }, [activeCheckId, orderedChecks]);

  useEffect(() => {
    if (!activeCheck && orderedChecks[0]) {
      setActiveCheckId(orderedChecks[0].id);
    }
  }, [activeCheck, orderedChecks]);

  const selectReceipt = useCallback((index: number) => {
    setCurrentIndex(index);
    const receipt = receipts[index];
    const nextCheck = CHECK_ORDER.find((id) =>
      receipt?.checks.some((check) => check.id === id),
    );
    if (nextCheck) {
      setActiveCheckId(nextCheck);
    }
  }, [receipts]);

  const handleImageUnavailable = useCallback((receipt: ReceiptHealthReceipt) => {
    setUnavailableImageKeys((current) => {
      const key = receiptKey(receipt);
      if (current.has(key)) return current;
      const next = new Set(current);
      next.add(key);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!currentReceipt) return;
    if (!unavailableImageKeys.has(receiptKey(currentReceipt))) return;

    const replacementIndex = receipts.findIndex(
      (receipt, index) =>
        index !== currentIndex && !unavailableImageKeys.has(receiptKey(receipt)),
    );
    if (replacementIndex >= 0) {
      selectReceipt(replacementIndex);
    }
  }, [
    currentIndex,
    currentReceipt,
    receipts,
    selectReceipt,
    unavailableImageKeys,
  ]);

  const goPrevious = useCallback(() => {
    setCurrentIndex((index) => Math.max(0, index - 1));
  }, []);

  const goNext = useCallback(async () => {
    if (currentIndex < receipts.length - 1) {
      selectReceipt(currentIndex + 1);
      return;
    }

    if (!hasMore || loadingMore) return;
    setLoadingMore(true);
    try {
      const previousLength = receipts.length;
      const loaded = await loadReceipts(previousLength);
      if (loaded.length > 0) {
        setCurrentIndex(previousLength);
      }
    } catch (err) {
      console.error("Failed to fetch more receipt health data:", err);
      setError(err instanceof Error ? err.message : "Failed to load more receipts");
    } finally {
      setLoadingMore(false);
    }
  }, [currentIndex, hasMore, loadReceipts, loadingMore, receipts.length, selectReceipt]);

  const selectFirstIssueCheck = useCallback((receipt: ReceiptHealthReceipt) => {
    const issueCheckId = receipt.primary_issues[0]?.check_id;
    if (issueCheckId && receipt.checks.some((check) => check.id === issueCheckId)) {
      setActiveCheckId(issueCheckId);
      return;
    }

    const firstNonPassingCheck = receipt.checks.find((check) =>
      check.status === "fail" || check.status === "review"
    );
    if (firstNonPassingCheck) {
      setActiveCheckId(firstNonPassingCheck.id);
    }
  }, []);

  const goNextIssue = useCallback(async () => {
    if (findingIssue || loadingMore) return;
    setFindingIssue(true);
    try {
      let searchStart = currentIndex + 1;
      let loadedReceipts = receipts;

      for (let fetchCount = 0; fetchCount <= MAX_ISSUE_FETCHES; fetchCount += 1) {
        const issueIndex = loadedReceipts.findIndex(
          (receipt, index) =>
            index >= searchStart &&
            receipt.summary.issue_count > 0 &&
            !unavailableImageKeys.has(receiptKey(receipt)),
        );

        if (issueIndex >= 0) {
          setCurrentIndex(issueIndex);
          selectFirstIssueCheck(loadedReceipts[issueIndex]);
          return;
        }

        if (!hasMore) return;

        const previousLength = loadedReceipts.length;
        const loaded = await loadReceipts(previousLength);
        if (loaded.length === 0) return;

        const existing = new Set(
          loadedReceipts.map((receipt) => receiptKey(receipt)),
        );
        loadedReceipts = [
          ...loadedReceipts,
          ...loaded.filter((receipt) => !existing.has(receiptKey(receipt))),
        ];
        searchStart = previousLength;
      }
    } catch (err) {
      console.error("Failed to find issue receipt:", err);
      setError(err instanceof Error ? err.message : "Failed to find issue receipt");
    } finally {
      setFindingIssue(false);
    }
  }, [
    currentIndex,
    findingIssue,
    hasMore,
    loadReceipts,
    loadingMore,
    receipts,
    selectFirstIssueCheck,
    unavailableImageKeys,
  ]);

  if (loading) {
    return (
      <div ref={ref} className={styles.container} data-testid="receipt-health-explorer">
        <ReceiptFlowLoadingShell
          variant="within"
          layoutVars={{
            ...DEFAULT_LAYOUT_VARS,
            "--rf-center-height": "560px",
          } as React.CSSProperties}
        />
      </div>
    );
  }

  if (error || !currentReceipt || !activeCheck) {
    return (
      <div ref={ref} className={styles.container} data-testid="receipt-health-explorer">
        <ReceiptFlowLoadingShell
          variant="within"
          message={error ?? "No receipt health data available"}
          isError={Boolean(error)}
        />
      </div>
    );
  }

  const canGoPrevious = currentIndex > 0;
  const canGoNext = currentIndex < receipts.length - 1 || hasMore;
  const hasLoadedIssueAhead = receipts.some(
    (receipt, index) =>
      index > currentIndex &&
      receipt.summary.issue_count > 0 &&
      !unavailableImageKeys.has(receiptKey(receipt)),
  );
  const canGoNextIssue = hasLoadedIssueAhead || hasMore;

  return (
    <div ref={ref} className={styles.container} data-testid="receipt-health-explorer">
      <div className={styles.header}>
        <div className={styles.titleBlock}>
          <div className={styles.kicker}>Receipt Health</div>
          <h3 className={styles.title}>{receiptTitle(currentReceipt)}</h3>
          <div className={styles.subhead}>
            <span>Receipt {currentReceipt.receipt_id}</span>
            {currentReceipt.receipt_type ? <span>{currentReceipt.receipt_type}</span> : null}
            <span>{currentIndex + 1} of {totalCount || receipts.length}</span>
          </div>
        </div>
        <div className={styles.headerMetrics}>
          <span className={statusClass(currentReceipt.overall_status)}>
            {STATUS_LABELS[currentReceipt.overall_status]}
          </span>
          <span className={styles.issueMetric}>
            {issueLabel(currentReceipt.summary.issue_count)}
          </span>
          <button
            type="button"
            className={styles.issueButton}
            onClick={goNextIssue}
            disabled={!canGoNextIssue || findingIssue || loadingMore}
          >
            {findingIssue ? "Finding" : "Next issue"}
          </button>
        </div>
      </div>

      <div className={styles.body}>
        <div className={styles.visualColumn}>
          <ReceiptImage
            receipt={currentReceipt}
            activeCheck={activeCheck}
            onImageUnavailable={handleImageUnavailable}
          />
          <div className={styles.controls}>
            <button
              type="button"
              className={styles.navButton}
              onClick={goPrevious}
              disabled={!canGoPrevious}
            >
              Prev
            </button>
            <ReceiptRail
              receipts={receipts}
              currentIndex={currentIndex}
              onSelect={selectReceipt}
            />
            <button
              type="button"
              className={styles.navButton}
              onClick={goNext}
              disabled={!canGoNext || loadingMore}
            >
              {loadingMore ? "Loading" : "Next"}
            </button>
          </div>
        </div>

        <aside className={styles.inspector}>
          <HealthSummary receipt={currentReceipt} />

          <div className={styles.checkList}>
            {orderedChecks.map((check) => (
              <CheckButton
                key={check.id}
                check={check}
                active={check.id === activeCheck.id}
                onSelect={() => setActiveCheckId(check.id)}
              />
            ))}
          </div>

          <CheckDetails check={activeCheck} />

          <div className={styles.sectionHeader}>
            <span>{CHECK_LABELS[activeCheck.id]}</span>
            <span>{activeCheck.evidence_count} boxes</span>
          </div>
          <Issues receipt={currentReceipt} />
        </aside>
      </div>
    </div>
  );
}
