import { animated, useTransition } from "@react-spring/web";
import Image from "next/image";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import styles from "../../../styles/LayoutLMInferenceVisualization.module.css";
import { detectImageFormatSupport, FormatSupport, getBestImageUrl } from "../../../utils/imageFormat";

const isDevelopment = process.env.NODE_ENV === "development";

// LayoutLM uses 4 simplified labels (matching SROIE research)
const LAYOUTLM_LABELS = {
  MERCHANT_NAME: {
    name: "Merchant Name",
    color: "var(--color-yellow)",
    description: "Store or business name",
  },
  DATE: {
    name: "Date",
    color: "var(--color-blue)",
    description: "Transaction date and time",
  },
  ADDRESS: {
    name: "Address",
    color: "var(--color-red)",
    description: "Store address and location",
  },
  AMOUNT: {
    name: "Amount",
    color: "var(--color-green)",
    description: "Prices, totals, and currency values",
  },
} as const;

// Helper to extract base label from BIO format (B-MERCHANT_NAME -> MERCHANT_NAME)
const getBaseLabel = (label: string): string => {
  if (label === "O") return "O";
  return label.replace(/^[BI]-/, "");
};

// Get color for a label
const getLabelColor = (label: string): string => {
  const base = getBaseLabel(label);
  return LAYOUTLM_LABELS[base as keyof typeof LAYOUTLM_LABELS]?.color || "var(--color-blue)";
};

// Format confidence as percentage
const formatConfidence = (conf: number): string => {
  return `${(conf * 100).toFixed(1)}%`;
};

// Get confidence color (green = high, yellow = medium, red = low)
const getConfidenceColor = (conf: number): string => {
  if (conf >= 0.8) return "var(--color-green)";
  if (conf >= 0.6) return "var(--color-yellow)";
  return "var(--color-red)";
};

interface LayoutLMInferenceResponse {
  original: {
    receipt: {
      image_id: string;
      receipt_id: number;
      width: number;
      height: number;
      cdn_s3_bucket: string;
      cdn_s3_key: string;
      cdn_webp_s3_key?: string;
      cdn_avif_s3_key?: string;
    };
    words: Array<{
      receipt_id: number;
      line_id: number;
      word_id: number;
      text: string;
      bounding_box: {
        x: number;
        y: number;
        width: number;
        height: number;
      };
    }>;
    predictions: Array<{
      word_id: number;
      line_id: number;
      text: string;
      predicted_label: string;
      predicted_confidence: number;
      ground_truth_label: string | null;
      is_correct: boolean;
    }>;
    line_predictions: Array<{
      line_id: number;
      tokens: string[];
      predicted_labels: string[];
      confidences: number[];
      ground_truth_labels: string[] | null;
    }>;
  };
  metrics: {
    overall_accuracy: number;
    per_label_f1: Record<string, number>;
    per_label_precision: Record<string, number>;
    per_label_recall: Record<string, number>;
    total_words: number;
    correct_predictions: number;
  };
  model_info: {
    model_name: string;
    device: string;
    s3_uri: string;
  };
  cached_at: string;
}

const LayoutLMInferenceVisualization: React.FC = () => {
  const [data, setData] = useState<LayoutLMInferenceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);
  const [showConfidence, setShowConfidence] = useState(true);
  const [showMetrics, setShowMetrics] = useState(true);
  const [ref, inView] = useOptimizedInView({ threshold: 0.1 });

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch LayoutLM inference data
  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const apiUrl = isDevelopment
        ? "https://dev-api.tylernorlund.com"
        : "https://api.tylernorlund.com";
      const response = await fetch(`${apiUrl}/layoutlm_inference`, {
        headers: { "Content-Type": "application/json" },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      const result = await response.json();
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load LayoutLM results");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch when component comes into view
  useEffect(() => {
    if (!inView || data) return;
    fetchData();
  }, [inView, data, fetchData]);

  // Create prediction map: (line_id, word_id) -> prediction
  const predictionMap = useMemo(() => {
    if (!data?.original?.predictions) return new Map<string, typeof data.original.predictions[0]>();
    const map = new Map<string, typeof data.original.predictions[0]>();
    data.original.predictions.forEach((pred) => {
      const key = `${pred.line_id}:${pred.word_id}`;
      map.set(key, pred);
    });
    return map;
  }, [data]);

  // Get prediction for a word
  const getPrediction = useCallback(
    (lineId: number, wordId: number) => {
      const key = `${lineId}:${wordId}`;
      return predictionMap.get(key);
    },
    [predictionMap]
  );

  // Get image URL
  const imageUrl = useMemo(() => {
    if (!data?.original?.receipt || !formatSupport) return null;
    const receipt = data.original.receipt;
    return getBestImageUrl(receipt, formatSupport, "full");
  }, [data, formatSupport]);

  // Calculate bounding box coordinates for SVG overlay
  // Note: Receipt coordinates have Y=0 at bottom, SVG has Y=0 at top
  const getBoundingBox = useCallback(
    (word: {
      bounding_box: { x: number; y: number; width: number; height: number };
      top_left?: { x: number; y: number };
      top_right?: { x: number; y: number };
      bottom_left?: { x: number; y: number };
      bottom_right?: { x: number; y: number };
    }) => {
      if (!data?.original?.receipt) return null;
      const receipt = data.original.receipt;
      const svgWidth = receipt.width;
      const svgHeight = receipt.height;

      // If we have corner points, use them (more accurate)
      if (word.top_left && word.top_right && word.bottom_left && word.bottom_right) {
        // Flip Y coordinate: receipt coordinates have Y=0 at bottom, SVG has Y=0 at top
        const x1 = word.top_left.x * svgWidth;
        const y1 = (1 - word.top_left.y) * svgHeight;
        const x2 = word.top_right.x * svgWidth;
        const y2 = (1 - word.top_right.y) * svgHeight;
        const x3 = word.bottom_right.x * svgWidth;
        const y3 = (1 - word.bottom_right.y) * svgHeight;
        const x4 = word.bottom_left.x * svgWidth;
        const y4 = (1 - word.bottom_left.y) * svgHeight;

        // Calculate bounding box from corner points
        const minX = Math.min(x1, x2, x3, x4);
        const maxX = Math.max(x1, x2, x3, x4);
        const minY = Math.min(y1, y2, y3, y4);
        const maxY = Math.max(y1, y2, y3, y4);

        return {
          left: minX,
          top: minY,
          width: maxX - minX,
          height: maxY - minY,
        };
      }

      // Fallback: Use bounding_box (y is bottom-left corner in OCR space)
      const left = word.bounding_box.x * svgWidth;
      const bottom = word.bounding_box.y * svgHeight; // Bottom in OCR space (y=0 at bottom)
      const width = word.bounding_box.width * svgWidth;
      const height = word.bounding_box.height * svgHeight;

      // Convert to SVG coordinates (y=0 at top)
      // In OCR space: y=0 at bottom, so bottom_y = (1 - normalized_y) * height
      // In SVG space: top_y = normalized_y * height
      // So: top_y = (1 - bottom_y_normalized) * height
      const top = (1 - word.bounding_box.y - word.bounding_box.height) * svgHeight;

      return {
        left,
        top,
        width,
        height,
      };
    },
    [data]
  );

  // Filter words that have predictions
  const labeledWords = useMemo(() => {
    if (!data?.original?.words) return [];
    return data.original.words.filter((word) => {
      const pred = getPrediction(word.line_id, word.word_id);
      return pred && pred.predicted_label !== "O";
    });
  }, [data, getPrediction]);

  // Animate labels appearing
  const labelTransitions = useTransition(
    inView && labeledWords.length > 0 ? labeledWords : [],
    {
      keys: (word) => `${word.receipt_id}-${word.line_id}-${word.word_id}`,
      from: { opacity: 0, transform: "scale(0.8)" },
      enter: (item, index) => ({
        opacity: 1,
        transform: "scale(1)",
        delay: index * 30,
      }),
      leave: { opacity: 0, transform: "scale(0.8)" },
      config: { duration: 300 },
    }
  );

  if (loading && !data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading}>Loading LayoutLM inference results...</div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.error}>Error: {error}</div>
      </div>
    );
  }

  if (!data) return null;

  const receipt = data.original.receipt;
  const metrics = data.metrics;

  return (
    <div ref={ref} className={styles.container}>
      <div className={styles.header}>
        <h3>LayoutLM Predictions</h3>
        <p className={styles.subtitle}>
          AI model trained on {metrics.total_words} words with {formatConfidence(metrics.overall_accuracy)} accuracy
        </p>
      </div>

      {/* Metrics Summary */}
      {showMetrics && (
        <div className={styles.metricsContainer}>
          <div className={styles.metricCard}>
            <div className={styles.metricValue}>{formatConfidence(metrics.overall_accuracy)}</div>
            <div className={styles.metricLabel}>Overall Accuracy</div>
          </div>
          <div className={styles.metricCard}>
            <div className={styles.metricValue}>{metrics.correct_predictions}</div>
            <div className={styles.metricLabel}>Correct Predictions</div>
          </div>
          <div className={styles.metricCard}>
            <div className={styles.metricValue}>{metrics.total_words}</div>
            <div className={styles.metricLabel}>Total Words</div>
          </div>
        </div>
      )}

      {/* Per-Label Performance */}
      {showMetrics && Object.keys(metrics.per_label_f1).length > 0 && (
        <div className={styles.perLabelMetrics}>
          <h4>Per-Label Performance</h4>
          <div className={styles.labelMetricsGrid}>
            {Object.entries(metrics.per_label_f1)
              .filter(([label]) => label !== "O")
              .map(([label, f1]) => {
                const baseLabel = getBaseLabel(label);
                const labelInfo = LAYOUTLM_LABELS[baseLabel as keyof typeof LAYOUTLM_LABELS];
                if (!labelInfo) return null;
                return (
                  <div key={label} className={styles.labelMetric}>
                    <div
                      className={styles.labelColorDot}
                      style={{ backgroundColor: labelInfo.color }}
                    />
                    <div className={styles.labelMetricInfo}>
                      <div className={styles.labelMetricName}>{labelInfo.name}</div>
                      <div className={styles.labelMetricF1}>F1: {formatConfidence(f1)}</div>
                    </div>
                  </div>
                );
              })}
          </div>
        </div>
      )}

      {/* Receipt Image with Overlays */}
      {imageUrl && (
        <div className={styles.imageContainer}>
          <div className={styles.imageWrapper}>
            <Image
              src={imageUrl}
              alt="Receipt with LayoutLM predictions"
              width={receipt.width}
              height={receipt.height}
              className={styles.receiptImage}
              unoptimized
            />
            <svg
              className={styles.overlay}
              viewBox={`0 0 ${receipt.width} ${receipt.height}`}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: "100%",
                pointerEvents: "none",
              }}
            >
              {labelTransitions((style, word) => {
                const pred = getPrediction(word.line_id, word.word_id);
                if (!pred || pred.predicted_label === "O") return null;
                const bbox = getBoundingBox(word);
                if (!bbox) return null;

                const baseLabel = getBaseLabel(pred.predicted_label);
                const labelColor = getLabelColor(pred.predicted_label);
                const confColor = getConfidenceColor(pred.predicted_confidence);

                return (
                  <animated.g
                    key={`${word.receipt_id}-${word.line_id}-${word.word_id}`}
                    style={style}
                  >
                    {/* Bounding box */}
                    <animated.rect
                      x={bbox.left}
                      y={bbox.top}
                      width={bbox.width}
                      height={bbox.height}
                      fill="none"
                      stroke={labelColor}
                      strokeWidth={showConfidence ? pred.predicted_confidence * 3 : 2}
                      opacity={showConfidence ? 0.6 + pred.predicted_confidence * 0.4 : 0.7}
                      rx={2}
                    />
                    {/* Label text background */}
                    {showConfidence && (
                      <animated.rect
                        x={bbox.left}
                        y={bbox.top - 20}
                        width={Math.max(bbox.width, 80)}
                        height={18}
                        fill={labelColor}
                        opacity={0.9}
                        rx={3}
                      />
                    )}
                    {/* Label text */}
                    {showConfidence && (
                      <animated.text
                        x={bbox.left + 4}
                        y={bbox.top - 5}
                        fill="white"
                        fontSize="12"
                        fontWeight="bold"
                      >
                        {baseLabel} ({formatConfidence(pred.predicted_confidence)})
                      </animated.text>
                    )}
                  </animated.g>
                );
              })}
            </svg>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className={styles.legend}>
        <h4>Label Types</h4>
        <div className={styles.legendItems}>
          {Object.entries(LAYOUTLM_LABELS).map(([key, info]) => (
            <div key={key} className={styles.legendItem}>
              <div
                className={styles.legendColor}
                style={{ backgroundColor: info.color }}
              />
              <div className={styles.legendText}>
                <div className={styles.legendName}>{info.name}</div>
                <div className={styles.legendDescription}>{info.description}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Toggle Controls */}
      <div className={styles.controls}>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={showConfidence}
            onChange={(e) => setShowConfidence(e.target.checked)}
          />
          Show Confidence Scores
        </label>
        <label className={styles.toggle}>
          <input
            type="checkbox"
            checked={showMetrics}
            onChange={(e) => setShowMetrics(e.target.checked)}
          />
          Show Metrics
        </label>
        <button onClick={fetchData} className={styles.refreshButton}>
          🔄 Load New Receipt
        </button>
      </div>

      {/* Model Info */}
      <div className={styles.modelInfo}>
        <p className={styles.modelInfoText}>
          Model: {data.model_info.model_name} | Device: {data.model_info.device} | Cached:{" "}
          {new Date(data.cached_at).toLocaleTimeString()}
        </p>
      </div>
    </div>
  );
};

export default LayoutLMInferenceVisualization;

