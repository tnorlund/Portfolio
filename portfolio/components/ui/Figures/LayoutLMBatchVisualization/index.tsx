import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { animated, useSpring, useTransition, config } from "@react-spring/web";
import { useInView } from "react-intersection-observer";
import Image from "next/image";
import { api } from "../../../../services/api";
import {
  LayoutLMBatchInferenceResponse,
  LayoutLMReceiptInference,
  LayoutLMPrediction,
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

// Animation timing (in ms)
const ENTITY_REVEAL_DELAY = 150;
const TRANSITION_DURATION = 1000;

interface EntityCardProps {
  label: string;
  value: string | null;
  isRevealed: boolean;
}

const EntityCard: React.FC<EntityCardProps> = ({ label, value, isRevealed }) => {
  const spring = useSpring({
    opacity: isRevealed ? 1 : 0,
    transform: isRevealed ? "translateX(0px)" : "translateX(20px)",
    config: { tension: 280, friction: 24 },
  });

  const labelKey = label.toUpperCase().replace(" ", "_");
  const colorVar = LABEL_COLORS[labelKey] || LABEL_COLORS.O;

  return (
    <animated.div
      className={styles.entityCard}
      style={{
        ...spring,
        borderLeftColor: colorVar,
      }}
    >
      <span className={styles.entityLabel}>{label}</span>
      <span className={styles.entityValue}>{value || "---"}</span>
    </animated.div>
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

interface InferenceTimeProps {
  inferenceTimeMs: number;
  show: boolean;
}

const InferenceTime: React.FC<InferenceTimeProps> = ({ inferenceTimeMs, show }) => {
  const spring = useSpring({
    opacity: show ? 1 : 0,
    transform: show ? "translateY(0px)" : "translateY(10px)",
    config: { tension: 280, friction: 24 },
  });

  return (
    <animated.div className={styles.inferenceTime} style={spring}>
      <span className={styles.inferenceLabel}>Inference Time</span>
      <span className={styles.inferenceValue}>{inferenceTimeMs.toFixed(0)}ms</span>
    </animated.div>
  );
};

interface ReceiptViewerProps {
  receipt: LayoutLMReceiptInference;
  scanProgress: number;
  revealedWordIds: Set<string>;
  formatSupport: { supportsWebP: boolean; supportsAVIF: boolean } | null;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({
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

    // Pass receiptData directly - it has cdn_s3_key, cdn_webp_s3_key, cdn_avif_s3_key
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
    <div className={styles.receiptViewer}>
      <div className={styles.receiptImageWrapper}>
        <div className={styles.receiptImageInner}>
          <Image
            src={imageUrl}
            alt="Receipt"
            fill
            style={{ objectFit: "contain", objectPosition: "center" }}
            priority
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
    triggerOnce: true,
  });

  const [data, setData] = useState<LayoutLMBatchInferenceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentReceiptIndex, setCurrentReceiptIndex] = useState(0);
  const [scanProgress, setScanProgress] = useState(0);
  const [revealedEntities, setRevealedEntities] = useState<string[]>([]);
  const [showInferenceTime, setShowInferenceTime] = useState(false);
  const [formatSupport, setFormatSupport] = useState<{
    supportsWebP: boolean;
    supportsAVIF: boolean;
  } | null>(null);

  const hasStartedAnimation = useRef(false);
  const animationRef = useRef<number | null>(null);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch data
  useEffect(() => {
    api
      .fetchLayoutLMInference()
      .then((response) => {
        setData(response);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to fetch LayoutLM inference:", err);
        setError(err.message);
        setLoading(false);
      });
  }, []);

  // Calculate revealed word IDs based on scan progress
  const revealedWordIds = useMemo(() => {
    if (!data || !data.receipts[currentReceiptIndex]) {
      return new Set<string>();
    }

    const receipt = data.receipts[currentReceiptIndex];
    const { words, predictions } = receipt.original;
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

  // Animation loop - uses actual inference time per receipt
  useEffect(() => {
    if (!inView || !data || data.receipts.length === 0 || hasStartedAnimation.current) {
      return;
    }

    hasStartedAnimation.current = true;
    let receiptIndex = 0;
    let startTime = performance.now();

    const animate = (currentTime: number) => {
      const elapsed = currentTime - startTime;
      const currentReceipt = data.receipts[receiptIndex];
      const scanDuration = currentReceipt.inference_time_ms;
      const totalDuration = scanDuration + 4 * ENTITY_REVEAL_DELAY + TRANSITION_DURATION;

      if (elapsed < scanDuration) {
        // Scanning phase - duration matches actual inference time
        const progress = (elapsed / scanDuration) * 100;
        setScanProgress(Math.min(progress, 100));
        setRevealedEntities([]);
        setShowInferenceTime(false);
      } else if (elapsed < scanDuration + 4 * ENTITY_REVEAL_DELAY) {
        // Entity reveal phase
        setScanProgress(100);
        const entityElapsed = elapsed - scanDuration;
        const entityCount = Math.floor(entityElapsed / ENTITY_REVEAL_DELAY) + 1;
        const entities = ["MERCHANT_NAME", "DATE", "ADDRESS", "AMOUNT"].slice(
          0,
          Math.min(entityCount, 4)
        );
        setRevealedEntities(entities);
        setShowInferenceTime(true);
      } else if (elapsed < totalDuration) {
        // Hold phase
        setScanProgress(100);
        setRevealedEntities(["MERCHANT_NAME", "DATE", "ADDRESS", "AMOUNT"]);
        setShowInferenceTime(true);
      } else {
        // Move to next receipt
        receiptIndex = (receiptIndex + 1) % data.receipts.length;
        setCurrentReceiptIndex(receiptIndex);
        setScanProgress(0);
        setRevealedEntities([]);
        setShowInferenceTime(false);
        startTime = currentTime;
      }

      animationRef.current = requestAnimationFrame(animate);
    };

    animationRef.current = requestAnimationFrame(animate);

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [inView, data]);

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
  const { entities_summary } = currentReceipt;

  return (
    <div ref={ref} className={styles.container}>
      <ProgressDots
        total={data.receipts.length}
        current={currentReceiptIndex}
        receipts={data.receipts}
      />

      <div className={styles.mainWrapper}>
        <ReceiptViewer
          receipt={currentReceipt}
          scanProgress={scanProgress}
          revealedWordIds={revealedWordIds}
          formatSupport={formatSupport}
        />

        <div className={styles.entitySidebar}>
          <EntityCard
            label="Merchant"
            value={entities_summary.merchant_name}
            isRevealed={revealedEntities.includes("MERCHANT_NAME")}
          />
          <EntityCard
            label="Date"
            value={entities_summary.date}
            isRevealed={revealedEntities.includes("DATE")}
          />
          <EntityCard
            label="Address"
            value={entities_summary.address}
            isRevealed={revealedEntities.includes("ADDRESS")}
          />
          <EntityCard
            label="Amount"
            value={entities_summary.amount}
            isRevealed={revealedEntities.includes("AMOUNT")}
          />
        </div>
      </div>

      <InferenceTime
        inferenceTimeMs={currentReceipt.inference_time_ms}
        show={showInferenceTime}
      />
    </div>
  );
};

export default LayoutLMBatchVisualization;
