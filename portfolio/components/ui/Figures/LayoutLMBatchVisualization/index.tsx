import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { animated, useSpring } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import Image from "next/image";
import { api } from "../../../../services/api";
import {
  LayoutLMReceiptInference,
  LayoutLMReceiptWord,
} from "../../../../types/api";
import { getBestImageUrl, usePreloadReceiptImages } from "../../../../utils/imageFormat";
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
import styles from "./LayoutLMBatchVisualization.module.css";

// Label colors for 8-label hybrid model
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  AMOUNT: "var(--color-green)",
  ADDRESS: "var(--color-red)",
  WEBSITE: "var(--color-purple)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-orange)",
  O: "var(--text-color)",
};

// Entity type display names
const ENTITY_DISPLAY_NAMES: Record<string, string> = {
  MERCHANT_NAME: "Merchant",
  DATE: "Date",
  TIME: "Time",
  AMOUNT: "Amount",
  ADDRESS: "Address",
  WEBSITE: "Website",
  STORE_HOURS: "Hours",
  PAYMENT_METHOD: "Payment",
};

// Entity types in order (grouped by color for visual clarity)
const ENTITY_TYPES = [
  "MERCHANT_NAME",
  "DATE",
  "TIME",
  "AMOUNT",
  "ADDRESS",
  "WEBSITE",
  "STORE_HOURS",
  "PAYMENT_METHOD",
];

// Mobile legend groups - combine same-colored labels
const MOBILE_LEGEND_GROUPS = [
  { color: "var(--color-yellow)", label: "Merchant", types: ["MERCHANT_NAME"] },
  { color: "var(--color-blue)", label: "Date / Time", types: ["DATE", "TIME"] },
  { color: "var(--color-green)", label: "Amount", types: ["AMOUNT"] },
  { color: "var(--color-red)", label: "Address", types: ["ADDRESS"] },
  { color: "var(--color-purple)", label: "Website", types: ["WEBSITE"] },
  { color: "var(--color-orange)", label: "Hours / Payment", types: ["STORE_HOURS", "PAYMENT_METHOD"] },
];

// Animation timing
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

interface ReceiptQueueProps {
  receipts: LayoutLMReceiptInference[];
  currentIndex: number;
  formatSupport: ImageFormatSupport | null;
  isTransitioning: boolean;
  isPoolExhausted: boolean;
  shouldAnimate: boolean;
  fadeDelay?: number;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
  isPoolExhausted,
  shouldAnimate,
  fadeDelay = 50,
}) => {
  const maxVisible = 6; // Show up to 6 receipts in the visual queue
  const [imagesLoaded, setImagesLoaded] = useState<Set<number>>(new Set());

  // Track when images load for staggered animation
  const handleImageLoad = useCallback((idx: number) => {
    setImagesLoaded((prev) => new Set(prev).add(idx));
  }, []);

  // Build visible receipts array - handles looping when pool is exhausted
  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    return getVisibleQueueIndices(receipts.length, currentIndex, maxVisible, isPoolExhausted).map(
      (idx) => receipts[idx]
    );
  }, [receipts, currentIndex, isPoolExhausted]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  const STACK_GAP = 20; // Gap between stacked receipts

  return (
    <div className={styles.receiptQueue} data-rf-queue>
      {visibleReceipts.map((receipt, idx) => {
        const imageUrl = getBestImageUrl(receipt.original.receipt, formatSupport);
        const { width, height } = receipt.original.receipt;
        // Position is based only on receipt ID - stays stable as items are removed
        const { rotation, leftOffset } = getQueuePosition(receipt.receipt_id);

        // During transition, shift positions up (first item flies away)
        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const zIndex = maxVisible - idx;
        // First item fades out during transition
        const isFlying = isTransitioning && idx === 0;

        // Use position-based key to handle duplicate receipts when looping
        const queueKey = `${receipt.receipt_id}-queue-${idx}`;

        // Animation state: drop in from above with staggered delay
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
                width={width}
                height={height}
                style={{
                  width: "100%",
                  height: "auto",
                  display: "block",
                }}
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

interface LegendItemProps {
  entityType: string;
  isRevealed: boolean;
}

const LegendItem: React.FC<LegendItemProps> = ({ entityType, isRevealed }) => {
  const color = LABEL_COLORS[entityType] || LABEL_COLORS.O;
  const displayName = ENTITY_DISPLAY_NAMES[entityType] || entityType;

  return (
    <div
      className={`${styles.legendItem} ${isRevealed ? styles.revealed : ""}`}
    >
      <div className={styles.legendDot} style={{ backgroundColor: color }} />
      <span className={styles.legendLabel}>{displayName}</span>
    </div>
  );
};

interface EntityLegendProps {
  revealedEntityTypes: Set<string>;
  inferenceTimeMs: number;
  showInferenceTime: boolean;
}

const EntityLegend: React.FC<EntityLegendProps> = ({
  revealedEntityTypes,
  inferenceTimeMs,
  showInferenceTime,
}) => {
  const inferenceSpring = useSpring({
    opacity: showInferenceTime ? 1 : 0.2,
    config: { tension: 280, friction: 24 },
  });

  return (
    <div className={styles.entityLegend}>
      {/* Desktop: full legend (hidden on mobile via CSS) */}
      <div className={styles.legendDesktop}>
        {ENTITY_TYPES.map((entityType) => (
          <LegendItem
            key={entityType}
            entityType={entityType}
            isRevealed={revealedEntityTypes.has(entityType)}
          />
        ))}
      </div>
      {/* Mobile: grouped legend (hidden on desktop via CSS) */}
      <div className={styles.legendMobile}>
        {MOBILE_LEGEND_GROUPS.map((group) => {
          const isRevealed = group.types.some((t) => revealedEntityTypes.has(t));
          return (
            <div
              key={group.label}
              className={`${styles.legendItem} ${isRevealed ? styles.revealed : ""}`}
            >
              <div className={styles.legendDot} style={{ backgroundColor: group.color }} />
              <span className={styles.legendLabel}>{group.label}</span>
            </div>
          );
        })}
      </div>
      <animated.div className={styles.inferenceTime} style={inferenceSpring}>
        <span className={styles.inferenceLabel}>Inference Time</span>
        <span className={styles.inferenceValue}>{inferenceTimeMs.toFixed(0)}ms</span>
      </animated.div>
    </div>
  );
};

interface ActiveReceiptViewerProps {
  receipt: LayoutLMReceiptInference;
  scanProgress: number;
  revealedWordIds: Set<string>;
  formatSupport: ImageFormatSupport | null;
}

const ActiveReceiptViewer: React.FC<ActiveReceiptViewerProps> = ({
  receipt,
  scanProgress,
  revealedWordIds,
  formatSupport,
}) => {
  const { original } = receipt;
  const { receipt: receiptData, words, predictions } = original;

  // Get the best image URL based on format support
  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receiptData, formatSupport);
  }, [receiptData, formatSupport]);

  // Build word lookup for bounding boxes
  const wordLookup = useMemo(() => {
    const lookup = new Map<string, LayoutLMReceiptWord>();
    for (const word of words) {
      lookup.set(`${word.line_id}_${word.word_id}`, word);
    }
    return lookup;
  }, [words]);

  // Get predictions with non-O labels that are revealed
  const visiblePredictions = useMemo(() => {
    return predictions.filter((pred) => {
      if (pred.predicted_label_base === "O") return false;
      const key = `${pred.line_id}_${pred.word_id}`;
      return revealedWordIds.has(key);
    });
  }, [predictions, revealedWordIds]);

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
            width={receiptData.width}
            height={receiptData.height}
            className={styles.receiptImage}
          />

          {/* SVG overlay for bounding boxes and scan line */}
          {/* Use unique IDs per receipt to avoid conflicts during crossfade */}
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${receiptData.width} ${receiptData.height}`}
            preserveAspectRatio="none"
          >
            {/* Scan line - positioned in image coordinates */}
            <defs>
              <linearGradient id={`scanLineGradient-${receipt.receipt_id}`} x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="transparent" />
                <stop offset="20%" stopColor="var(--color-red)" />
                <stop offset="80%" stopColor="var(--color-red)" />
                <stop offset="100%" stopColor="transparent" />
              </linearGradient>
              <filter id={`scanLineGlow-${receipt.receipt_id}`} x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="4" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            {/* Only show scan line when scanProgress > 0 to avoid flash at top during transitions */}
            {scanProgress > 0 && (
              <rect
                x="0"
                y={(scanProgress / 100) * receiptData.height}
                width={receiptData.width}
                height={Math.max(receiptData.height * 0.005, 3)}
                fill={`url(#scanLineGradient-${receipt.receipt_id})`}
                filter={`url(#scanLineGlow-${receipt.receipt_id})`}
              />
            )}

            {/* Bounding boxes */}
            {visiblePredictions.map((pred, idx) => {
              const key = `${pred.line_id}_${pred.word_id}`;
              const word = wordLookup.get(key);
              if (!word) return null;

              const { bounding_box } = word;
              const color = LABEL_COLORS[pred.predicted_label_base] || LABEL_COLORS.O;

              // Convert normalized bounding box to pixel coordinates
              const x = bounding_box.x * receiptData.width;
              const y = (1 - bounding_box.y - bounding_box.height) * receiptData.height;
              const width = bounding_box.width * receiptData.width;
              const height = bounding_box.height * receiptData.height;

              return (
                <rect
                  key={`${key}-${idx}`}
                  x={x}
                  y={y}
                  width={width}
                  height={height}
                  fill={color}
                  fillOpacity={0.3}
                  stroke={color}
                  strokeWidth={2}
                />
              );
            })}
          </svg>
        </div>
      </div>
    </div>
  );
};

// Queue management constants
const QUEUE_REFETCH_THRESHOLD = 7;
const MAX_EMPTY_FETCHES = 3; // After this many fetches with no new receipts, stop trying

// Inner component - only mounted when receipts are loaded
interface LayoutLMBatchInnerProps {
  observerRef: (node?: Element | null) => void;
  inView: boolean;
  receipts: LayoutLMReceiptInference[];
  formatSupport: ImageFormatSupport | null;
  isPoolExhausted: boolean;
  onFetchMore: () => void;
}

const LayoutLMBatchInner: React.FC<LayoutLMBatchInnerProps> = ({
  observerRef,
  inView,
  receipts,
  formatSupport,
  isPoolExhausted,
  onFetchMore,
}) => {
  const [currentReceiptIndex, setCurrentReceiptIndex] = useState(0);
  const [scanProgress, setScanProgress] = useState(0);
  const [revealedEntityTypes, setRevealedEntityTypes] = useState<Set<string>>(new Set());
  const [showInferenceTime, setShowInferenceTime] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [startQueueAnimation, setStartQueueAnimation] = useState(false);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const isPoolExhaustedRef = useRef(isPoolExhausted);

  // Keep ref in sync with prop for animation loop access
  useEffect(() => {
    isPoolExhaustedRef.current = isPoolExhausted;
  }, [isPoolExhausted]);

  // Start queue animation when in view and receipts are loaded
  useEffect(() => {
    if (inView && receipts.length > 0 && !startQueueAnimation) {
      setStartQueueAnimation(true);
    }
  }, [inView, receipts.length, startQueueAnimation]);

  // Check if we need to fetch more receipts (when queue is getting low)
  // Skip fetching if pool is exhausted (we'll loop instead)
  const remainingReceipts = receipts.length - currentReceiptIndex;
  useEffect(() => {
    if (remainingReceipts < QUEUE_REFETCH_THRESHOLD && !isPoolExhausted) {
      onFetchMore();
    }
  }, [remainingReceipts, onFetchMore, isPoolExhausted]);

  // Calculate revealed word IDs based on scan progress
  const revealedWordIds = useMemo(() => {
    const receipt = receipts[currentReceiptIndex];
    if (!receipt) {
      return new Set<string>();
    }

    const { words } = receipt.original;
    const revealed = new Set<string>();

    // Scan progress is 0-100, representing vertical position
    const scanY = scanProgress / 100;

    for (const word of words) {
      // Word is revealed when scan line passes its top edge
      // bounding_box.y is normalized from bottom, so we need to invert
      const wordTopY = 1 - word.bounding_box.y - word.bounding_box.height;
      if (wordTopY <= scanY) {
        revealed.add(`${word.line_id}_${word.word_id}`);
      }
    }

    return revealed;
  }, [receipts, currentReceiptIndex, scanProgress]);

  // Update revealed entity types based on scan progress
  const updateRevealedEntityTypes = useCallback(() => {
    const receipt = receipts[currentReceiptIndex];
    if (!receipt) return;

    const { words, predictions } = receipt.original;
    const scanY = scanProgress / 100;

    // Build word lookup for efficient access
    const wordMap = new Map<string, (typeof words)[0]>();
    for (const w of words) {
      wordMap.set(`${w.line_id}_${w.word_id}`, w);
    }

    const newRevealed = new Set<string>();

    for (const pred of predictions) {
      if (pred.predicted_label_base === "O") continue;

      // Find the corresponding word using Map lookup
      const word = wordMap.get(`${pred.line_id}_${pred.word_id}`);
      if (!word) continue;

      // Check if word is revealed by scan
      const wordTopY = 1 - word.bounding_box.y - word.bounding_box.height;
      if (wordTopY <= scanY) {
        newRevealed.add(pred.predicted_label_base);
      }
    }

    setRevealedEntityTypes(newRevealed);
  }, [receipts, currentReceiptIndex, scanProgress]);

  // Update revealed entities when scan progress changes
  useEffect(() => {
    updateRevealedEntityTypes();
  }, [scanProgress, updateRevealedEntityTypes]);

  // Animation loop - uses actual inference time per receipt
  // Uses a ref to track receipts length to avoid restarting animation
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  const hasReceipts = receipts.length > 0;
  useEffect(() => {
    if (!inView || !hasReceipts) {
      return;
    }

    // Prevent multiple animation loops
    if (isAnimatingRef.current) {
      return;
    }
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
        // No receipt at this index yet - wait for more to load
        animationRef.current = requestAnimationFrame(animate);
        return;
      }

      const scanDuration = currentReceipt.inference_time_ms;
      const totalDuration = scanDuration + HOLD_DURATION + TRANSITION_DURATION;

      if (elapsed < scanDuration) {
        // SCAN PHASE
        const progress = (elapsed / scanDuration) * 100;
        setScanProgress(Math.min(progress, 100));
        setShowInferenceTime(false);
        setIsTransitioning(false);
      } else if (elapsed < scanDuration + HOLD_DURATION) {
        // HOLD PHASE
        setScanProgress(100);
        setShowInferenceTime(true);
        setIsTransitioning(false);
      } else if (elapsed < totalDuration) {
        // TRANSITION PHASE - animate receipt flying from queue to center
        if (!isInTransition) {
          isInTransition = true;
          setIsTransitioning(true);
        }
      } else {
        // MOVE TO NEXT RECEIPT
        let nextIndex = receiptIndex + 1;

        // Check if next receipt exists
        if (nextIndex >= currentReceipts.length) {
          // If pool is exhausted, loop back to the beginning
          if (isPoolExhaustedRef.current) {
            nextIndex = 0;
          } else {
            // Wait for more receipts to be fetched
            animationRef.current = requestAnimationFrame(animate);
            return;
          }
        }

        receiptIndex = nextIndex;
        isInTransition = false;
        setCurrentReceiptIndex(receiptIndex);
        setScanProgress(0);
        setRevealedEntityTypes(new Set());
        setShowInferenceTime(false);
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
  }, [inView, hasReceipts]);

  // Get next receipt - use modulo when pool is exhausted for looping
  const getNextReceipt = useCallback(
    (items: LayoutLMReceiptInference[], idx: number) => {
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

  const flyingElement = useMemo(() => {
    if (!showFlying || !flyingItem || !formatSupport) return null;
    const fUrl = getBestImageUrl(flyingItem.original.receipt, formatSupport);
    if (!fUrl) return null;
    const { width, height } = flyingItem.original.receipt;
    const ar = width / height;
    let dh = Math.min(500, height);
    let dw = dh * ar;
    if (dw > 350) { dw = 350; dh = dw / ar; }
    return (
      <FlyingReceipt
        key={`flying-${flyingItem.receipt_id}`}
        imageUrl={fUrl}
        displayWidth={dw}
        displayHeight={dh}
        receiptId={flyingItem.receipt_id}
      />
    );
  }, [showFlying, flyingItem, formatSupport]);

  const currentReceipt = receipts[currentReceiptIndex];

  const nextReceipt = isPoolExhausted
    ? receipts[(currentReceiptIndex + 1) % receipts.length]
    : receipts[currentReceiptIndex + 1];

  return (
    <div ref={observerRef} className={styles.container}>
      <ReceiptFlowShell
        layoutVars={
          {
            "--rf-queue-width": "120px",
            "--rf-queue-height": "400px",
            "--rf-center-max-width": "350px",
            "--rf-center-height": "500px",
            "--rf-mobile-center-height": "400px",
            "--rf-mobile-center-height-sm": "320px",
            "--rf-gap": "1.5rem",
          } as React.CSSProperties
        }
        isTransitioning={isTransitioning}
        queue={
          <ReceiptQueue
            receipts={receipts}
            currentIndex={currentReceiptIndex}
            formatSupport={formatSupport}
            isTransitioning={isTransitioning}
            isPoolExhausted={isPoolExhausted}
            shouldAnimate={startQueueAnimation}
            fadeDelay={50}
          />
        }
        center={
          <ActiveReceiptViewer
            receipt={currentReceipt}
            scanProgress={scanProgress}
            revealedWordIds={revealedWordIds}
            formatSupport={formatSupport}
          />
        }
        flying={flyingElement}
        next={
          isTransitioning && nextReceipt ? (
            <ActiveReceiptViewer
              receipt={nextReceipt}
              scanProgress={0}
              revealedWordIds={new Set()}
              formatSupport={formatSupport}
            />
          ) : null
        }
        legend={
          <EntityLegend
          revealedEntityTypes={revealedEntityTypes}
          inferenceTimeMs={currentReceipt.inference_time_ms}
          showInferenceTime={showInferenceTime}
          />
        }
      />
    </div>
  );
};

// Outer component - handles data fetching and loading guards
const LayoutLMBatchVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [receipts, setReceipts] = useState<LayoutLMReceiptInference[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const formatSupport = useImageFormatSupport();
  const [isPoolExhausted, setIsPoolExhausted] = useState(false);

  const allImageFormats = useMemo(() => receipts.map((r) => r.original.receipt), [receipts]);
  usePreloadReceiptImages(allImageFormats, formatSupport);

  const isFetchingRef = useRef(false);
  const seenReceiptIds = useRef<Set<string>>(new Set());
  const emptyFetchCountRef = useRef(0);

  // Fetch a batch of receipts and append to queue (with deduplication)
  const fetchMoreReceipts = useCallback(async () => {
    if (isFetchingRef.current || isPoolExhausted) return;
    isFetchingRef.current = true;

    try {
      const response = await api.fetchLayoutLMInference();
      if (response && response.receipts) {
        // Filter out duplicates
        const newReceipts = response.receipts.filter(
          (r) => !seenReceiptIds.current.has(r.receipt_id)
        );

        // Track new receipt IDs
        newReceipts.forEach((r) => seenReceiptIds.current.add(r.receipt_id));

        if (newReceipts.length > 0) {
          setReceipts((prev) => [...prev, ...newReceipts]);
          // Reset empty fetch counter when we get new receipts
          emptyFetchCountRef.current = 0;
        } else {
          // No new receipts - increment empty fetch counter
          emptyFetchCountRef.current += 1;
          if (emptyFetchCountRef.current >= MAX_EMPTY_FETCHES) {
            // Pool is exhausted - stop fetching and enable looping
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

  // Initial fetch - get 2 batches to start with ~10 receipts
  useEffect(() => {
    const initialFetch = async () => {
      try {
        // Fetch 2 batches in parallel
        const [response1, response2] = await Promise.all([
          api.fetchLayoutLMInference(),
          api.fetchLayoutLMInference(),
        ]);

        const allReceipts: LayoutLMReceiptInference[] = [];

        // Add receipts from first batch
        if (response1?.receipts) {
          response1.receipts.forEach((r) => {
            if (!seenReceiptIds.current.has(r.receipt_id)) {
              seenReceiptIds.current.add(r.receipt_id);
              allReceipts.push(r);
            }
          });
        }

        // Add receipts from second batch (deduplicated)
        if (response2?.receipts) {
          response2.receipts.forEach((r) => {
            if (!seenReceiptIds.current.has(r.receipt_id)) {
              seenReceiptIds.current.add(r.receipt_id);
              allReceipts.push(r);
            }
          });
        }

        setReceipts(allReceipts);
        setError(null);
      } catch (err) {
        console.error("Failed to fetch initial receipts:", err);
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setInitialLoading(false);
      }
    };

    initialFetch();
  }, []);

  const LAYOUT_VARS = {
    "--rf-queue-width": "120px",
    "--rf-queue-height": "400px",
    "--rf-center-max-width": "350px",
    "--rf-center-height": "500px",
    "--rf-mobile-center-height": "400px",
    "--rf-mobile-center-height-sm": "320px",
    "--rf-gap": "1.5rem",
  } as React.CSSProperties;

  if (initialLoading) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="layoutlm"
        />
      </div>
    );
  }

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="layoutlm"
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
          variant="layoutlm"
          message="No inference data available"
        />
      </div>
    );
  }

  return (
    <LayoutLMBatchInner
      observerRef={ref}
      inView={inView}
      receipts={receipts}
      formatSupport={formatSupport}
      isPoolExhausted={isPoolExhausted}
      onFetchMore={fetchMoreReceipts}
    />
  );
};

export default LayoutLMBatchVisualization;
