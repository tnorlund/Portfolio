import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { animated, useSpring } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import Image from "next/image";
import { api } from "../../../../services/api";
import {
  LayoutLMBatchInferenceResponse,
  LayoutLMReceiptInference,
  LayoutLMReceiptWord,
} from "../../../../types/api";
import { getBestImageUrl, usePreloadReceiptImages } from "../../../../utils/imageFormat";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import {
  DEFAULT_LAYOUT_VARS,
  ReceiptFlowLoadingShell,
} from "../ReceiptFlow/ReceiptFlowLoadingShell";
import {
  getQueuePosition,
  getReceiptMotionScale,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import { ImageFormatSupport } from "../ReceiptFlow/types";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
import styles from "./LayoutLMBatchVisualization.module.css";
import { CHARGE_GREEN, LABEL_COLORS } from "../labelStyles";
import { LabelBoxOverlay } from "../labelBoxOverlay";
import sharedStyles from "../labelBoxOverlay.module.css";

const emptyStringSet = new Set<string>();

// Normalize ADDRESS_LINE to ADDRESS for display purposes
const normalizeLabel = (label: string): string => {
  // Keep granular labels as-is (LABEL_COLORS covers both ADDRESS and
  // ADDRESS_LINE, the currency roles, QUANTITY, etc.).
  return label;
};

// Legend groups — taxonomy families. `title` (hover) carries the breakdown so
// the labels stay short.
const MOBILE_LEGEND_GROUPS = [
  { color: "var(--color-yellow)", label: "Merchant", title: "Merchant name", types: ["MERCHANT_NAME"] },
  { color: "var(--color-blue)", label: "Date / Time", title: "Date · Time", types: ["DATE", "TIME"] },
  {
    color: CHARGE_GREEN,
    label: "Charges",
    title: "Total · Subtotal · Tax · Line total · Unit price",
    types: ["GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL", "UNIT_PRICE", "AMOUNT"],
  },
  {
    color: "var(--color-teal)",
    label: "Credits",
    title: "Discount · Coupon · Tip · Change · Cash back · Refund",
    types: ["DISCOUNT", "COUPON", "TIP", "CHANGE", "CASH_BACK", "REFUND"],
  },
  { color: "var(--color-cyan)", label: "Quantity", title: "Quantity", types: ["QUANTITY"] },
  { color: "var(--color-red)", label: "Address", title: "Address line", types: ["ADDRESS", "ADDRESS_LINE"] },
  { color: "var(--color-pink)", label: "Phone", title: "Phone number", types: ["PHONE_NUMBER"] },
  { color: "var(--color-purple)", label: "Website", title: "Website", types: ["WEBSITE"] },
  { color: "var(--color-orange)", label: "Hours / Pay", title: "Store hours · Payment method", types: ["STORE_HOURS", "PAYMENT_METHOD"] },
];

// Animation timing
const HOLD_DURATION = 1000;
const TRANSITION_DURATION = 600;

const LAYOUT_VARS = {
  ...DEFAULT_LAYOUT_VARS,
  "--rf-align-items": "center",
} as React.CSSProperties;

const makeFallbackBox = (
  top: number,
  left: number,
  width: number,
  height: number,
) => ({
  x: left,
  y: 1 - top - height,
  width,
  height,
});

const FALLBACK_WORD_SPECS = [
  ["SPROUTS", "MERCHANT_NAME", 0.055, 0.31, 0.19, 0.016],
  ["FARMERS", "MERCHANT_NAME", 0.074, 0.29, 0.18, 0.014],
  ["MARKET", "MERCHANT_NAME", 0.091, 0.32, 0.15, 0.014],
  ["123", "ADDRESS_LINE", 0.136, 0.2, 0.08, 0.012],
  ["MAIN", "ADDRESS_LINE", 0.136, 0.29, 0.11, 0.012],
  ["ST", "ADDRESS_LINE", 0.136, 0.41, 0.05, 0.012],
  ["07/06/26", "DATE", 0.172, 0.2, 0.18, 0.014],
  ["10:34", "TIME", 0.172, 0.55, 0.1, 0.014],
  ["ORGANIC", "O", 0.39, 0.14, 0.17, 0.013],
  ["BANANAS", "O", 0.39, 0.33, 0.16, 0.013],
  ["1.31", "QUANTITY", 0.39, 0.55, 0.08, 0.013],
  ["$1.29", "UNIT_PRICE", 0.39, 0.67, 0.09, 0.013],
  ["$1.69", "LINE_TOTAL", 0.39, 0.8, 0.09, 0.013],
  ["SUBTOTAL", "SUBTOTAL", 0.76, 0.55, 0.16, 0.013],
  ["$28.43", "SUBTOTAL", 0.76, 0.78, 0.11, 0.013],
  ["TAX", "TAX", 0.792, 0.62, 0.07, 0.013],
  ["$1.18", "TAX", 0.792, 0.79, 0.1, 0.013],
  ["TOTAL", "GRAND_TOTAL", 0.824, 0.56, 0.12, 0.015],
  ["$29.61", "GRAND_TOTAL", 0.824, 0.76, 0.13, 0.015],
  ["VISA", "PAYMENT_METHOD", 0.875, 0.17, 0.09, 0.013],
] as const;

const FALLBACK_RECEIPT_IMAGES = [
  {
    imageId: "0fca7cfd-183e-4109-87a9-2b7b7b94e82d",
    receiptId: 2,
    width: 830,
    height: 2827,
    jpg: "assets/0fca7cfd-183e-4109-87a9-2b7b7b94e82d_RECEIPT_00002.jpg",
    webp: "assets/0fca7cfd-183e-4109-87a9-2b7b7b94e82d_RECEIPT_00002.webp",
  },
  {
    imageId: "c914012e-3fc3-4ba2-b314-fa7d423acac8",
    receiptId: 2,
    width: 871,
    height: 2914,
    jpg: "assets/c914012e-3fc3-4ba2-b314-fa7d423acac8_RECEIPT_00002.jpg",
    webp: "assets/c914012e-3fc3-4ba2-b314-fa7d423acac8_RECEIPT_00002.webp",
  },
  {
    imageId: "00ded398-af6f-4a49-86f7-c79ccb554e48",
    receiptId: 2,
    width: 792,
    height: 2575,
    jpg: "assets/00ded398-af6f-4a49-86f7-c79ccb554e48_RECEIPT_00002.jpg",
    webp: "assets/00ded398-af6f-4a49-86f7-c79ccb554e48_RECEIPT_00002.webp",
  },
];

const makeFallbackReceipt = (
  image: (typeof FALLBACK_RECEIPT_IMAGES)[number],
  index: number,
): LayoutLMReceiptInference => {
  const receiptNumber = index + 1;
  const words = FALLBACK_WORD_SPECS.map(
    ([text, _label, top, left, width, height], wordId): LayoutLMReceiptWord => ({
      receipt_id: image.receiptId,
      line_id: Math.floor(wordId / 3),
      word_id: wordId,
      text,
      bounding_box: makeFallbackBox(top, left, width, height),
    })
  );

  const predictions = FALLBACK_WORD_SPECS.map(
    ([text, label], wordId) => ({
      word_id: wordId,
      line_id: Math.floor(wordId / 3),
      text,
      predicted_label: label === "O" ? "O" : `B-${label}`,
      predicted_label_base: label,
      ground_truth_label: null,
      ground_truth_label_base: null,
      predicted_confidence: 0.92,
      is_correct: true,
    })
  );

  return {
    receipt_id: `${image.imageId}-${image.receiptId}`,
    original: {
      receipt: {
        image_id: image.imageId,
        receipt_id: image.receiptId,
        width: image.width,
        height: image.height,
        cdn_s3_bucket: "portfolio-cdn",
        cdn_s3_key: image.jpg,
        cdn_webp_s3_key: image.webp,
        cdn_medium_s3_key: image.jpg,
        cdn_medium_webp_s3_key: image.webp,
      },
      words,
      predictions,
    },
    metrics: {
      overall_accuracy: 0.94,
      total_words: words.length,
      correct_predictions: words.length,
    },
    model_info: {
      model_name: "layoutlm-fallback",
      device: "browser-fixture",
      s3_uri: "local-fallback",
    },
    entities_summary: {
      merchant_name: "Sprouts Farmers Market",
      date: "07/06/26",
      address: "123 Main St",
      amount: "$29.61",
    },
    inference_time_ms: 180 + receiptNumber * 35,
    cached_at: "2026-07-07T00:00:00Z",
  };
};

const FALLBACK_LAYOUTLM_RESPONSE: LayoutLMBatchInferenceResponse = {
  receipts: FALLBACK_RECEIPT_IMAGES.map(makeFallbackReceipt),
  aggregate_stats: {
    avg_accuracy: 0.94,
    min_accuracy: 0.92,
    max_accuracy: 0.96,
    avg_inference_time_ms: 235,
    total_receipts_in_pool: FALLBACK_RECEIPT_IMAGES.length,
    batch_size: FALLBACK_RECEIPT_IMAGES.length,
    total_words_processed: FALLBACK_WORD_SPECS.length * FALLBACK_RECEIPT_IMAGES.length,
    estimated_throughput_per_hour: 46000,
  },
  fetched_at: "2026-07-07T00:00:00Z",
};

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
  const motionScale = getReceiptMotionScale();
  const dropDuration = 0.6 * motionScale;
  const flyOpacityDuration = 0.25 * motionScale;
  const settleDuration = 0.4 * motionScale;

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
            // Used by FlyingReceipt.computeFrom to launch the flight from this
            // exact card rather than always the top of the stack.
            data-rf-card-id={receipt.receipt_id}
            style={{
              top: `${stackOffset}px`,
              left: `${10 + leftOffset}px`,
              transform: `rotate(${rotation}deg) translateY(${showItem ? 0 : -50}px)`,
              // Hide the top card while it's flying to center (inline opacity
              // wins over the .flyingOut class, so it must be handled here) so
              // it doesn't sit in the stack as a duplicate of the flying copy.
              opacity: isFlying ? 0 : showItem ? 1 : 0,
              zIndex,
              transition: `transform ${dropDuration}s ease-out ${shouldAnimate ? idx * fadeDelay * motionScale : 0}ms, opacity ${isFlying ? `${flyOpacityDuration}s` : `${dropDuration}s`} ease-out ${isFlying ? 0 : shouldAnimate ? idx * fadeDelay * motionScale : 0}ms, top ${settleDuration}s ease, left ${settleDuration}s ease`,
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
      {/* Desktop: taxonomy-family legend (hidden on mobile via CSS) — 9 groups
          instead of one row per granular label. */}
      <div className={styles.legendDesktop}>
        {MOBILE_LEGEND_GROUPS.map((group) => {
          const isRevealed = group.types.some((t) =>
            revealedEntityTypes.has(t)
          );
          return (
            <div
              key={group.label}
              title={group.title}
              className={`${styles.legendItem} ${isRevealed ? styles.revealed : ""}`}
            >
              <div
                className={styles.legendDot}
                style={{ backgroundColor: group.color }}
              />
              <span className={styles.legendLabel}>{group.label}</span>
            </div>
          );
        })}
      </div>
      {/* Mobile: grouped legend (hidden on desktop via CSS) */}
      <div className={styles.legendMobile}>
        {MOBILE_LEGEND_GROUPS.map((group) => {
          const isRevealed = group.types.some((t) => revealedEntityTypes.has(t));
          return (
            <div
              key={group.label}
              title={group.title}
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
  revealedWordIds: Set<string>;
  formatSupport: ImageFormatSupport | null;
}

const ActiveReceiptViewer: React.FC<ActiveReceiptViewerProps> = ({
  receipt,
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
      if (normalizeLabel(pred.predicted_label_base) === "O") return false;
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
        <div className={`${styles.receiptImageInner} ${sharedStyles.receiptCard}`}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt="Receipt"
            width={receiptData.width}
            height={receiptData.height}
            className={styles.receiptImage}
          />

          {/* SVG overlay for the labeled bounding boxes. The old red scan
              line was removed — inference is now ~15ms, so labels appear
              effectively instantly. Shared with SynthesisPipeline via
              LabelBoxOverlay so the two figures can never drift. */}
          <LabelBoxOverlay
            className={styles.svgOverlay}
            width={receiptData.width}
            height={receiptData.height}
            boxes={visiblePredictions.flatMap((pred, idx) => {
              const key = `${pred.line_id}_${pred.word_id}`;
              const word = wordLookup.get(key);
              if (!word) return [];

              const { bounding_box } = word;
              const color =
                LABEL_COLORS[normalizeLabel(pred.predicted_label_base)] ||
                LABEL_COLORS.O;

              // Convert normalized bounding box to pixel coordinates
              return [
                {
                  key: `${key}-${idx}`,
                  x: bounding_box.x * receiptData.width,
                  y:
                    (1 - bounding_box.y - bounding_box.height) *
                    receiptData.height,
                  width: bounding_box.width * receiptData.width,
                  height: bounding_box.height * receiptData.height,
                  color,
                },
              ];
            })}
          />
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

  // Build prediction lookup once per receipt (avoids rebuilding on every scanProgress change)
  const predMap = useMemo(() => {
    const receipt = receipts[currentReceiptIndex];
    if (!receipt) return new Map<string, string>();
    const map = new Map<string, string>();
    for (const pred of receipt.original.predictions) {
      const label = normalizeLabel(pred.predicted_label_base);
      if (label !== "O") {
        map.set(`${pred.line_id}_${pred.word_id}`, label);
      }
    }
    return map;
  }, [receipts, currentReceiptIndex]);

  // Calculate revealed word IDs and entity types in a single pass
  const { revealedWordIds, revealedEntityTypes } = useMemo(() => {
    const receipt = receipts[currentReceiptIndex];
    if (!receipt) {
      return { revealedWordIds: emptyStringSet, revealedEntityTypes: emptyStringSet };
    }

    const { words } = receipt.original;
    const scanY = scanProgress / 100;
    const wordIds = new Set<string>();
    const entityTypes = new Set<string>();

    for (const word of words) {
      const wordTopY = 1 - word.bounding_box.y - word.bounding_box.height;
      if (wordTopY <= scanY) {
        const key = `${word.line_id}_${word.word_id}`;
        wordIds.add(key);
        const label = predMap.get(key);
        if (label) entityTypes.add(label);
      }
    }

    return { revealedWordIds: wordIds, revealedEntityTypes: entityTypes };
  }, [receipts, currentReceiptIndex, scanProgress, predMap]);

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

      const motionScale = getReceiptMotionScale();
      const scanDuration = currentReceipt.inference_time_ms * motionScale;
      const holdDuration = HOLD_DURATION * motionScale;
      const transitionDuration = TRANSITION_DURATION * motionScale;
      const totalDuration = scanDuration + holdDuration + transitionDuration;

      if (elapsed < scanDuration) {
        // SCAN PHASE
        const progress = (elapsed / scanDuration) * 100;
        setScanProgress(Math.min(progress, 100));
        setShowInferenceTime(false);
        setIsTransitioning(false);
      } else if (elapsed < scanDuration + holdDuration) {
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
    // The loop owns receiptIndex after startup; restarting on every index
    // change would interrupt the receipt and legend handoff.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const motionScale = getReceiptMotionScale();
  const layoutVars = useMemo(
    () => ({
      ...LAYOUT_VARS,
      "--rf-motion-scale": motionScale,
    } as React.CSSProperties),
    [motionScale],
  );

  const nextLegend = isTransitioning && nextReceipt ? (
    <EntityLegend
      revealedEntityTypes={emptyStringSet}
      inferenceTimeMs={nextReceipt.inference_time_ms}
      showInferenceTime={false}
    />
  ) : null;

  return (
    <div ref={observerRef} className={styles.container}>
      <ReceiptFlowShell
        layoutVars={layoutVars}
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
            revealedWordIds={revealedWordIds}
            formatSupport={formatSupport}
          />
        }
        flying={flyingElement}
        next={
          isTransitioning && nextReceipt ? (
            <ActiveReceiptViewer
              receipt={nextReceipt}
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
        nextLegend={nextLegend}
        stabilizeLegend
      />
    </div>
  );
};

// Outer component - handles data fetching and loading guards
const LayoutLMBatchVisualization: React.FC = () => {
  const { ref: lazyRef, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "200px",
  });
  const { ref: animRef, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });
  const setRefs = useCallback(
    (node?: Element | null) => {
      lazyRef(node);
      animRef(node);
    },
    [lazyRef, animRef],
  );

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
  const hasInitialFetchedRef = useRef(false);

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

  // Initial fetch only when near viewport - defers work until section is close
  useEffect(() => {
    if (!nearViewport || hasInitialFetchedRef.current) return;
    hasInitialFetchedRef.current = true;

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
        FALLBACK_LAYOUTLM_RESPONSE.receipts.forEach((r) => {
          seenReceiptIds.current.add(r.receipt_id);
        });
        setReceipts(FALLBACK_LAYOUTLM_RESPONSE.receipts);
        setIsPoolExhausted(true);
        setError(null);
      } finally {
        setInitialLoading(false);
      }
    };

    initialFetch();
  }, [nearViewport]);

  if (!nearViewport || initialLoading) {
    return (
      <div ref={setRefs} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="layoutlm"
        />
      </div>
    );
  }

  if (error) {
    return (
      <div ref={setRefs} className={styles.container}>
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
      <div ref={setRefs} className={styles.container}>
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
      observerRef={setRefs}
      inView={inView}
      receipts={receipts}
      formatSupport={formatSupport}
      isPoolExhausted={isPoolExhausted}
      onFetchMore={fetchMoreReceipts}
    />
  );
};

export default LayoutLMBatchVisualization;
