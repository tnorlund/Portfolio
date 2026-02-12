import { animated, to, useSpring } from "@react-spring/web";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  LabelEvaluatorReceipt,
  LabelEvaluatorWord,
  ReviewDecision,
  ReviewEvidence,
} from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl } from "../../../../utils/imageFormat";
import styles from "./BetweenReceiptVisualization.module.css";

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

// Layout constants
const QUEUE_WIDTH = 120;
const QUEUE_ITEM_WIDTH = 100;
const QUEUE_ITEM_LEFT_INSET = 10;
const CENTER_COLUMN_WIDTH = 350;
const CENTER_COLUMN_HEIGHT = 500;
const QUEUE_HEIGHT = 400;
const COLUMN_GAP = 24;

// Generate stable random positions for queue items
const getQueuePosition = (receiptId: string) => {
  const hash = receiptId.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;
  const rotation = (Math.abs(random1) * 24 - 12);
  const leftOffset = (Math.abs(random2) * 10 - 5);
  return { rotation, leftOffset };
};

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
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
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
    const result: LabelEvaluatorReceipt[] = [];
    const total = receipts.length;
    for (let i = 1; i <= maxVisible; i++) {
      result.push(receipts[(currentIndex + i) % total]);
    }
    return result;
  }, [receipts, currentIndex]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  const STACK_GAP = 20;

  return (
    <div className={styles.receiptQueue}>
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
              left: `${QUEUE_ITEM_LEFT_INSET + leftOffset}px`,
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
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

// ─── FlyingReceipt ───────────────────────────────────────────────────

interface FlyingReceiptProps {
  receipt: LabelEvaluatorReceipt | null;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isFlying: boolean;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  receipt,
  formatSupport,
  isFlying,
}) => {
  const width = Math.max(receipt?.width ?? 100, 1);
  const height = Math.max(receipt?.height ?? 150, 1);
  const receiptId = receipt ? `${receipt.image_id}_${receipt.receipt_id}` : "";
  const { rotation, leftOffset } = getQueuePosition(receiptId);

  const imageUrl = useMemo(() => {
    if (!formatSupport || !receipt) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  const aspectRatio = width / height;
  const maxHeight = 500;
  const maxWidth = 350;
  let displayHeight = Math.min(maxHeight, height);
  let displayWidth = displayHeight * aspectRatio;
  if (displayWidth > maxWidth) {
    displayWidth = maxWidth;
    displayHeight = displayWidth / aspectRatio;
  }

  const distanceToQueueItemCenter =
    CENTER_COLUMN_WIDTH / 2 +
    COLUMN_GAP +
    (QUEUE_WIDTH - (QUEUE_ITEM_LEFT_INSET + leftOffset + QUEUE_ITEM_WIDTH / 2));
  const startX = -distanceToQueueItemCenter;

  const queueItemHeight = (height / width) * QUEUE_ITEM_WIDTH;
  const queueItemCenterFromTop =
    (CENTER_COLUMN_HEIGHT - QUEUE_HEIGHT) / 2 + queueItemHeight / 2;
  const startY = queueItemCenterFromTop - CENTER_COLUMN_HEIGHT / 2;
  const startScale = QUEUE_ITEM_WIDTH / displayWidth;

  const { x, y, scale, rotate } = useSpring({
    from: { x: startX, y: startY, scale: startScale, rotate: rotation },
    to: { x: 0, y: 0, scale: 1, rotate: 0 },
    reset: true,
    config: { tension: 120, friction: 18 },
  });

  if (!receipt || !imageUrl || !isFlying) return null;

  const borderWidth = 1;
  const totalWidth = displayWidth + borderWidth * 2;
  const totalHeight = displayHeight + borderWidth * 2;

  return (
    <animated.div
      className={styles.flyingReceipt}
      style={{
        transform: to(
          [x, y, scale, rotate],
          (xVal, yVal, scaleVal, rotateVal) =>
            `translate(${xVal}px, ${yVal}px) scale(${scaleVal}) rotate(${rotateVal}deg)`
        ),
        marginLeft: -totalWidth / 2,
        marginTop: -totalHeight / 2,
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imageUrl}
        alt="Flying receipt"
        className={styles.flyingReceiptImage}
        style={{ width: displayWidth, height: displayHeight }}
      />
    </animated.div>
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
  const suggestedLabel = issue.suggested_label;
  const labelColor = LABEL_COLORS[suggestedLabel] || "var(--text-color)";
  const decisionColor = DECISION_COLORS[llm_review.decision] || "var(--text-color)";

  const showLabelChange =
    llm_review.decision === "INVALID" &&
    issue.current_label !== null &&
    issue.current_label !== llm_review.suggested_label &&
    llm_review.suggested_label !== null;

  return (
    <div className={styles.evidenceCard}>
      {/* Word + suggested label */}
      <div className={styles.cardHeader}>
        <span className={styles.cardWord}>&ldquo;{issue.word_text}&rdquo;</span>
        <span className={styles.cardArrow}>&rarr;</span>
        <span className={styles.cardLabel} style={{ color: labelColor }}>
          {suggestedLabel}
        </span>
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
          <span className={styles.labelBefore}>{issue.current_label}</span>
          <span className={styles.cardArrow}>&rarr;</span>
          <span
            className={styles.labelAfter}
            style={{ color: LABEL_COLORS[llm_review.suggested_label!] || "var(--text-color)" }}
          >
            {llm_review.suggested_label}
          </span>
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
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
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
              const suggestedLabel = card.decision.issue.suggested_label;
              const color = LABEL_COLORS[suggestedLabel] || "var(--text-color)";
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
}

const EvidencePanel: React.FC<EvidencePanelProps> = ({ revealedCards }) => {
  return (
    <div className={styles.evidencePanel}>
      {revealedCards.map((card) => (
        <EvidenceCard
          key={card.key}
          decision={card.decision}
          word={card.word}
        />
      ))}
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
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [showFlyingReceipt, setShowFlyingReceipt] = useState(false);
  const [flyingReceipt, setFlyingReceipt] = useState<LabelEvaluatorReceipt | null>(null);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Control flying receipt visibility
  useEffect(() => {
    if (isTransitioning) {
      const next = receipts.length > 0
        ? receipts[(currentIndex + 1) % receipts.length]
        : null;
      setFlyingReceipt(next);
      setShowFlyingReceipt(true);
      return;
    }
    const timeout = setTimeout(() => {
      setShowFlyingReceipt(false);
      setFlyingReceipt(null);
    }, 50);
    return () => clearTimeout(timeout);
  }, [isTransitioning, currentIndex, receipts]);

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

    // Cast to ReviewDecision[] to access evidence fields
    const reviewDecisions = currentReceipt.review.all_decisions as unknown as ReviewDecision[];

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
  }, [inView, receipts.length > 0, currentIndex]);

  if (loading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading between-receipt data...
      </div>
    );
  }

  if (error) {
    return (
      <div ref={ref} className={styles.error}>
        Error: {error}
      </div>
    );
  }

  if (receipts.length === 0) {
    return (
      <div ref={ref} className={styles.loading}>
        No between-receipt data available
      </div>
    );
  }

  const nextIndex = (currentIndex + 1) % receipts.length;
  const nextReceipt = receipts[nextIndex];

  return (
    <div ref={ref} className={styles.container}>
      <div className={styles.mainWrapper}>
        <ReceiptQueue
          receipts={receipts}
          currentIndex={currentIndex}
          formatSupport={formatSupport}
          isTransitioning={isTransitioning}
        />

        <div className={styles.centerColumn}>
          {/* Current receipt */}
          <div className={`${styles.receiptContainer} ${isTransitioning ? styles.fadeOut : ""}`}>
            <ReceiptViewer
              receipt={currentReceipt}
              scanProgress={scanProgress}
              revealedCards={revealedCards}
              formatSupport={formatSupport}
            />
          </div>

          {/* Flying receipt for desktop transition */}
          <div className={styles.flyingReceiptContainer}>
            {showFlyingReceipt && flyingReceipt && (
              <FlyingReceipt
                key={`flying-${flyingReceipt.image_id}_${flyingReceipt.receipt_id}`}
                receipt={flyingReceipt}
                formatSupport={formatSupport}
                isFlying={showFlyingReceipt}
              />
            )}
          </div>

          {/* Next receipt for mobile crossfade */}
          {isTransitioning && nextReceipt && (
            <div className={`${styles.receiptContainer} ${styles.nextReceipt} ${styles.fadeIn}`}>
              <ReceiptViewer
                receipt={nextReceipt}
                scanProgress={0}
                revealedCards={[]}
                formatSupport={formatSupport}
              />
            </div>
          )}
        </div>

        <EvidencePanel revealedCards={revealedCards} />
      </div>
    </div>
  );
};

export default BetweenReceiptVisualization;
