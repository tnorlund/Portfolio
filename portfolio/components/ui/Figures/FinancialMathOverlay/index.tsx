import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  FinancialMathReceipt,
  FinancialMathEquation,
} from "../../../../types/api";
import {
  getBestImageUrl,
  getJpegFallbackUrl,
  usePreloadReceiptImages,
} from "../../../../utils/imageFormat";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import {
  DEFAULT_LAYOUT_VARS,
  ReceiptFlowLoadingShell,
} from "../ReceiptFlow/ReceiptFlowLoadingShell";
import {
  getQueuePosition,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import { ImageFormatSupport } from "../ReceiptFlow/types";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
import styles from "./FinancialMathOverlay.module.css";

// Issue type colors
const ISSUE_COLORS: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

// Animation timing
const SCAN_DURATION = 3500;
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

const LAYOUT_VARS = DEFAULT_LAYOUT_VARS;

// Queue management
const QUEUE_REFETCH_THRESHOLD = 7;
const MAX_EMPTY_FETCHES = 3;

// Get equation-level color based on issue_type or word decisions
function getEquationColor(equation: FinancialMathEquation): string {
  const hasInvalid = equation.involved_words.some(
    (w) => w.decision === "INVALID"
  );
  const hasReview = equation.involved_words.some(
    (w) => w.decision === "NEEDS_REVIEW"
  );
  if (hasInvalid) return ISSUE_COLORS.INVALID;
  if (hasReview) return ISSUE_COLORS.NEEDS_REVIEW;
  return ISSUE_COLORS.VALID;
}

// ─── Receipt Queue (Left Column) ────────────────────────────────────────────

interface ReceiptQueueProps {
  receipts: FinancialMathReceipt[];
  currentIndex: number;
  formatSupport: ImageFormatSupport | null;
  isTransitioning: boolean;
  isPoolExhausted: boolean;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
  isPoolExhausted,
}) => {
  const maxVisible = 6;
  const STACK_GAP = 20;

  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    return getVisibleQueueIndices(receipts.length, currentIndex, maxVisible, isPoolExhausted).map(
      (idx) => receipts[idx]
    );
  }, [receipts, currentIndex, isPoolExhausted]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  return (
    <div className={styles.receiptQueue} data-rf-queue>
      {visibleReceipts.map((receipt, idx) => {
        const imageUrl = getBestImageUrl(receipt, formatSupport, 'thumbnail');
        const receiptKey = `${receipt.image_id}-${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptKey);

        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        const isFlying = isTransitioning && idx === 0;
        const queueKey = `${receiptKey}-queue-${idx}`;

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
                width={100}
                height={150}
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

// ─── Active Receipt Viewer (Center Column) ──────────────────────────────────

interface ActiveReceiptViewerProps {
  receipt: FinancialMathReceipt;
  scanProgress: number;
  revealedEquationIndices: Set<number>;
  formatSupport: ImageFormatSupport | null;
}

const ActiveReceiptViewer: React.FC<ActiveReceiptViewerProps> = ({
  receipt,
  scanProgress,
  revealedEquationIndices,
  formatSupport,
}) => {
  const [imgDim, setImgDim] = useState<{ w: number; h: number } | null>(null);

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport);
  }, [receipt, formatSupport]);

  const handleLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      setImgDim({ w: img.naturalWidth, h: img.naturalHeight });
    },
    []
  );

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  const w = imgDim?.w ?? 600;
  const h = imgDim?.h ?? 900;

  return (
    <div className={styles.activeReceipt}>
      <div className={styles.receiptImageWrapper}>
        <div className={styles.receiptImageInner}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="Receipt"
            className={styles.receiptImage}
            onLoad={handleLoad}
            onError={(e) => {
              const fallback = getJpegFallbackUrl(receipt);
              if (e.currentTarget.src !== fallback) {
                e.currentTarget.src = fallback;
              }
            }}
          />

          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${w} ${h}`}
            preserveAspectRatio="none"
          >
            {/* Scan line — subtle monochrome, matching other components */}
            {scanProgress > 0 && scanProgress < 100 && (
              <rect
                x="0"
                y={(scanProgress / 100) * h}
                width={w}
                height={1}
                fill="var(--text-color)"
                opacity={0.3}
              />
            )}

            {/* Equation word bounding boxes */}
            {receipt.equations.map((eq, eqIdx) => {
              if (!revealedEquationIndices.has(eqIdx)) return null;
              const color = getEquationColor(eq);
              return eq.involved_words.map((word) => {
                const bx = word.bbox.x * w;
                const by = (1 - word.bbox.y - word.bbox.height) * h;
                const bw = word.bbox.width * w;
                const bh = word.bbox.height * h;
                return (
                  <rect
                    key={`${eqIdx}-${word.line_id}-${word.word_id}`}
                    x={bx}
                    y={by}
                    width={bw}
                    height={bh}
                    fill={color}
                    fillOpacity={0.3}
                    stroke={color}
                    strokeWidth={2}
                  />
                );
              });
            })}
          </svg>
        </div>
      </div>
    </div>
  );
};

// ─── Equation Notation Builder ──────────────────────────────────────────────

function formatDollar(val: number | string): string {
  const n = typeof val === "number" ? val : parseFloat(String(val));
  if (isNaN(n)) return String(val);
  return `$${Math.abs(n).toFixed(2)}`;
}

interface EquationNotation {
  /** Values being summed (displayed vertically, last one gets "+" prefix) */
  addends: string[];
  /** Result value below the line */
  result: string;
}

function buildEquationNotation(eq: FinancialMathEquation): EquationNotation {
  const words = eq.involved_words;
  const issueType = eq.issue_type || "";

  // HAS_TOTAL: simple single-value display (no addends)
  if (issueType === "HAS_TOTAL") {
    return {
      addends: [],
      result: eq.actual_value != null ? formatDollar(eq.actual_value) : "N/A",
    };
  }

  // TOTAL_CHECK: SUBTOTAL + TAX = TOTAL (text-scanned)
  if (issueType === "TOTAL_CHECK") {
    // Parse values from description: "TOTAL ($X) = SUBTOTAL ($Y) + TAX ($Z) = $W"
    const desc = eq.description || "";
    const subtotalMatch = desc.match(/SUBTOTAL\s*\(\$?([\d.]+)\)/);
    const taxMatch = desc.match(/TAX\s*\(\$?([\d.]+)\)/);
    const totalMatch = desc.match(/TOTAL\s*\(\$?([\d.]+)\)/);
    const addends = [];
    if (subtotalMatch) addends.push(`$${subtotalMatch[1]}`);
    if (taxMatch) addends.push(`$${taxMatch[1]}`);
    return {
      addends: addends.length > 0 ? addends : [formatDollar(eq.expected_value)],
      result: totalMatch ? `$${totalMatch[1]}` : formatDollar(eq.actual_value),
    };
  }

  // TIP_CHECK: SUBTOTAL + TIP = TOTAL (text-scanned)
  if (issueType === "TIP_CHECK") {
    const desc = eq.description || "";
    const subtotalMatch = desc.match(/SUBTOTAL\s*\(\$?([\d.]+)\)/);
    const tipMatch = desc.match(/TIP\s*\(\$?([\d.]+)\)/);
    const totalMatch = desc.match(/TOTAL\s*\(\$?([\d.]+)\)/);
    const addends = [];
    if (subtotalMatch) addends.push(`$${subtotalMatch[1]}`);
    if (tipMatch) addends.push(`$${tipMatch[1]}`);
    return {
      addends: addends.length > 0 ? addends : [formatDollar(eq.expected_value)],
      result: totalMatch ? `$${totalMatch[1]}` : formatDollar(eq.actual_value),
    };
  }

  if (issueType.includes("GRAND_TOTAL")) {
    const subtotals = words.filter((w) => w.current_label === "SUBTOTAL");
    const taxes = words.filter((w) => w.current_label === "TAX");
    const grandTotals = words.filter((w) => w.current_label === "GRAND_TOTAL");
    const addends = [
      ...subtotals.map((w) => w.word_text),
      ...taxes.map((w) => w.word_text),
    ];
    const result =
      grandTotals.map((w) => w.word_text).join("") ||
      formatDollar(eq.actual_value);
    return {
      addends: addends.length > 0 ? addends : [formatDollar(eq.expected_value)],
      result,
    };
  }

  if (issueType.includes("SUBTOTAL")) {
    const lineItems = words.filter((w) => w.current_label === "LINE_TOTAL");
    const subtotals = words.filter((w) => w.current_label === "SUBTOTAL");
    const addends = lineItems.map((w) => w.word_text);
    const result =
      subtotals.map((w) => w.word_text).join("") ||
      formatDollar(eq.actual_value);
    return {
      addends: addends.length > 0 ? addends : [formatDollar(eq.expected_value)],
      result,
    };
  }

  return {
    addends: [formatDollar(eq.expected_value)],
    result: formatDollar(eq.actual_value),
  };
}

// ─── Equation Panel (Right Column) ──────────────────────────────────────────

interface EquationPanelProps {
  equations: FinancialMathEquation[];
  revealedEquationIndices: Set<number>;
  isTransitioning?: boolean;
  receiptType?: "itemized" | "service" | "terminal";
}

const EquationPanel: React.FC<EquationPanelProps> = ({
  equations,
  revealedEquationIndices,
  isTransitioning = false,
  receiptType,
}) => {
  return (
    <div
      className={styles.equationPanel}
      style={{ opacity: isTransitioning ? 0 : 1, transition: 'opacity 0.3s ease' }}
    >
      {receiptType && receiptType !== "itemized" && (
        <div className={styles.receiptTypeBadge}>
          {receiptType.toUpperCase()}
        </div>
      )}
      {equations.map((eq, idx) => {
        const color = getEquationColor(eq);
        const isRevealed = revealedEquationIndices.has(idx);
        const diff =
          typeof eq.difference === "number"
            ? eq.difference
            : parseFloat(String(eq.difference));
        const hasDiff = !isNaN(diff) && Math.abs(diff) > 0.001;
        const hasInvalid = eq.involved_words.some(
          (w) => w.decision === "INVALID"
        );
        const hasReview = eq.involved_words.some(
          (w) => w.decision === "NEEDS_REVIEW"
        );
        const isValid = !hasInvalid && !hasReview;
        const notation = buildEquationNotation(eq);
        const isHasTotal = eq.issue_type === "HAS_TOTAL";

        return (
          <div
            key={idx}
            className={`${styles.equationCard} ${isRevealed ? styles.revealed : ""}`}
            style={{ borderColor: isRevealed ? color : undefined }}
          >
            <div className={styles.summation}>
              {isHasTotal ? (
                /* HAS_TOTAL: simple single-value display */
                <div className={styles.resultRow}>
                  <span className={styles.resultVal}>{notation.result}</span>
                  <span
                    className={`${styles.resultIcon} ${isValid ? styles.resultValid : styles.resultInvalid}`}
                  >
                    {isValid ? "\u2713" : "\u2717"}
                  </span>
                </div>
              ) : (
                <>
                  {/* Addends stacked vertically */}
                  <div className={styles.addends}>
                    {notation.addends.map((val, i) => (
                      <div key={i} className={styles.addendRow}>
                        <span className={styles.addendOp}>
                          {i === notation.addends.length - 1 && notation.addends.length > 1
                            ? "+"
                            : ""}
                        </span>
                        <span className={styles.addendVal}>{val}</span>
                      </div>
                    ))}
                  </div>
                  {/* Horizontal rule = the "equals" line */}
                  <div className={styles.sumLine} />
                  {/* Result row with validity indicator */}
                  <div className={styles.resultRow}>
                    <span className={styles.resultVal}>{notation.result}</span>
                    <span
                      className={`${styles.resultIcon} ${isValid ? styles.resultValid : styles.resultInvalid}`}
                    >
                      {isValid ? "\u2713" : "\u2717"}
                    </span>
                  </div>
                  {hasDiff && (
                    <div
                      className={styles.equationDiff}
                      style={isValid ? { color: "rgba(var(--text-color-rgb), 0.4)" } : undefined}
                    >
                      {diff > 0 ? "+" : ""}
                      {diff.toFixed(2)}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

// ─── Main Component ─────────────────────────────────────────────────────────

export default function FinancialMathOverlay() {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [receipts, setReceipts] = useState<FinancialMathReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [currentReceiptIndex, setCurrentReceiptIndex] = useState(0);
  const [scanProgress, setScanProgress] = useState(0);
  const [revealedEquationIndices, setRevealedEquationIndices] = useState<
    Set<number>
  >(new Set());
  const [isTransitioning, setIsTransitioning] = useState(false);
  const formatSupport = useImageFormatSupport();
  const [isPoolExhausted, setIsPoolExhausted] = useState(false);

  usePreloadReceiptImages(receipts, formatSupport);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const isFetchingRef = useRef(false);
  const seenReceiptIds = useRef<Set<string>>(new Set());
  const emptyFetchCountRef = useRef(0);
  const isPoolExhaustedRef = useRef(isPoolExhausted);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  useEffect(() => {
    isPoolExhaustedRef.current = isPoolExhausted;
  }, [isPoolExhausted]);

  const getNextReceipt = useCallback(
    (items: FinancialMathReceipt[], idx: number) => {
      const next = idx + 1;
      return isPoolExhausted ? items[next % items.length] : items[next] ?? null;
    },
    [isPoolExhausted],
  );

  const { flyingItem, showFlying } = useFlyingReceipt(
    isTransitioning,
    receipts,
    currentReceiptIndex,
    getNextReceipt,
  );

  // Fetch more receipts
  const fetchMoreReceipts = useCallback(async () => {
    if (isFetchingRef.current || isPoolExhausted) return;
    isFetchingRef.current = true;
    try {
      const response = await api.fetchLabelEvaluatorFinancialMath(20);
      if (response?.receipts) {
        const newReceipts = response.receipts.filter(
          (r) =>
            !seenReceiptIds.current.has(`${r.image_id}-${r.receipt_id}`)
        );
        newReceipts.forEach((r) =>
          seenReceiptIds.current.add(`${r.image_id}-${r.receipt_id}`)
        );
        if (newReceipts.length > 0) {
          setReceipts((prev) => [...prev, ...newReceipts]);
          emptyFetchCountRef.current = 0;
        } else {
          emptyFetchCountRef.current += 1;
          if (emptyFetchCountRef.current >= MAX_EMPTY_FETCHES) {
            setIsPoolExhausted(true);
          }
        }
      }
    } catch (err) {
      console.error("Failed to fetch more receipts:", err);
    } finally {
      isFetchingRef.current = false;
    }
  }, [isPoolExhausted]);

  // Initial fetch
  useEffect(() => {
    const initialFetch = async () => {
      try {
        const [r1, r2] = await Promise.all([
          api.fetchLabelEvaluatorFinancialMath(20),
          api.fetchLabelEvaluatorFinancialMath(20),
        ]);
        const all: FinancialMathReceipt[] = [];
        for (const resp of [r1, r2]) {
          if (resp?.receipts) {
            for (const r of resp.receipts) {
              const key = `${r.image_id}-${r.receipt_id}`;
              if (!seenReceiptIds.current.has(key)) {
                seenReceiptIds.current.add(key);
                all.push(r);
              }
            }
          }
        }
        setReceipts(all);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setInitialLoading(false);
      }
    };
    initialFetch();
  }, []);

  // Refetch when queue is getting low
  const remainingReceipts = receipts.length - currentReceiptIndex;
  useEffect(() => {
    if (
      remainingReceipts < QUEUE_REFETCH_THRESHOLD &&
      !isFetchingRef.current &&
      !initialLoading &&
      !isPoolExhausted
    ) {
      fetchMoreReceipts();
    }
  }, [remainingReceipts, fetchMoreReceipts, initialLoading, isPoolExhausted]);

  // Compute which equations are revealed based on scan progress
  const computeRevealed = useCallback(
    (receipt: FinancialMathReceipt, progress: number): Set<number> => {
      const revealed = new Set<number>();
      const scanY = progress / 100;
      receipt.equations.forEach((eq, eqIdx) => {
        // Text-scanned equations have zero bboxes — reveal immediately
        const hasRealBbox = eq.involved_words.some(
          (w) => w.bbox.width > 0 || w.bbox.height > 0
        );
        if (!hasRealBbox) {
          if (progress > 0) revealed.add(eqIdx);
          return;
        }
        // An equation is revealed when at least one of its words' top edge is above the scan line
        const anyRevealed = eq.involved_words.some((word) => {
          const wordTopY = 1 - word.bbox.y - word.bbox.height;
          return wordTopY <= scanY;
        });
        if (anyRevealed) revealed.add(eqIdx);
      });
      return revealed;
    },
    []
  );


  // Animation loop
  const hasReceipts = receipts.length > 0;
  useEffect(() => {
    if (!inView || !hasReceipts) return;
    if (isAnimatingRef.current) return;
    isAnimatingRef.current = true;

    let receiptIndex = currentReceiptIndex;
    let startTime = performance.now();
    let isInTransition = false;

    const animate = (currentTime: number) => {
      const currentReceipts = receiptsRef.current;
      if (currentReceipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }

      const elapsed = currentTime - startTime;
      const currentReceipt = currentReceipts[receiptIndex];
      if (!currentReceipt) {
        animationRef.current = requestAnimationFrame(animate);
        return;
      }

      const totalDuration = SCAN_DURATION + HOLD_DURATION + TRANSITION_DURATION;

      if (elapsed < SCAN_DURATION) {
        // SCAN PHASE
        const progress = (elapsed / SCAN_DURATION) * 100;
        setScanProgress(Math.min(progress, 100));
        setRevealedEquationIndices(
          computeRevealed(currentReceipt, Math.min(progress, 100))
        );
        setIsTransitioning(false);
      } else if (elapsed < SCAN_DURATION + HOLD_DURATION) {
        // HOLD PHASE
        setScanProgress(100);
        setRevealedEquationIndices(computeRevealed(currentReceipt, 100));
        setIsTransitioning(false);
      } else if (elapsed < totalDuration) {
        // TRANSITION PHASE
        if (!isInTransition) {
          isInTransition = true;
          setIsTransitioning(true);
        }
      } else {
        // NEXT RECEIPT
        let nextIndex = receiptIndex + 1;
        if (nextIndex >= currentReceipts.length) {
          if (isPoolExhaustedRef.current) {
            nextIndex = 0;
          } else {
            animationRef.current = requestAnimationFrame(animate);
            return;
          }
        }
        receiptIndex = nextIndex;
        isInTransition = false;
        setCurrentReceiptIndex(receiptIndex);
        setScanProgress(0);
        setRevealedEquationIndices(new Set());
        setIsTransitioning(false);
        startTime = currentTime;
      }

      animationRef.current = requestAnimationFrame(animate);
    };

    animationRef.current = requestAnimationFrame(animate);
    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
      isAnimatingRef.current = false;
    };
  }, [inView, hasReceipts, computeRevealed]);

  const flyingElement = useMemo(() => {
    if (!showFlying || !flyingItem || !formatSupport) return null;
    const fUrl = getBestImageUrl(flyingItem, formatSupport);
    if (!fUrl) return null;
    const ar = flyingItem.width / flyingItem.height;
    let dh = Math.min(500, flyingItem.height);
    let dw = dh * ar;
    if (dw > 350) { dw = 350; dh = dw / ar; }
    return (
      <FlyingReceipt
        key={`flying-${flyingItem.image_id}-${flyingItem.receipt_id}`}
        imageUrl={fUrl}
        displayWidth={dw}
        displayHeight={dh}
        receiptId={`${flyingItem.image_id}-${flyingItem.receipt_id}`}
        onImageError={(e) => {
          const fallback = getJpegFallbackUrl(flyingItem);
          if ((e.target as HTMLImageElement).src !== fallback) {
            (e.target as HTMLImageElement).src = fallback;
          }
        }}
      />
    );
  }, [showFlying, flyingItem, formatSupport]);

  // ─── Render ─────────────────────────────────────────────────────────────

  if (initialLoading) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="financial"
        />
      </div>
    );
  }

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="financial"
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
          variant="financial"
          message="No financial math data available"
        />
      </div>
    );
  }

  const currentReceipt = receipts[currentReceiptIndex];
  const nextIndex = currentReceiptIndex + 1;
  const nextReceipt = isPoolExhausted
    ? receipts[nextIndex % receipts.length]
    : receipts[nextIndex];

  return (
    <div ref={ref} className={styles.container}>
      <ReceiptFlowShell
        layoutVars={LAYOUT_VARS}
        isTransitioning={isTransitioning}
        queue={
          <ReceiptQueue
            receipts={receipts}
            currentIndex={currentReceiptIndex}
            formatSupport={formatSupport}
            isTransitioning={isTransitioning}
            isPoolExhausted={isPoolExhausted}
          />
        }
        center={
          <ActiveReceiptViewer
            receipt={currentReceipt}
            scanProgress={scanProgress}
            revealedEquationIndices={revealedEquationIndices}
            formatSupport={formatSupport}
          />
        }
        flying={flyingElement}
        next={
          isTransitioning && nextReceipt ? (
            <ActiveReceiptViewer
              receipt={nextReceipt}
              scanProgress={0}
              revealedEquationIndices={new Set()}
              formatSupport={formatSupport}
            />
          ) : null
        }
        legend={
          <EquationPanel
          equations={currentReceipt.equations}
          revealedEquationIndices={revealedEquationIndices}
          isTransitioning={isTransitioning}
          receiptType={currentReceipt.receipt_type}
          />
        }
      />
    </div>
  );
}
