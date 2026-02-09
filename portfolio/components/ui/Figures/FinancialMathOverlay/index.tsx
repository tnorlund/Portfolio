import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { animated, useSpring, to } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import Image from "next/image";
import { api } from "../../../../services/api";
import {
  FinancialMathReceipt,
  FinancialMathEquation,
} from "../../../../types/api";
import {
  detectImageFormatSupport,
  getBestImageUrl,
  FormatSupport,
  ImageFormats,
} from "../../../../utils/imageFormat";
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

// Queue management
const QUEUE_REFETCH_THRESHOLD = 7;
const MAX_EMPTY_FETCHES = 3;

// Build CDN keys from image_id and receipt_id
function buildCdnKeys(imageId: string, receiptId: number): ImageFormats {
  const paddedId = String(receiptId).padStart(5, "0");
  const base = `assets/${imageId}_RECEIPT_${paddedId}`;
  return {
    cdn_s3_key: `${base}.jpg`,
    cdn_webp_s3_key: `${base}.webp`,
    cdn_avif_s3_key: `${base}.avif`,
  };
}

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

// Stable random positions for queue items
const getQueuePosition = (key: string) => {
  const hash = key.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;
  const rotation = Math.abs(random1) * 24 - 12;
  const leftOffset = Math.abs(random2) * 10 - 5;
  return { rotation, leftOffset };
};

// ─── Receipt Queue (Left Column) ────────────────────────────────────────────

interface ReceiptQueueProps {
  receipts: FinancialMathReceipt[];
  currentIndex: number;
  formatSupport: FormatSupport | null;
  isTransitioning: boolean;
  isPoolExhausted: boolean;
  shouldAnimate: boolean;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
  isPoolExhausted,
  shouldAnimate,
}) => {
  const maxVisible = 6;
  const [imagesLoaded, setImagesLoaded] = useState<Set<number>>(new Set());
  const STACK_GAP = 20;
  const fadeDelay = 50;

  const handleImageLoad = useCallback((idx: number) => {
    setImagesLoaded((prev) => new Set(prev).add(idx));
  }, []);

  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    const result: FinancialMathReceipt[] = [];
    for (let i = 1; i <= maxVisible; i++) {
      const idx = currentIndex + i;
      if (isPoolExhausted) {
        result.push(receipts[idx % receipts.length]);
      } else if (idx < receipts.length) {
        result.push(receipts[idx]);
      }
    }
    return result;
  }, [receipts, currentIndex, isPoolExhausted]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  return (
    <div className={styles.receiptQueue}>
      {visibleReceipts.map((receipt, idx) => {
        const cdnKeys = buildCdnKeys(receipt.image_id, receipt.receipt_id);
        const imageUrl = getBestImageUrl(cdnKeys, formatSupport);
        const receiptKey = `${receipt.image_id}-${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptKey);

        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        const isFlying = isTransitioning && idx === 0;

        const queueKey = `${receiptKey}-queue-${idx}`;
        const isImageLoaded = imagesLoaded.has(idx);
        const showItem = shouldAnimate && isImageLoaded;

        return (
          <div
            key={queueKey}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            style={{
              top: `${stackOffset}px`,
              left: `${10 + leftOffset}px`,
              transform: `rotate(${rotation}deg) translateY(${showItem ? 0 : -50}px)`,
              opacity: showItem ? 1 : 0,
              zIndex,
              transition: `transform 0.6s ease-out ${shouldAnimate ? idx * fadeDelay : 0}ms, opacity 0.6s ease-out ${shouldAnimate ? idx * fadeDelay : 0}ms, top 0.4s ease, left 0.4s ease`,
            }}
          >
            {imageUrl && (
              <Image
                src={imageUrl}
                alt={`Queued receipt ${idx + 1}`}
                width={100}
                height={150}
                style={{ width: "100%", height: "auto", display: "block" }}
                sizes="100px"
                onLoad={() => handleImageLoad(idx)}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

// ─── Flying Receipt ─────────────────────────────────────────────────────────

interface FlyingReceiptProps {
  receipt: FinancialMathReceipt | null;
  formatSupport: FormatSupport | null;
  isFlying: boolean;
  imageDimensions: { width: number; height: number } | null;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  receipt,
  formatSupport,
  isFlying,
  imageDimensions,
}) => {
  const width = imageDimensions?.width ?? 100;
  const height = imageDimensions?.height ?? 150;
  const receiptKey = receipt
    ? `${receipt.image_id}-${receipt.receipt_id}`
    : "";
  const { rotation, leftOffset } = getQueuePosition(receiptKey);

  const imageUrl = useMemo(() => {
    if (!formatSupport || !receipt) return null;
    return getBestImageUrl(
      buildCdnKeys(receipt.image_id, receipt.receipt_id),
      formatSupport
    );
  }, [receipt, formatSupport]);

  const aspectRatio = width / height;
  const displayHeight = Math.min(500, height);
  const displayWidth = displayHeight * aspectRatio;

  const queueItemWidth = 100;
  const queueWidth = 120;
  const gap = 24;
  const centerColumnWidth = 350;
  const centerColumnHeight = 500;
  const queueHeight = 400;

  const distanceToQueueItemCenter =
    centerColumnWidth / 2 +
    gap +
    (queueWidth - (10 + leftOffset + queueItemWidth / 2));
  const startX = -distanceToQueueItemCenter;

  const queueItemHeight = (height / width) * queueItemWidth;
  const queueItemCenterFromTop =
    (centerColumnHeight - queueHeight) / 2 + queueItemHeight / 2;
  const startY = queueItemCenterFromTop - centerColumnHeight / 2;

  const startScale = queueItemWidth / displayWidth;

  const { x, y, scale, rotate } = useSpring({
    from: { x: startX, y: startY, scale: startScale, rotate: rotation },
    to: { x: 0, y: 0, scale: 1, rotate: 0 },
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

// ─── Active Receipt Viewer (Center Column) ──────────────────────────────────

interface ActiveReceiptViewerProps {
  receipt: FinancialMathReceipt;
  scanProgress: number;
  revealedEquationIndices: Set<number>;
  formatSupport: FormatSupport | null;
  onImageLoad?: (w: number, h: number) => void;
}

const ActiveReceiptViewer: React.FC<ActiveReceiptViewerProps> = ({
  receipt,
  scanProgress,
  revealedEquationIndices,
  formatSupport,
  onImageLoad,
}) => {
  const [imgDim, setImgDim] = useState<{ w: number; h: number } | null>(null);

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(
      buildCdnKeys(receipt.image_id, receipt.receipt_id),
      formatSupport
    );
  }, [receipt, formatSupport]);

  const handleLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      setImgDim({ w: img.naturalWidth, h: img.naturalHeight });
      onImageLoad?.(img.naturalWidth, img.naturalHeight);
    },
    [onImageLoad]
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
          />

          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${w} ${h}`}
            preserveAspectRatio="none"
          >
            {/* Scan line */}
            <defs>
              <linearGradient
                id={`scanGrad-${receipt.image_id}-${receipt.receipt_id}`}
                x1="0%"
                y1="0%"
                x2="100%"
                y2="0%"
              >
                <stop offset="0%" stopColor="transparent" />
                <stop offset="20%" stopColor="var(--color-red)" />
                <stop offset="80%" stopColor="var(--color-red)" />
                <stop offset="100%" stopColor="transparent" />
              </linearGradient>
              <filter
                id={`scanGlow-${receipt.image_id}-${receipt.receipt_id}`}
                x="-50%"
                y="-50%"
                width="200%"
                height="200%"
              >
                <feGaussianBlur stdDeviation="4" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            {scanProgress > 0 && (
              <rect
                x="0"
                y={(scanProgress / 100) * h}
                width={w}
                height={Math.max(h * 0.005, 3)}
                fill={`url(#scanGrad-${receipt.image_id}-${receipt.receipt_id})`}
                filter={`url(#scanGlow-${receipt.image_id}-${receipt.receipt_id})`}
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
}

const EquationPanel: React.FC<EquationPanelProps> = ({
  equations,
  revealedEquationIndices,
  isTransitioning = false,
}) => {
  return (
    <div
      className={`${styles.equationPanel} ${isTransitioning ? styles.equationPanelHidden : ""}`}
    >
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

        return (
          <div
            key={idx}
            className={`${styles.equationCard} ${isRevealed ? styles.revealed : ""}`}
            style={{ borderColor: isRevealed ? color : undefined }}
          >
            <div className={styles.summation}>
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
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(
    null
  );
  const [isPoolExhausted, setIsPoolExhausted] = useState(false);
  const [startQueueAnimation, setStartQueueAnimation] = useState(false);

  // Track natural image dimensions per receipt for flying receipt
  const imageDimsRef = useRef<Map<string, { width: number; height: number }>>(
    new Map()
  );

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

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Start queue animation when in view and receipts loaded
  useEffect(() => {
    if (inView && receipts.length > 0 && !startQueueAnimation) {
      setStartQueueAnimation(true);
    }
  }, [inView, receipts.length, startQueueAnimation]);

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

  // Store image dimensions when active receipt loads
  const handleActiveImageLoad = useCallback(
    (w: number, h: number) => {
      const receipt = receipts[currentReceiptIndex];
      if (receipt) {
        imageDimsRef.current.set(
          `${receipt.image_id}-${receipt.receipt_id}`,
          { width: w, height: h }
        );
      }
    },
    [receipts, currentReceiptIndex]
  );

  // Animation loop
  useEffect(() => {
    if (!inView || receipts.length === 0) return;
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
  }, [inView, receipts.length > 0, computeRevealed]);

  // ─── Render ─────────────────────────────────────────────────────────────

  if (initialLoading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading financial math data...
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
        No financial math data available
      </div>
    );
  }

  const currentReceipt = receipts[currentReceiptIndex];
  const nextIndex = currentReceiptIndex + 1;
  const nextReceipt = isPoolExhausted
    ? receipts[nextIndex % receipts.length]
    : receipts[nextIndex];

  const nextReceiptKey = nextReceipt
    ? `${nextReceipt.image_id}-${nextReceipt.receipt_id}`
    : "";
  const nextDims = imageDimsRef.current.get(nextReceiptKey) ?? null;

  return (
    <div ref={ref} className={styles.container}>
      <div className={styles.mainWrapper}>
        <ReceiptQueue
          receipts={receipts}
          currentIndex={currentReceiptIndex}
          formatSupport={formatSupport}
          isTransitioning={isTransitioning}
          isPoolExhausted={isPoolExhausted}
          shouldAnimate={startQueueAnimation}
        />

        <div className={styles.centerColumn}>
          <div
            className={`${styles.receiptContainer} ${isTransitioning ? styles.fadeOut : ""}`}
          >
            <ActiveReceiptViewer
              receipt={currentReceipt}
              scanProgress={scanProgress}
              revealedEquationIndices={revealedEquationIndices}
              formatSupport={formatSupport}
              onImageLoad={handleActiveImageLoad}
            />
          </div>

          <div className={styles.flyingReceiptContainer}>
            {isTransitioning && nextReceipt && (
              <FlyingReceipt
                key={`flying-${nextReceipt.image_id}-${nextReceipt.receipt_id}`}
                receipt={nextReceipt}
                formatSupport={formatSupport}
                isFlying={isTransitioning}
                imageDimensions={nextDims}
              />
            )}
          </div>

          {isTransitioning && nextReceipt && (
            <div
              className={`${styles.receiptContainer} ${styles.nextReceipt} ${styles.fadeIn}`}
            >
              <ActiveReceiptViewer
                receipt={nextReceipt}
                scanProgress={0}
                revealedEquationIndices={new Set()}
                formatSupport={formatSupport}
              />
            </div>
          )}
        </div>

        <EquationPanel
          equations={currentReceipt.equations}
          revealedEquationIndices={revealedEquationIndices}
          isTransitioning={isTransitioning}
        />
      </div>
    </div>
  );
}
