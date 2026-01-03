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
import { detectImageFormatSupport, getBestImageUrl } from "../../../../utils/image";
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
const SCAN_DURATION = 5000;
const ENTITY_REVEAL_DELAY = 150;
const TRANSITION_DURATION = 1000;
const TOTAL_RECEIPT_DURATION = SCAN_DURATION + 4 * ENTITY_REVEAL_DELAY + TRANSITION_DURATION;

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

interface StatsBarProps {
  aggregateStats: LayoutLMBatchInferenceResponse["aggregate_stats"];
  currentReceipt: LayoutLMReceiptInference;
}

const StatsBar: React.FC<StatsBarProps> = ({ aggregateStats, currentReceipt }) => {
  return (
    <div className={styles.statsBar}>
      <div className={styles.statItem}>
        <span className={styles.statLabel}>Accuracy</span>
        <span className={styles.statValue}>
          {(aggregateStats.avg_accuracy * 100).toFixed(1)}%
        </span>
      </div>
      <div className={styles.statItem}>
        <span className={styles.statLabel}>Inference</span>
        <span className={styles.statValue}>
          {currentReceipt.inference_time_ms.toFixed(0)}ms
        </span>
      </div>
      <div className={styles.statItem}>
        <span className={styles.statLabel}>Throughput</span>
        <span className={styles.statValue}>
          ~{aggregateStats.estimated_throughput_per_hour.toLocaleString()}/hr
        </span>
      </div>
      <div className={styles.statItem}>
        <span className={styles.statLabel}>Pool</span>
        <span className={styles.statValue}>
          {aggregateStats.total_receipts_in_pool}
        </span>
      </div>
    </div>
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

    const bucket = receiptData.cdn_s3_bucket;
    const formats = {
      jpeg: receiptData.cdn_s3_key,
      webp: receiptData.cdn_webp_s3_key,
      avif: receiptData.cdn_avif_s3_key,
    };

    return getBestImageUrl(bucket, formats, formatSupport);
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
        <Image
          src={imageUrl}
          alt="Receipt"
          fill
          style={{ objectFit: "contain" }}
          priority
        />

        {/* SVG overlay for bounding boxes */}
        <svg
          className={styles.svgOverlay}
          viewBox={`0 0 ${receiptData.width} ${receiptData.height}`}
          preserveAspectRatio="xMidYMid meet"
        >
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

        {/* Scan line */}
        <div
          className={styles.scanLine}
          style={{ top: `${scanProgress}%` }}
        />
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

  // Animation loop
  useEffect(() => {
    if (!inView || !data || data.receipts.length === 0 || hasStartedAnimation.current) {
      return;
    }

    hasStartedAnimation.current = true;
    let receiptIndex = 0;
    let startTime = performance.now();

    const animate = (currentTime: number) => {
      const elapsed = currentTime - startTime;

      if (elapsed < SCAN_DURATION) {
        // Scanning phase
        const progress = (elapsed / SCAN_DURATION) * 100;
        setScanProgress(Math.min(progress, 100));
        setRevealedEntities([]);
      } else if (elapsed < SCAN_DURATION + 4 * ENTITY_REVEAL_DELAY) {
        // Entity reveal phase
        setScanProgress(100);
        const entityElapsed = elapsed - SCAN_DURATION;
        const entityCount = Math.floor(entityElapsed / ENTITY_REVEAL_DELAY) + 1;
        const entities = ["MERCHANT_NAME", "DATE", "ADDRESS", "AMOUNT"].slice(
          0,
          Math.min(entityCount, 4)
        );
        setRevealedEntities(entities);
      } else if (elapsed < TOTAL_RECEIPT_DURATION) {
        // Hold phase
        setScanProgress(100);
        setRevealedEntities(["MERCHANT_NAME", "DATE", "ADDRESS", "AMOUNT"]);
      } else {
        // Move to next receipt
        receiptIndex = (receiptIndex + 1) % data.receipts.length;
        setCurrentReceiptIndex(receiptIndex);
        setScanProgress(0);
        setRevealedEntities([]);
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

      <StatsBar
        aggregateStats={data.aggregate_stats}
        currentReceipt={currentReceipt}
      />
    </div>
  );
};

export default LayoutLMBatchVisualization;
