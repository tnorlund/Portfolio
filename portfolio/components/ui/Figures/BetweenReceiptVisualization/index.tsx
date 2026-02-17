import React, { useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  LabelEvaluatorDecision,
  LabelEvaluatorReceipt,
  LabelEvaluatorWord,
  ReviewDecision,
  ReviewEvidence,
} from "../../../../types/api";
import { getBestImageUrl, getJpegFallbackUrl, usePreloadReceiptImages } from "../../../../utils/imageFormat";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import { ReceiptFlowLoadingShell } from "../ReceiptFlow/ReceiptFlowLoadingShell";
import {
  getQueuePosition,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import { ImageFormatSupport } from "../ReceiptFlow/types";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
import styles from "./BetweenReceiptVisualization.module.css";

// Type guard to narrow union to ReviewDecision
function isReviewDecision(
  d: LabelEvaluatorDecision | ReviewDecision
): d is ReviewDecision {
  return "evidence" in d && "consensus_score" in d;
}

// Label colors for bounding boxes (same as LayoutLMBatchVisualization)
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  AMOUNT: "var(--color-green)",
  ADDRESS: "var(--color-red)",
  WEBSITE: "var(--color-purple)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-orange)",
  LINE_TOTAL: "var(--color-green)",
  SUBTOTAL: "var(--color-green)",
  TAX: "var(--color-green)",
  GRAND_TOTAL: "var(--color-green)",
  PRODUCT_NAME: "var(--color-yellow)",
  O: "var(--text-color)",
};

// Decision colors
const DECISION_COLORS: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

// Animation timing (ms)
const SCAN_DURATION = 3000;
const HOLD_DURATION = 1500;
const TRANSITION_DURATION = 600;

const LAYOUT_VARS = {
  "--rf-queue-width": "120px",
  "--rf-queue-height": "400px",
  "--rf-center-max-width": "350px",
  "--rf-center-height": "500px",
  "--rf-mobile-center-height": "400px",
  "--rf-mobile-center-height-sm": "320px",
  "--rf-gap": "1.5rem",
  "--rf-align-items": "flex-start",
} as React.CSSProperties;

// Revealed card for tracking which review decisions are visible
interface RevealedCard {
  key: string;
  decision: ReviewDecision;
  word: LabelEvaluatorWord;
}

// ─── ReceiptQueue ────────────────────────────────────────────────────

interface ReceiptQueueProps {
  receipts: LabelEvaluatorReceipt[];
  currentIndex: number;
  formatSupport: ImageFormatSupport | null;
  isTransitioning: boolean;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
}) => {
  const maxVisible = 6;

  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    return getVisibleQueueIndices(receipts.length, currentIndex, maxVisible, true).map(
      (idx) => receipts[idx]
    );
  }, [receipts, currentIndex, maxVisible]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  const STACK_GAP = 20;

  return (
    <div className={styles.receiptQueue} data-rf-queue>
      {visibleReceipts.map((receipt, idx) => {
        const imageUrl = getBestImageUrl(receipt, formatSupport, "thumbnail");
        const { width, height } = receipt;
        const receiptId = `${receipt.image_id}_${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptId);
        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        const isFlying = isTransitioning && idx === 0;
        const queueKey = `${receiptId}-queue-${idx}`;

        return (
          <div
            key={queueKey}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            style={{
              top: `${stackOffset}px`,
              left: `${10 + leftOffset}px`,
              transform: `rotate(${rotation}deg)`,
              zIndex,
            }}
          >
            {imageUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={imageUrl}
                alt={`Queued receipt ${idx + 1}`}
                width={width}
                height={height}
                style={{ width: "100%", height: "auto", display: "block" }}
                onError={(e) => {
                  const fallback = getJpegFallbackUrl(receipt);
                  if (e.currentTarget.src !== fallback) {
                    e.currentTarget.src = fallback;
                  }
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

// ─── EvidenceDots ────────────────────────────────────────────────────

interface EvidenceDotsProps {
  evidence: ReviewEvidence[];
  maxWidth: number;
}

const EvidenceDots: React.FC<EvidenceDotsProps> = ({ evidence, maxWidth }) => {
  // Sort by descending similarity score
  const sorted = useMemo(
    () => [...evidence].sort((a, b) => b.similarity_score - a.similarity_score),
    [evidence]
  );

  // Calculate how many dots fit
  const dotGap = 3;
  const maxDots = Math.floor((maxWidth + dotGap) / (10 + dotGap));
  const visible = sorted.slice(0, maxDots);

  return (
    <svg className={styles.dotRow} width={maxWidth} height={12}>
      {visible.map((ev, i) => {
        const r = ev.is_same_merchant ? 5 : 3.5;
        const cx = i * (10 + dotGap) + 5;
        const cy = 6;

        return ev.label_valid ? (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={r}
            fill="var(--text-color)"
            opacity={0.8}
          />
        ) : (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke="var(--text-color)"
            strokeWidth={1}
            opacity={0.6}
          />
        );
      })}
    </svg>
  );
};

// ─── EvidenceCard ────────────────────────────────────────────────────

interface EvidenceCardProps {
  decision: ReviewDecision;
  word: LabelEvaluatorWord;
}

const EvidenceCard: React.FC<EvidenceCardProps> = ({ decision, word }) => {
  const { issue, evidence, llm_review } = decision;
  const displayLabel = issue.current_label ?? issue.suggested_label;
  const decisionColor = DECISION_COLORS[llm_review.decision] || "var(--text-color)";

  // Show label change when the LLM proposes a different label
  const proposedLabel = llm_review.suggested_label ?? issue.suggested_label;
  const showLabelChange =
    proposedLabel !== null &&
    proposedLabel !== issue.current_label;

  return (
    <div className={styles.evidenceCard}>
      {/* Word + current/proposed label */}
      <div className={styles.cardHeader}>
        <span className={styles.cardWord}>&ldquo;{issue.word_text}&rdquo;</span>
        {displayLabel && (
          <>
            <span className={styles.cardArrow}>&rarr;</span>
            <span className={styles.cardLabel}>{displayLabel}</span>
          </>
        )}
      </div>

      {/* Evidence dots */}
      {evidence.length > 0 && (
        <EvidenceDots evidence={evidence} maxWidth={220} />
      )}

      {/* Decision + confidence */}
      <div className={styles.decisionLine}>
        <span className={styles.decisionText} style={{ color: decisionColor }}>
          {llm_review.decision}
        </span>
        <span className={styles.confidenceText}>{llm_review.confidence}</span>
      </div>

      {/* Label change (before → after) */}
      {showLabelChange && (
        <div className={styles.labelChange}>
          <span className={styles.labelBefore}>{issue.current_label ?? 'O'}</span>
          <span className={styles.cardArrow}>&rarr;</span>
          <span className={styles.labelAfter}>{proposedLabel}</span>
        </div>
      )}

      {/* Reasoning */}
      <div className={styles.reasoningText}>{llm_review.reasoning}</div>
    </div>
  );
};

// ─── ReceiptViewer ───────────────────────────────────────────────────

interface ReceiptViewerProps {
  receipt: LabelEvaluatorReceipt;
  scanProgress: number;
  revealedCards: RevealedCard[];
  formatSupport: ImageFormatSupport | null;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
  receipt,
  scanProgress,
  revealedCards,
  formatSupport,
}) => {
  const { width, height } = receipt;

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  const scanY = (scanProgress / 100) * height;

  return (
    <div className={styles.receiptViewer}>
      <div className={styles.receiptImageWrapper}>
        <div className={styles.receiptImageInner}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="Receipt"
            className={styles.receiptImage}
            width={width}
            height={height}
            onError={(e) => {
              const fallback = getJpegFallbackUrl(receipt);
              if (e.currentTarget.src !== fallback) {
                e.currentTarget.src = fallback;
              }
            }}
          />
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
          >
            {/* Scan line - thin, subtle */}
            {scanProgress > 0 && scanProgress < 100 && (
              <rect
                x="0"
                y={scanY}
                width={width}
                height={1}
                fill="var(--text-color)"
                opacity={0.3}
              />
            )}

            {/* Bounding boxes for flagged words */}
            {revealedCards.map((card) => {
              const { word } = card;
              const effectiveLabel = card.decision.issue.current_label ?? card.decision.issue.suggested_label;
              const color = effectiveLabel ? (LABEL_COLORS[effectiveLabel] || "var(--text-color)") : "var(--text-color)";
              const x = word.bbox.x * width;
              const y = (1 - word.bbox.y - word.bbox.height) * height;
              const w = word.bbox.width * width;
              const h = word.bbox.height * height;

              return (
                <rect
                  key={card.key}
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  fill={color}
                  fillOpacity={0.15}
                  stroke={color}
                  strokeWidth={1.5}
                  strokeOpacity={0.5}
                />
              );
            })}
          </svg>
        </div>
      </div>
    </div>
  );
};

// ─── EvidencePanel ───────────────────────────────────────────────────

interface EvidencePanelProps {
  revealedCards: RevealedCard[];
  isTransitioning?: boolean;
}

const EvidencePanel: React.FC<EvidencePanelProps> = ({ revealedCards, isTransitioning = false }) => {
  return (
    <div
      className={`${styles.evidencePanel}${isTransitioning ? ` ${styles.evidencePanelHidden}` : ""}`}
    >
      {revealedCards.map((card, idx) => (
        <div
          key={card.key}
          className={idx >= 3 ? styles.mobileHidden : undefined}
        >
          <EvidenceCard
            decision={card.decision}
            word={card.word}
          />
        </div>
      ))}
      {revealedCards.length > 3 && (
        <div className={styles.overflowIndicator}>
          +{revealedCards.length - 3} more
        </div>
      )}
    </div>
  );
};

// ─── Main Component ──────────────────────────────────────────────────

const BetweenReceiptVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [receipts, setReceipts] = useState<LabelEvaluatorReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [scanProgress, setScanProgress] = useState(0);
  const [revealedCards, setRevealedCards] = useState<RevealedCard[]>([]);
  const formatSupport = useImageFormatSupport();
  const [isTransitioning, setIsTransitioning] = useState(false);

  usePreloadReceiptImages(receipts, formatSupport);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  const { flyingItem, showFlying } = useFlyingReceipt(
    isTransitioning,
    receipts,
    currentIndex,
  );

  // Fetch data — filter to receipts with review decisions
  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await api.fetchLabelEvaluatorVisualization();
        if (response && response.receipts) {
          const filtered = response.receipts.filter(
            (r) => r.review && r.review.all_decisions.length > 0
          );
          setReceipts(filtered);
        }
      } catch (err) {
        console.error("Failed to fetch label evaluator data:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const currentReceipt = receipts[currentIndex];

  const flyingElement = useMemo(() => {
    if (!showFlying || !flyingItem || !formatSupport) return null;
    const fUrl = getBestImageUrl(flyingItem, formatSupport);
    if (!fUrl) return null;
    const fw = Math.max(flyingItem.width, 1);
    const fh = Math.max(flyingItem.height, 1);
    const ar = fw / fh;
    let dh = Math.min(500, fh);
    let dw = dh * ar;
    if (dw > 350) { dw = 350; dh = dw / ar; }
    return (
      <FlyingReceipt
        key={`flying-${flyingItem.image_id}_${flyingItem.receipt_id}`}
        imageUrl={fUrl}
        displayWidth={dw}
        displayHeight={dh}
        receiptId={`${flyingItem.image_id}_${flyingItem.receipt_id}`}
        onImageError={(e) => {
          const fallback = getJpegFallbackUrl(flyingItem);
          if ((e.target as HTMLImageElement).src !== fallback) {
            (e.target as HTMLImageElement).src = fallback;
          }
        }}
      />
    );
  }, [showFlying, flyingItem, formatSupport]);

  // Build word lookup for current receipt
  const wordLookup = useMemo(() => {
    if (!currentReceipt) return new Map<string, LabelEvaluatorWord>();
    const map = new Map<string, LabelEvaluatorWord>();
    for (const word of currentReceipt.words) {
      map.set(`${word.line_id}_${word.word_id}`, word);
    }
    return map;
  }, [currentReceipt]);

  // Calculate revealed cards based on scan progress
  useEffect(() => {
    if (!currentReceipt || !currentReceipt.review) return;

    const scanY = scanProgress / 100;
    const cards: RevealedCard[] = [];

    const reviewDecisions = currentReceipt.review.all_decisions.filter(isReviewDecision);

    for (const decision of reviewDecisions) {
      const word = wordLookup.get(
        `${decision.issue.line_id}_${decision.issue.word_id}`
      );
      if (!word) continue;

      // Word is revealed when scan passes its top edge
      const wordTopY = 1 - word.bbox.y - word.bbox.height;
      if (wordTopY <= scanY) {
        cards.push({
          key: `${decision.issue.line_id}_${decision.issue.word_id}`,
          decision,
          word,
        });
      }
    }

    setRevealedCards(cards);
  }, [currentReceipt, scanProgress, wordLookup]);

  // Animation loop
  useEffect(() => {
    if (!inView || receipts.length === 0) return;
    if (isAnimatingRef.current) return;
    isAnimatingRef.current = true;

    const totalCycle = SCAN_DURATION + HOLD_DURATION + TRANSITION_DURATION;
    let receiptIdx = currentIndex;
    let startTime = performance.now();
    let isInTransition = false;

    setScanProgress(0);
    setIsTransitioning(false);
    setRevealedCards([]);

    const animate = (time: number) => {
      const currentReceipts = receiptsRef.current;
      if (currentReceipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }

      const elapsed = time - startTime;

      if (elapsed < SCAN_DURATION) {
        // SCAN phase
        const progress = (elapsed / SCAN_DURATION) * 100;
        setScanProgress(Math.min(progress, 100));
        setIsTransitioning(false);
      } else if (elapsed < SCAN_DURATION + HOLD_DURATION) {
        // HOLD phase
        setScanProgress(100);
        setIsTransitioning(false);
      } else if (elapsed < totalCycle) {
        // TRANSITION phase
        if (!isInTransition) {
          isInTransition = true;
          setIsTransitioning(true);
        }
      } else {
        // Move to next receipt
        receiptIdx = (receiptIdx + 1) % currentReceipts.length;
        isInTransition = false;
        setCurrentIndex(receiptIdx);
        setScanProgress(0);
        setIsTransitioning(false);
        setRevealedCards([]);
        startTime = time;
      }

      animationRef.current = requestAnimationFrame(animate);
    };

    animationRef.current = requestAnimationFrame(animate);

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
      isAnimatingRef.current = false;
    };
  }, [inView, receipts.length, currentIndex]);

  if (loading) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="between"
        />
      </div>
    );
  }

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="between"
          message={`Error: ${error}`}
          isError
        />
      </div>
    );
  }

  if (receipts.length === 0) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="between"
          message="No between-receipt data available"
        />
      </div>
    );
  }

  const nextIndex = (currentIndex + 1) % receipts.length;
  const nextReceipt = receipts[nextIndex];

  return (
    <div ref={ref} className={styles.container}>
      <ReceiptFlowShell
        layoutVars={LAYOUT_VARS}
        isTransitioning={isTransitioning}
        queue={
          <ReceiptQueue
            receipts={receipts}
            currentIndex={currentIndex}
            formatSupport={formatSupport}
            isTransitioning={isTransitioning}
          />
        }
        center={
          <ReceiptViewer
            receipt={currentReceipt}
            scanProgress={scanProgress}
            revealedCards={revealedCards}
            formatSupport={formatSupport}
          />
        }
        flying={flyingElement}
        next={
          isTransitioning && nextReceipt ? (
            <ReceiptViewer
              receipt={nextReceipt}
              scanProgress={0}
              revealedCards={[]}
              formatSupport={formatSupport}
            />
          ) : null
        }
        legend={<EvidencePanel revealedCards={revealedCards} isTransitioning={isTransitioning} />}
      />
    </div>
  );
};

export default BetweenReceiptVisualization;
