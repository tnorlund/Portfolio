import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { animated, useSpring, to } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import Image from "next/image";
import { api } from "../../../../services/api";
import { EvidenceReceipt } from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl, FormatSupport, ImageFormats } from "../../../../utils/imageFormat";
import styles from "./EvidenceVisualization.module.css";

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

// Animation timing
const SCAN_DURATION = 3000;
const HOLD_DURATION = 1500;
const TRANSITION_DURATION = 600;

// Queue management constants
const QUEUE_REFETCH_THRESHOLD = 7;
const MAX_EMPTY_FETCHES = 3;

// Stable random positions for queue items based on receipt key
const getQueuePosition = (key: string) => {
  const hash = key.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;
  const rotation = Math.abs(random1) * 24 - 12;
  const leftOffset = Math.abs(random2) * 10 - 5;
  return { rotation, leftOffset };
};

// Unique key for an evidence receipt
const receiptKey = (r: EvidenceReceipt) => `${r.image_id}_${r.receipt_id}`;

// ---------- Queue Component ----------

interface ReceiptQueueProps {
  receipts: EvidenceReceipt[];
  currentIndex: number;
  formatSupport: FormatSupport | null;
  isTransitioning: boolean;
  isPoolExhausted: boolean;
  shouldAnimate: boolean;
  imageDims: React.MutableRefObject<Map<string, { width: number; height: number }>>;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
  isPoolExhausted,
  shouldAnimate,
  imageDims,
}) => {
  const maxVisible = 6;
  const [imagesLoaded, setImagesLoaded] = useState<Set<number>>(new Set());
  const handleImageLoad = useCallback((idx: number) => {
    setImagesLoaded((prev) => new Set(prev).add(idx));
  }, []);

  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    const result: EvidenceReceipt[] = [];
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

  const STACK_GAP = 20;

  return (
    <div className={styles.receiptQueue}>
      {visibleReceipts.map((receipt, idx) => {
        const key = receiptKey(receipt);
        const cdnKeys = buildCdnKeys(receipt.image_id, receipt.receipt_id);
        const imageUrl = getBestImageUrl(cdnKeys, formatSupport);
        const dims = imageDims.current.get(key);
        const { rotation, leftOffset } = getQueuePosition(key);
        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        const isFlying = isTransitioning && idx === 0;
        const isImageLoaded = imagesLoaded.has(idx);
        const showItem = shouldAnimate && isImageLoaded;

        return (
          <div
            key={`${key}-queue-${idx}`}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            style={{
              top: `${stackOffset}px`,
              left: `${10 + leftOffset}px`,
              transform: `rotate(${rotation}deg) translateY(${showItem ? 0 : -50}px)`,
              opacity: showItem ? 1 : 0,
              zIndex,
              transition: `transform 0.6s ease-out ${shouldAnimate ? idx * 50 : 0}ms, opacity 0.6s ease-out ${shouldAnimate ? idx * 50 : 0}ms, top 0.4s ease, left 0.4s ease`,
            }}
          >
            {imageUrl && (
              <Image
                src={imageUrl}
                alt={`Queued receipt ${idx + 1}`}
                width={dims?.width ?? 200}
                height={dims?.height ?? 300}
                style={{ width: "100%", height: "auto", display: "block" }}
                sizes="100px"
                onLoad={(e) => {
                  handleImageLoad(idx);
                  const img = e.currentTarget as HTMLImageElement;
                  if (!imageDims.current.has(key)) {
                    imageDims.current.set(key, { width: img.naturalWidth, height: img.naturalHeight });
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

// ---------- Flying Receipt ----------

interface FlyingReceiptProps {
  receipt: EvidenceReceipt | null;
  formatSupport: FormatSupport | null;
  isFlying: boolean;
  imageDims: React.MutableRefObject<Map<string, { width: number; height: number }>>;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  receipt,
  formatSupport,
  isFlying,
  imageDims,
}) => {
  const key = receipt ? receiptKey(receipt) : "";
  const dims = imageDims.current.get(key);
  const width = dims?.width ?? 200;
  const height = dims?.height ?? 300;
  const { rotation, leftOffset } = getQueuePosition(key);

  const imageUrl = useMemo(() => {
    if (!formatSupport || !receipt) return null;
    return getBestImageUrl(buildCdnKeys(receipt.image_id, receipt.receipt_id), formatSupport);
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

  const distanceToQueueItemCenter = (centerColumnWidth / 2) + gap + (queueWidth - (10 + leftOffset + queueItemWidth / 2));
  const startX = -distanceToQueueItemCenter;

  const queueItemHeight = (height / width) * queueItemWidth;
  const queueItemCenterFromTop = ((centerColumnHeight - queueHeight) / 2) + (queueItemHeight / 2);
  const startY = queueItemCenterFromTop - (centerColumnHeight / 2);
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

// ---------- Active Receipt Viewer ----------

interface ActiveReceiptViewerProps {
  receipt: EvidenceReceipt;
  scanProgress: number;
  formatSupport: FormatSupport | null;
  imageDims: React.MutableRefObject<Map<string, { width: number; height: number }>>;
}

const ActiveReceiptViewer: React.FC<ActiveReceiptViewerProps> = ({
  receipt,
  scanProgress,
  formatSupport,
  imageDims,
}) => {
  const key = receiptKey(receipt);
  const cdnKeys = buildCdnKeys(receipt.image_id, receipt.receipt_id);

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(cdnKeys, formatSupport);
  }, [cdnKeys, formatSupport]);

  const dims = imageDims.current.get(key);
  const imgWidth = dims?.width ?? 300;
  const imgHeight = dims?.height ?? 500;

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  return (
    <div className={styles.activeReceipt}>
      <div className={styles.receiptImageWrapper}>
        <div className={styles.receiptImageInner}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="Receipt"
            className={styles.receiptImage}
            onLoad={(e) => {
              const img = e.currentTarget as HTMLImageElement;
              if (!imageDims.current.has(key)) {
                imageDims.current.set(key, { width: img.naturalWidth, height: img.naturalHeight });
              }
            }}
          />
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${imgWidth} ${imgHeight}`}
            preserveAspectRatio="none"
          >
            {scanProgress > 0 && (
              <>
                <defs>
                  <linearGradient id={`scanGrad-${key}`} x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="transparent" />
                    <stop offset="20%" stopColor="var(--color-red)" />
                    <stop offset="80%" stopColor="var(--color-red)" />
                    <stop offset="100%" stopColor="transparent" />
                  </linearGradient>
                  <filter id={`scanGlow-${key}`} x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur stdDeviation="4" result="blur" />
                    <feMerge>
                      <feMergeNode in="blur" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>
                </defs>
                <rect
                  x="0"
                  y={(scanProgress / 100) * imgHeight}
                  width={imgWidth}
                  height={Math.max(imgHeight * 0.005, 3)}
                  fill={`url(#scanGrad-${key})`}
                  filter={`url(#scanGlow-${key})`}
                />
              </>
            )}
          </svg>
        </div>
      </div>
    </div>
  );
};

// ---------- Evidence Panel (right column) ----------

interface EvidencePanelProps {
  receipt: EvidenceReceipt;
  revealedCount: number;
  scanComplete: boolean;
}

const DecisionBadge: React.FC<{ decision: string }> = ({ decision }) => {
  const cls =
    decision === "VALID"
      ? styles.badgeValid
      : decision === "INVALID"
        ? styles.badgeInvalid
        : styles.badgeNeedsReview;
  return <span className={`${styles.badge} ${cls}`}>{decision}</span>;
};

const EvidencePanel: React.FC<EvidencePanelProps> = ({
  receipt,
  revealedCount,
  scanComplete,
}) => {
  const issues = receipt.issues_with_evidence;
  const { summary } = receipt;

  return (
    <div className={styles.evidencePanel}>
      {issues.map((issue, idx) => {
        const isRevealed = idx < revealedCount;
        return (
          <div
            key={`${issue.line_id}-${issue.word_id}`}
            className={`${styles.issueRow} ${isRevealed ? styles.revealed : ""}`}
          >
            <div className={styles.issueRowHeader}>
              <span className={styles.issueWordText} title={issue.word_text}>
                {issue.word_text}
              </span>
              <span className={styles.issueLabel}>{issue.current_label}</span>
              <DecisionBadge decision={issue.decision} />
            </div>
            <div className={styles.consensusBar}>
              <div
                className={styles.consensusMarker}
                style={{ left: `${((issue.consensus_score + 1) / 2) * 100}%` }}
              />
            </div>
            <span className={styles.evidenceCount}>
              {issue.similar_word_count} similar words
            </span>
          </div>
        );
      })}

      <div className={`${styles.summarySection} ${scanComplete ? styles.summaryRevealed : ""}`}>
        <div className={styles.summaryTitle}>Summary</div>
        <div className={styles.summaryRow}>
          <span>Avg Consensus</span>
          <span className={styles.summaryValue}>{summary.avg_consensus_score.toFixed(2)}</span>
        </div>
        <div className={styles.decisionBreakdown}>
          <span className={styles.decisionStat}>
            <span className={styles.decisionDot} style={{ background: "var(--color-green)" }} />
            {summary.decisions.VALID}
          </span>
          <span className={styles.decisionStat}>
            <span className={styles.decisionDot} style={{ background: "var(--color-orange)" }} />
            {summary.decisions.INVALID}
          </span>
          <span className={styles.decisionStat}>
            <span className={styles.decisionDot} style={{ background: "var(--color-yellow)" }} />
            {summary.decisions.NEEDS_REVIEW}
          </span>
        </div>
      </div>
    </div>
  );
};

// ---------- Main Component ----------

const EvidenceVisualization: React.FC = () => {
  const { ref, inView } = useInView({ threshold: 0.3, triggerOnce: false });

  const [receipts, setReceipts] = useState<EvidenceReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [currentReceiptIndex, setCurrentReceiptIndex] = useState(0);
  const [scanProgress, setScanProgress] = useState(0);
  const [revealedCount, setRevealedCount] = useState(0);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);
  const [isPoolExhausted, setIsPoolExhausted] = useState(false);
  const [startQueueAnimation, setStartQueueAnimation] = useState(false);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const isFetchingRef = useRef(false);
  const seenReceiptIds = useRef<Set<string>>(new Set());
  const emptyFetchCountRef = useRef(0);
  const isPoolExhaustedRef = useRef(isPoolExhausted);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  // Ref map for image dimensions (avoid React 18 batching pitfalls)
  const imageDims = useRef<Map<string, { width: number; height: number }>>(new Map());

  useEffect(() => {
    isPoolExhaustedRef.current = isPoolExhausted;
  }, [isPoolExhausted]);

  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

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
      const response = await api.fetchLabelEvaluatorEvidence();
      if (response && response.receipts) {
        const newReceipts = response.receipts.filter(
          (r) => !seenReceiptIds.current.has(receiptKey(r))
        );
        newReceipts.forEach((r) => seenReceiptIds.current.add(receiptKey(r)));
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
      console.error("Failed to fetch more evidence receipts:", err);
    } finally {
      isFetchingRef.current = false;
    }
  }, [isPoolExhausted]);

  // Initial fetch
  useEffect(() => {
    const initialFetch = async () => {
      try {
        const [response1, response2] = await Promise.all([
          api.fetchLabelEvaluatorEvidence(),
          api.fetchLabelEvaluatorEvidence(),
        ]);
        const allReceipts: EvidenceReceipt[] = [];
        for (const resp of [response1, response2]) {
          if (resp?.receipts) {
            resp.receipts.forEach((r) => {
              const key = receiptKey(r);
              if (!seenReceiptIds.current.has(key)) {
                seenReceiptIds.current.add(key);
                allReceipts.push(r);
              }
            });
          }
        }
        setReceipts(allReceipts);
        setError(null);
      } catch (err) {
        console.error("Failed to fetch initial evidence:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setInitialLoading(false);
      }
    };
    initialFetch();
  }, []);

  // Refetch when queue runs low
  const remainingReceipts = receipts.length - currentReceiptIndex;
  useEffect(() => {
    if (remainingReceipts < QUEUE_REFETCH_THRESHOLD && !isFetchingRef.current && !initialLoading && !isPoolExhausted) {
      fetchMoreReceipts();
    }
  }, [remainingReceipts, fetchMoreReceipts, initialLoading, isPoolExhausted]);

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

      const issueCount = currentReceipt.issues_with_evidence.length;
      const totalDuration = SCAN_DURATION + HOLD_DURATION + TRANSITION_DURATION;

      if (elapsed < SCAN_DURATION) {
        // SCAN PHASE - reveal issues progressively
        const progress = (elapsed / SCAN_DURATION) * 100;
        setScanProgress(Math.min(progress, 100));
        // Reveal issues proportionally to scan progress
        const revealed = Math.floor((elapsed / SCAN_DURATION) * issueCount);
        setRevealedCount(Math.min(revealed, issueCount));
        setIsTransitioning(false);
      } else if (elapsed < SCAN_DURATION + HOLD_DURATION) {
        // HOLD PHASE - all issues revealed
        setScanProgress(100);
        setRevealedCount(issueCount);
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
        setRevealedCount(0);
        setIsTransitioning(false);
        startTime = currentTime;
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
  }, [inView, receipts.length > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  if (initialLoading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading evidence data...
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
        No evidence data available
      </div>
    );
  }

  const currentReceipt = receipts[currentReceiptIndex];
  const nextIndex = currentReceiptIndex + 1;
  const nextReceipt = isPoolExhausted
    ? receipts[nextIndex % receipts.length]
    : receipts[nextIndex];

  const scanComplete = scanProgress >= 100;

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
          imageDims={imageDims}
        />

        <div className={styles.centerColumn}>
          <div className={`${styles.receiptContainer} ${isTransitioning ? styles.fadeOut : ""}`}>
            <ActiveReceiptViewer
              receipt={currentReceipt}
              scanProgress={scanProgress}
              formatSupport={formatSupport}
              imageDims={imageDims}
            />
          </div>

          <div className={styles.flyingReceiptContainer}>
            {isTransitioning && nextReceipt && (
              <FlyingReceipt
                key={`flying-${receiptKey(nextReceipt)}`}
                receipt={nextReceipt}
                formatSupport={formatSupport}
                isFlying={isTransitioning}
                imageDims={imageDims}
              />
            )}
          </div>

          {isTransitioning && nextReceipt && (
            <div className={`${styles.receiptContainer} ${styles.nextReceipt} ${styles.fadeIn}`}>
              <ActiveReceiptViewer
                receipt={nextReceipt}
                scanProgress={0}
                formatSupport={formatSupport}
                imageDims={imageDims}
              />
            </div>
          )}
        </div>

        <EvidencePanel
          receipt={currentReceipt}
          revealedCount={revealedCount}
          scanComplete={scanComplete}
        />
      </div>
    </div>
  );
};

export default EvidenceVisualization;
