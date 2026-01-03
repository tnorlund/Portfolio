import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { animated, useSpring, to } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import Image from "next/image";
import { api } from "../../../../services/api";
import {
  LayoutLMBatchInferenceResponse,
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
}) => {
  // Show remaining receipts (after current one)
  const remainingReceipts = receipts.slice(currentIndex + 1);
  const maxVisible = 4;
  const visibleReceipts = remainingReceipts.slice(0, maxVisible);

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

        return (
          <div
            key={receipt.receipt_id}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            style={{
              top: `${stackOffset}px`,
              left: `${10 + leftOffset}px`,
              transform: `rotate(${rotation}deg)`,
              zIndex,
              opacity: isFlying ? 0 : 1 - Math.max(0, adjustedIdx) * 0.12,
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
  if (!receipt) return null;

  const { width, height } = receipt.original.receipt;
  const { rotation } = getQueuePosition(receipt.receipt_id);

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt.original.receipt, formatSupport);
  }, [receipt, formatSupport]);

  // Queue item is 100px wide, center is ~300px, so scale is 100/300 = 0.33
  // Queue is positioned about 200px to the left of center
  const { x, scale, rotate } = useSpring({
    from: {
      x: -200,
      scale: 0.33,
      rotate: rotation,
    },
    to: {
      x: 0,
      scale: 1,
      rotate: 0,
    },
    config: { tension: 120, friction: 18 },
  });

  if (!imageUrl || !isFlying) return null;

  // Calculate the display dimensions - max height 500px, maintain aspect ratio
  const aspectRatio = width / height;
  const displayHeight = Math.min(500, height);
  const displayWidth = displayHeight * aspectRatio;

  return (
    <animated.div
      className={styles.flyingReceipt}
      style={{
        transform: to(
          [x, scale, rotate],
          (xVal, scaleVal, rotateVal) =>
            `translateX(${xVal}px) scale(${scaleVal}) rotate(${rotateVal}deg)`
        ),
        marginLeft: -displayWidth / 2,
        marginTop: -displayHeight / 2,
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

interface ProgressDotsProps {
  total: number;
  current: number;
  receipts: LayoutLMReceiptInference[];
}

const ProgressDots: React.FC<ProgressDotsProps> = ({ total, current, receipts }) => {
  return (
    <div className={styles.progressBar}>
      {Array.from({ length: total }).map((_, idx) => {
        const isActive = idx === current;
        const isCompleted = idx < current;
        const accuracy = receipts[idx]?.metrics?.overall_accuracy;

        return (
          <div
            key={idx}
            className={`${styles.progressDot} ${isActive ? styles.active : ""} ${
              isCompleted ? styles.completed : ""
            }`}
            title={
              accuracy !== undefined
                ? `Receipt ${idx + 1}: ${(accuracy * 100).toFixed(1)}% accuracy`
                : `Receipt ${idx + 1}`
            }
          >
            <span className={styles.dotNumber}>{idx + 1}</span>
            {isCompleted && <span className={styles.checkmark}>✓</span>}
          </div>
        );
      })}
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

  // Calculate display dimensions - max height 500px, maintain aspect ratio
  const maxHeight = 500;
  const aspectRatio = receiptData.width / receiptData.height;
  const displayHeight = Math.min(maxHeight, receiptData.height);
  const displayWidth = displayHeight * aspectRatio;

  return (
    <div className={styles.activeReceipt}>
      <div className={styles.receiptImageWrapper}>
        <div
          className={styles.receiptImageInner}
          style={{ width: displayWidth, height: displayHeight }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="Receipt"
            style={{ width: displayWidth, height: displayHeight, display: "block" }}
          />

          {/* SVG overlay for bounding boxes and scan line */}
          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${receiptData.width} ${receiptData.height}`}
            preserveAspectRatio="xMidYMid meet"
          >
            {/* Scan line - positioned in image coordinates */}
            <defs>
              <linearGradient id="scanLineGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="transparent" />
                <stop offset="20%" stopColor="var(--color-red)" />
                <stop offset="80%" stopColor="var(--color-red)" />
                <stop offset="100%" stopColor="transparent" />
              </linearGradient>
              <filter id="scanLineGlow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="4" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            <rect
              x="0"
              y={(scanProgress / 100) * receiptData.height}
              width={receiptData.width}
              height={Math.max(receiptData.height * 0.005, 3)}
              fill="url(#scanLineGradient)"
              filter="url(#scanLineGlow)"
            />

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

const LayoutLMBatchVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });

  const [data, setData] = useState<LayoutLMBatchInferenceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentReceiptIndex, setCurrentReceiptIndex] = useState(0);
  const [scanProgress, setScanProgress] = useState(0);
  const [revealedEntityTypes, setRevealedEntityTypes] = useState<Set<string>>(new Set());
  const [showInferenceTime, setShowInferenceTime] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch data function
  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const response = await api.fetchLayoutLMInference();
      setData(response);
      setCurrentReceiptIndex(0);
      setScanProgress(0);
      setRevealedEntityTypes(new Set());
      setShowInferenceTime(false);
      setIsTransitioning(false);
      setError(null);
    } catch (err) {
      console.error("Failed to fetch LayoutLM inference:", err);
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Calculate revealed word IDs based on scan progress
  const revealedWordIds = useMemo(() => {
    if (!data || !data.receipts[currentReceiptIndex]) {
      return new Set<string>();
    }

    const receipt = data.receipts[currentReceiptIndex];
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
  }, [data, currentReceiptIndex, scanProgress]);

  // Update revealed entity types based on scan progress
  const updateRevealedEntityTypes = useCallback(() => {
    if (!data || !data.receipts[currentReceiptIndex]) return;

    const receipt = data.receipts[currentReceiptIndex];
    const { words, predictions } = receipt.original;
    const scanY = scanProgress / 100;

    const newRevealed = new Set<string>();

    for (const pred of predictions) {
      if (pred.predicted_label_base === "O") continue;

      // Find the corresponding word
      const word = words.find(
        (w) => w.line_id === pred.line_id && w.word_id === pred.word_id
      );
      if (!word) continue;

      // Check if word is revealed by scan
      const wordTopY = 1 - word.bounding_box.y - word.bounding_box.height;
      if (wordTopY <= scanY) {
        newRevealed.add(pred.predicted_label_base);
      }
    }

    setRevealedEntityTypes(newRevealed);
  }, [data, currentReceiptIndex, scanProgress]);

  // Update revealed entities when scan progress changes
  useEffect(() => {
    updateRevealedEntityTypes();
  }, [scanProgress, updateRevealedEntityTypes]);

  // Animation loop - uses actual inference time per receipt
  useEffect(() => {
    if (!inView || !data || data.receipts.length === 0) {
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
      if (!data || data.receipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }

      const elapsed = currentTime - startTime;
      const currentReceipt = data.receipts[receiptIndex];

      if (!currentReceipt) {
        isAnimatingRef.current = false;
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
        const nextIndex = receiptIndex + 1;
        if (nextIndex >= data.receipts.length) {
          // All receipts processed - re-fetch from API
          isAnimatingRef.current = false;
          fetchData();
          return;
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
  }, [inView, data, fetchData]);

  if (loading) {
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

  if (!data || data.receipts.length === 0) {
    return (
      <div ref={ref} className={styles.loading}>
        No inference data available
      </div>
    );
  }

  const currentReceipt = data.receipts[currentReceiptIndex];
  const nextReceipt = data.receipts[currentReceiptIndex + 1];

  return (
    <div ref={ref} className={styles.container}>
      <ProgressDots
        total={data.receipts.length}
        current={currentReceiptIndex}
        receipts={data.receipts}
      />

      <div className={styles.mainWrapper}>
        <ReceiptQueue
          receipts={data.receipts}
          currentIndex={currentReceiptIndex}
          formatSupport={formatSupport}
          isTransitioning={isTransitioning}
        />

        <div className={styles.centerColumn}>
          {/* Show active receipt when not transitioning */}
          {!isTransitioning && (
            <ActiveReceiptViewer
              receipt={currentReceipt}
              scanProgress={scanProgress}
              revealedWordIds={revealedWordIds}
              formatSupport={formatSupport}
            />
          )}

          {/* Show flying receipt during transition */}
          {isTransitioning && nextReceipt && (
            <FlyingReceipt
              key={`flying-${nextReceipt.receipt_id}`}
              receipt={nextReceipt}
              formatSupport={formatSupport}
              isFlying={isTransitioning}
            />
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
