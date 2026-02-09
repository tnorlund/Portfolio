import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { animated, useSpring, to } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import Image from "next/image";
import { api } from "../../../../services/api";
import { DiffReceipt, DiffWord } from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl } from "../../../../utils/imageFormat";
import styles from "./DiffVisualization.module.css";

// Label colors for word bounding boxes
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  LINE_TOTAL: "var(--color-green)",
  SUBTOTAL: "var(--color-green)",
  TAX: "var(--color-green)",
  GRAND_TOTAL: "var(--color-green)",
  PAYMENT_METHOD: "var(--color-green)",
  ADDRESS_LINE: "var(--color-red)",
  PHONE_NUMBER: "var(--color-red)",
  WEBSITE: "var(--color-purple)",
  PRODUCT_NAME: "var(--color-orange)",
  LOYALTY_ID: "var(--color-purple)",
  O: "var(--text-color)",
};

// Change source display colors and labels
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

// Build CDN image keys from image_id and receipt_id
function buildCdnKeys(imageId: string, receiptId: number) {
  const paddedId = String(receiptId).padStart(5, "0");
  const base = `assets/${imageId}_RECEIPT_${paddedId}`;
  return {
    cdn_s3_key: `${base}.jpg`,
    cdn_webp_s3_key: `${base}.webp`,
    cdn_avif_s3_key: `${base}.avif`,
  };
}

// Animation timing
const SCAN_DURATION = 2000;
const REPAINT_DURATION = 1000;
const HOLD_DURATION = 1500;
const TRANSITION_DURATION = 600;
const TOTAL_DURATION = SCAN_DURATION + REPAINT_DURATION + HOLD_DURATION + TRANSITION_DURATION;

// Generate stable random positions for queue items
const getQueuePosition = (receiptKey: string) => {
  const hash = receiptKey.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;
  const rotation = Math.abs(random1) * 24 - 12;
  const leftOffset = Math.abs(random2) * 10 - 5;
  return { rotation, leftOffset };
};

// Queue management
const QUEUE_REFETCH_THRESHOLD = 7;
const MAX_EMPTY_FETCHES = 3;

interface ReceiptQueueProps {
  receipts: DiffReceipt[];
  currentIndex: number;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isTransitioning: boolean;
  isPoolExhausted: boolean;
  shouldAnimate: boolean;
  imageDims: Map<string, { width: number; height: number }>;
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
  const STACK_GAP = 20;

  const handleImageLoad = useCallback((idx: number) => {
    setImagesLoaded((prev) => new Set(prev).add(idx));
  }, []);

  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    const result: DiffReceipt[] = [];
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
        const rKey = `${receipt.image_id}_${receipt.receipt_id}`;
        const cdnKeys = buildCdnKeys(receipt.image_id, receipt.receipt_id);
        const imageUrl = getBestImageUrl(cdnKeys, formatSupport);
        const dims = imageDims.get(rKey);
        const { rotation, leftOffset } = getQueuePosition(rKey);
        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        const isFlying = isTransitioning && idx === 0;
        const isImageLoaded = imagesLoaded.has(idx);
        const showItem = shouldAnimate && isImageLoaded;

        return (
          <div
            key={`${rKey}-queue-${idx}`}
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
                onLoad={() => handleImageLoad(idx)}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

// Flying receipt animation
interface FlyingReceiptProps {
  receipt: DiffReceipt | null;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isFlying: boolean;
  imageDims: Map<string, { width: number; height: number }>;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  receipt,
  formatSupport,
  isFlying,
  imageDims,
}) => {
  const rKey = receipt ? `${receipt.image_id}_${receipt.receipt_id}` : "";
  const dims = imageDims.get(rKey);
  const width = dims?.width ?? 200;
  const height = dims?.height ?? 300;
  const { rotation, leftOffset } = getQueuePosition(rKey);

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

  const distanceToQueueItemCenter =
    centerColumnWidth / 2 + gap + (queueWidth - (10 + leftOffset + queueItemWidth / 2));
  const startX = -distanceToQueueItemCenter;

  const queueItemHeight = (height / width) * queueItemWidth;
  const queueHeight = 400;
  const queueItemCenterFromTop = (centerColumnHeight - queueHeight) / 2 + queueItemHeight / 2;
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

// Change summary panel - right column
interface ChangeSummaryProps {
  receipt: DiffReceipt;
  phase: "before" | "repaint" | "after";
}

const ChangeSummary: React.FC<ChangeSummaryProps> = ({ receipt, phase }) => {
  const changeSources = useMemo(() => {
    const sources: Record<string, number> = {};
    for (const word of receipt.words) {
      if (word.changed && word.change_source) {
        sources[word.change_source] = (sources[word.change_source] || 0) + 1;
      }
    }
    return sources;
  }, [receipt]);

  const allSources = Object.keys(CHANGE_SOURCE_COLORS);
  const showRepaint = phase === "repaint" || phase === "after";

  return (
    <div className={styles.changeSummary}>
      <div className={styles.changeBadge}>
        <span className={styles.changeBadgeCount}>{receipt.change_count}</span>
        <br />
        <span className={styles.changeBadgeSub}>of {receipt.word_count} words changed</span>
      </div>

      <div className={styles.sourcesList}>
        <span className={styles.sourcesLabel}>Change Sources</span>
        {allSources.map((source) => {
          const count = changeSources[source] || 0;
          const isActive = showRepaint && count > 0;
          return (
            <div
              key={source}
              className={`${styles.sourceItem} ${isActive ? styles.active : ""}`}
            >
              <div
                className={styles.sourceDot}
                style={{ backgroundColor: CHANGE_SOURCE_COLORS[source] }}
              />
              <span className={styles.sourceName}>
                {CHANGE_SOURCE_LABELS[source] || source}
              </span>
              {count > 0 && <span className={styles.sourceCount}>{count}</span>}
            </div>
          );
        })}
      </div>

      <div
        className={`${styles.phaseIndicator} ${phase !== "before" ? styles.phaseActive : ""}`}
      >
        {phase === "before" ? "Scanning..." : phase === "repaint" ? "Applying changes..." : "Complete"}
      </div>
    </div>
  );
};

// Active receipt viewer with SVG overlays
interface ActiveReceiptViewerProps {
  receipt: DiffReceipt;
  scanProgress: number;
  phase: "before" | "repaint" | "after";
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  imageDims: Map<string, { width: number; height: number }>;
  onImageLoad: (key: string, w: number, h: number) => void;
}

const ActiveReceiptViewer: React.FC<ActiveReceiptViewerProps> = ({
  receipt,
  scanProgress,
  phase,
  formatSupport,
  imageDims,
  onImageLoad,
}) => {
  const rKey = `${receipt.image_id}_${receipt.receipt_id}`;
  const cdnKeys = buildCdnKeys(receipt.image_id, receipt.receipt_id);
  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(cdnKeys, formatSupport);
  }, [cdnKeys, formatSupport]);

  const dims = imageDims.get(rKey);
  const imgWidth = dims?.width ?? 300;
  const imgHeight = dims?.height ?? 500;

  const handleLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      onImageLoad(rKey, img.naturalWidth, img.naturalHeight);
    },
    [rKey, onImageLoad]
  );

  // Get visible words based on scan progress and phase
  const visibleWords = useMemo(() => {
    const scanY = scanProgress / 100;
    return receipt.words
      .map((word) => {
        // bbox y is normalized 0-1 from top
        const wordTopY = word.bbox.y;
        if (wordTopY > scanY && phase === "before") return null;

        // Determine which label to show
        let label: string | null;
        let opacity: number;

        if (phase === "before") {
          label = word.before_label;
          opacity = word.changed ? 0.6 : 0.3;
        } else if (phase === "repaint") {
          label = word.changed ? word.after_label : word.before_label;
          opacity = word.changed ? 0.8 : 0.3;
        } else {
          label = word.after_label;
          opacity = word.changed ? 0.8 : 0.3;
        }

        if (!label || label === "O") return null;

        return { word, label, opacity };
      })
      .filter(Boolean) as { word: DiffWord; label: string; opacity: number }[];
  }, [receipt.words, scanProgress, phase]);

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
            width={imgWidth}
            height={imgHeight}
            className={styles.receiptImage}
            onLoad={handleLoad}
          />

          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${imgWidth} ${imgHeight}`}
            preserveAspectRatio="none"
          >
            {/* Scan line during before phase */}
            {phase === "before" && scanProgress > 0 && (
              <>
                <defs>
                  <linearGradient id={`scanGrad-${rKey}`} x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="transparent" />
                    <stop offset="20%" stopColor="var(--color-red)" />
                    <stop offset="80%" stopColor="var(--color-red)" />
                    <stop offset="100%" stopColor="transparent" />
                  </linearGradient>
                  <filter id={`scanGlow-${rKey}`} x="-50%" y="-50%" width="200%" height="200%">
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
                  fill={`url(#scanGrad-${rKey})`}
                  filter={`url(#scanGlow-${rKey})`}
                />
              </>
            )}

            {/* Word bounding boxes */}
            {visibleWords.map(({ word, label, opacity }) => {
              const color = LABEL_COLORS[label] || LABEL_COLORS.O;
              const x = word.bbox.x * imgWidth;
              const y = word.bbox.y * imgHeight;
              const w = word.bbox.width * imgWidth;
              const h = word.bbox.height * imgHeight;

              return (
                <rect
                  key={`${word.line_id}_${word.word_id}`}
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  fill={color}
                  fillOpacity={opacity * 0.4}
                  stroke={color}
                  strokeWidth={word.changed && phase !== "before" ? 2.5 : 1.5}
                  strokeOpacity={opacity}
                />
              );
            })}
          </svg>
        </div>
      </div>
    </div>
  );
};

export default function DiffVisualization() {
  const { ref, inView } = useInView({ threshold: 0.3, triggerOnce: false });

  const [receipts, setReceipts] = useState<DiffReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [currentReceiptIndex, setCurrentReceiptIndex] = useState(0);
  const [scanProgress, setScanProgress] = useState(0);
  const [phase, setPhase] = useState<"before" | "repaint" | "after">("before");
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);
  const [isPoolExhausted, setIsPoolExhausted] = useState(false);
  const [startQueueAnimation, setStartQueueAnimation] = useState(false);
  const [imageDims, setImageDims] = useState<Map<string, { width: number; height: number }>>(
    new Map()
  );

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const isFetchingRef = useRef(false);
  const seenReceiptKeys = useRef<Set<string>>(new Set());
  const emptyFetchCountRef = useRef(0);
  const isPoolExhaustedRef = useRef(isPoolExhausted);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

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

  const handleImageLoad = useCallback((key: string, w: number, h: number) => {
    setImageDims((prev) => {
      if (prev.has(key)) return prev;
      const next = new Map(prev);
      next.set(key, { width: w, height: h });
      return next;
    });
  }, []);

  const fetchMoreReceipts = useCallback(async () => {
    if (isFetchingRef.current || isPoolExhausted) return;
    isFetchingRef.current = true;

    try {
      const response = await api.fetchLabelEvaluatorDiff(20);
      if (response && response.receipts) {
        const newReceipts = response.receipts.filter(
          (r) => !seenReceiptKeys.current.has(`${r.image_id}_${r.receipt_id}`)
        );
        newReceipts.forEach((r) =>
          seenReceiptKeys.current.add(`${r.image_id}_${r.receipt_id}`)
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
    let cancelled = false;
    (async () => {
      try {
        const [r1, r2] = await Promise.all([
          api.fetchLabelEvaluatorDiff(20),
          api.fetchLabelEvaluatorDiff(20),
        ]);

        const allReceipts: DiffReceipt[] = [];
        for (const r of [r1, r2]) {
          if (r?.receipts) {
            for (const receipt of r.receipts) {
              const key = `${receipt.image_id}_${receipt.receipt_id}`;
              if (!seenReceiptKeys.current.has(key)) {
                seenReceiptKeys.current.add(key);
                allReceipts.push(receipt);
              }
            }
          }
        }

        if (!cancelled) {
          setReceipts(allReceipts);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        if (!cancelled) setInitialLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Refetch when queue is low
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

      if (elapsed < SCAN_DURATION) {
        // Phase 1: Scan down with before labels
        const progress = (elapsed / SCAN_DURATION) * 100;
        setScanProgress(Math.min(progress, 100));
        setPhase("before");
        setIsTransitioning(false);
      } else if (elapsed < SCAN_DURATION + REPAINT_DURATION) {
        // Phase 2: Repaint - changed words flash to after labels
        setScanProgress(100);
        setPhase("repaint");
        setIsTransitioning(false);
      } else if (elapsed < SCAN_DURATION + REPAINT_DURATION + HOLD_DURATION) {
        // Hold with after labels
        setScanProgress(100);
        setPhase("after");
        setIsTransitioning(false);
      } else if (elapsed < TOTAL_DURATION) {
        // Transition to next receipt
        if (!isInTransition) {
          isInTransition = true;
          setIsTransitioning(true);
        }
      } else {
        // Move to next
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
        setPhase("before");
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
  }, [inView, receipts.length > 0]);

  if (initialLoading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading diff data...
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
        No diff data available
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
              phase={phase}
              formatSupport={formatSupport}
              imageDims={imageDims}
              onImageLoad={handleImageLoad}
            />
          </div>

          <div className={styles.flyingReceiptContainer}>
            {isTransitioning && nextReceipt && (
              <FlyingReceipt
                key={`flying-${nextReceipt.image_id}_${nextReceipt.receipt_id}`}
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
                phase="before"
                formatSupport={formatSupport}
                imageDims={imageDims}
                onImageLoad={handleImageLoad}
              />
            </div>
          )}
        </div>

        <ChangeSummary receipt={currentReceipt} phase={phase} />
      </div>
    </div>
  );
}
