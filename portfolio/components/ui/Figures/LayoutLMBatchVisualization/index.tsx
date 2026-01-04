import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { animated, useSpring, to } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import Image from "next/image";
import { api } from "../../../../services/api";
import {
  LayoutLMReceiptInference,
  LayoutLMReceiptWord,
} from "../../../../types/api";
import { detectImageFormatSupport, getBestImageUrl } from "../../../../utils/imageFormat";
import styles from "./LayoutLMBatchVisualization.module.css";

// Label colors matching the existing carousel
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  ADDRESS: "var(--color-red)",
  AMOUNT: "var(--color-green)",
  O: "var(--text-color)",
};

// Entity type display names
const ENTITY_DISPLAY_NAMES: Record<string, string> = {
  MERCHANT_NAME: "Merchant",
  DATE: "Date",
  ADDRESS: "Address",
  AMOUNT: "Amount",
};

// Entity types in order
const ENTITY_TYPES = ["MERCHANT_NAME", "DATE", "ADDRESS", "AMOUNT"];

// Animation timing
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

interface ReceiptQueueProps {
  receipts: LayoutLMReceiptInference[];
  currentIndex: number;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isTransitioning: boolean;
  isPoolExhausted: boolean;
}

// Generate stable random positions for queue items based on receipt ID only
const getQueuePosition = (receiptId: string) => {
  // Use receipt ID to generate consistent random values
  const hash = receiptId.split("").reduce((acc, char) => acc + char.charCodeAt(0), 0);

  // Pseudo-random based on hash only (not index)
  const random1 = Math.sin(hash * 9301 + 49297) % 1;
  const random2 = Math.sin(hash * 7919 + 12345) % 1;

  // Rotation between -12 and 12 degrees
  const rotation = (Math.abs(random1) * 24 - 12);
  // Small horizontal offset (-5 to 5 pixels)
  const leftOffset = (Math.abs(random2) * 10 - 5);

  return { rotation, leftOffset };
};

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
  isPoolExhausted,
}) => {
  const maxVisible = 6; // Show up to 6 receipts in the visual queue

  // Build visible receipts array - handles looping when pool is exhausted
  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];

    const result: LayoutLMReceiptInference[] = [];
    const totalReceipts = receipts.length;

    for (let i = 1; i <= maxVisible; i++) {
      const idx = currentIndex + i;

      if (isPoolExhausted) {
        // When pool is exhausted, wrap around using modulo
        result.push(receipts[idx % totalReceipts]);
      } else if (idx < totalReceipts) {
        // Normal behavior - show next receipts if available
        result.push(receipts[idx]);
      }
      // Otherwise, no receipt to show at this position (still loading)
    }

    return result;
  }, [receipts, currentIndex, isPoolExhausted]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  const STACK_GAP = 20; // Gap between stacked receipts

  return (
    <div className={styles.receiptQueue}>
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
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

// Flying receipt that animates from queue to center
interface FlyingReceiptProps {
  receipt: LayoutLMReceiptInference | null;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
  isFlying: boolean;
}

const FlyingReceipt: React.FC<FlyingReceiptProps> = ({
  receipt,
  formatSupport,
  isFlying,
}) => {
  // Extract values with fallbacks to satisfy hooks rules (hooks must be called unconditionally)
  const width = receipt?.original.receipt.width ?? 100;
  const height = receipt?.original.receipt.height ?? 150;
  const receiptId = receipt?.receipt_id ?? '';
  const { rotation, leftOffset } = getQueuePosition(receiptId);

  const imageUrl = useMemo(() => {
    if (!formatSupport || !receipt) return null;
    return getBestImageUrl(receipt.original.receipt, formatSupport);
  }, [receipt, formatSupport]);

  // Calculate the display dimensions - max height 500px, maintain aspect ratio
  const aspectRatio = width / height;
  const displayHeight = Math.min(500, height);
  const displayWidth = displayHeight * aspectRatio;

  // Calculate starting position to match queue item position
  // The flying receipt center needs to move from center of centerColumn to center of queue item

  // Layout dimensions
  const queueItemWidth = 100;
  const queueWidth = 120;
  const gap = 24; // 1.5rem
  const centerColumnWidth = 350;
  const queueHeight = 400;
  const centerColumnHeight = 500;

  // X calculation:
  // From center of centerColumn, go left to reach queue item center
  // - Half of center column width to reach left edge: 175px
  // - Gap between columns: 24px
  // - Queue item center is at (10 + leftOffset + 50) = (60 + leftOffset) from left of queue
  // - So from right edge of queue to queue item center: 120 - (60 + leftOffset) = (60 - leftOffset)
  const distanceToQueueItemCenter = (centerColumnWidth / 2) + gap + (queueWidth - (10 + leftOffset + queueItemWidth / 2));
  const startX = -distanceToQueueItemCenter;

  // Y calculation:
  // Both containers are vertically centered (align-items: center)
  // Queue top is at (centerColumnHeight - queueHeight) / 2 = 50px from top of centerColumn
  // Queue item at idx 0 is at top: 0, so its top is 50px from top of centerColumn
  // Queue item height based on aspect ratio
  const queueItemHeight = (height / width) * queueItemWidth;
  // Queue item center Y from top of centerColumn
  const queueItemCenterFromTop = ((centerColumnHeight - queueHeight) / 2) + (queueItemHeight / 2);
  // Center of centerColumn is at centerColumnHeight / 2 = 250px
  const startY = queueItemCenterFromTop - (centerColumnHeight / 2);

  // Scale: queue item is 100px wide, so scale = 100 / displayWidth
  const startScale = queueItemWidth / displayWidth;

  // End position fine-tuning to match exactly where active receipt appears
  // The active receipt is centered in receiptImageWrapper using flexbox
  // Adjust these if the landing position doesn't match perfectly
  const endX = 0;
  const endY = 0;

  const { x, y, scale, rotate } = useSpring({
    from: {
      x: startX,
      y: startY,
      scale: startScale,
      rotate: rotation,
    },
    to: {
      x: endX,
      y: endY,
      scale: 1,
      rotate: 0,
    },
    config: { tension: 120, friction: 18 },
  });

  // Early return after all hooks
  if (!receipt || !imageUrl || !isFlying) return null;

  // Account for the 1px border on each side when centering
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
        style={{
          width: displayWidth,
          height: displayHeight,
        }}
      />
    </animated.div>
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
      {ENTITY_TYPES.map((entityType) => (
        <LegendItem
          key={entityType}
          entityType={entityType}
          isRevealed={revealedEntityTypes.has(entityType)}
        />
      ))}
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
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
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

const LayoutLMBatchVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  // Receipt queue - continuously grows as we fetch more
  const [receipts, setReceipts] = useState<LayoutLMReceiptInference[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [currentReceiptIndex, setCurrentReceiptIndex] = useState(0);
  const [scanProgress, setScanProgress] = useState(0);
  const [revealedEntityTypes, setRevealedEntityTypes] = useState<Set<string>>(new Set());
  const [showInferenceTime, setShowInferenceTime] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);
  const [isPoolExhausted, setIsPoolExhausted] = useState(false);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const isFetchingRef = useRef(false);
  const seenReceiptIds = useRef<Set<string>>(new Set());
  const emptyFetchCountRef = useRef(0);
  const isPoolExhaustedRef = useRef(isPoolExhausted);

  // Keep ref in sync with state for animation loop access
  useEffect(() => {
    isPoolExhaustedRef.current = isPoolExhausted;
  }, [isPoolExhausted]);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

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

  // Check if we need to fetch more receipts (when queue is getting low)
  // Skip fetching if pool is exhausted (we'll loop instead)
  const remainingReceipts = receipts.length - currentReceiptIndex;
  useEffect(() => {
    if (remainingReceipts < QUEUE_REFETCH_THRESHOLD && !isFetchingRef.current && !initialLoading && !isPoolExhausted) {
      fetchMoreReceipts();
    }
  }, [remainingReceipts, fetchMoreReceipts, initialLoading, isPoolExhausted]);

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

  useEffect(() => {
    if (!inView || receipts.length === 0) {
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
  }, [inView, receipts.length > 0]); // Only restart when receipts become available

  if (initialLoading) {
    return (
      <div ref={ref} className={styles.loading}>
        Loading inference data...
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
        No inference data available
      </div>
    );
  }

  const currentReceipt = receipts[currentReceiptIndex];
  // Get next receipt - use modulo when pool is exhausted for looping
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
        />

        <div className={styles.centerColumn}>
          {/* Current receipt - fades out during transition */}
          <div className={`${styles.receiptContainer} ${isTransitioning ? styles.fadeOut : ''}`}>
            <ActiveReceiptViewer
              receipt={currentReceipt}
              scanProgress={scanProgress}
              revealedWordIds={revealedWordIds}
              formatSupport={formatSupport}
            />
          </div>

          {/* Flying receipt for desktop transition */}
          <div className={styles.flyingReceiptContainer}>
            {isTransitioning && nextReceipt && (
              <FlyingReceipt
                key={`flying-${nextReceipt.receipt_id}`}
                receipt={nextReceipt}
                formatSupport={formatSupport}
                isFlying={isTransitioning}
              />
            )}
          </div>

          {/* Next receipt for mobile crossfade - fades in during transition */}
          {isTransitioning && nextReceipt && (
            <div className={`${styles.receiptContainer} ${styles.nextReceipt} ${styles.fadeIn}`}>
              <ActiveReceiptViewer
                receipt={nextReceipt}
                scanProgress={0}
                revealedWordIds={new Set()}
                formatSupport={formatSupport}
              />
            </div>
          )}
        </div>

        <EntityLegend
          revealedEntityTypes={revealedEntityTypes}
          inferenceTimeMs={currentReceipt.inference_time_ms}
          showInferenceTime={showInferenceTime}
        />
      </div>
    </div>
  );
};

export default LayoutLMBatchVisualization;
