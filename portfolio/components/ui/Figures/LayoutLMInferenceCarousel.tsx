import { animated, useSpring, useTransition } from "@react-spring/web";
import Image from "next/image";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import styles from "../../../styles/LayoutLMInferenceCarousel.module.css";
import { detectImageFormatSupport, FormatSupport, getBestImageUrl } from "../../../utils/imageFormat";

const isDevelopment = process.env.NODE_ENV === "development";

// LayoutLM uses 4 simplified labels
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

// Consistent order for bar chart display
const LABEL_ORDER = ["MERCHANT_NAME", "DATE", "ADDRESS", "AMOUNT", "O"] as const;

// Get display name for a label
const getLabelDisplayName = (label: string): string => {
  if (label === "O") return "No Label";
  return LAYOUTLM_LABELS[label as keyof typeof LAYOUTLM_LABELS]?.name || label;
};

// Get color for a label
const getLabelColor = (label: string): string => {
  if (label === "O") return "var(--text-color)";
  return LAYOUTLM_LABELS[label as keyof typeof LAYOUTLM_LABELS]?.color || "var(--color-blue)";
};

// Format confidence as percentage
const formatConfidence = (conf: number): string => {
  return `${(conf * 100).toFixed(1)}%`;
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
      top_left?: { x: number; y: number };
      top_right?: { x: number; y: number };
      bottom_left?: { x: number; y: number };
      bottom_right?: { x: number; y: number };
    }>;
    predictions: Array<{
      word_id: number;
      line_id: number;
      text: string;
      predicted_label: string;
      predicted_label_base: string;
      ground_truth_label: string | null;
      ground_truth_label_base: string | null;
      ground_truth_label_original?: string | null;
      predicted_confidence: number;
      is_correct: boolean;
      all_class_probabilities_base?: Record<string, number>;
    }>;
  };
  metrics: {
    overall_accuracy: number;
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

interface LabeledWord {
  word: LayoutLMInferenceResponse["original"]["words"][0];
  prediction: LayoutLMInferenceResponse["original"]["predictions"][0];
}

const CYCLE_INTERVAL_MS = 3000; // 3 seconds per word

const LayoutLMInferenceCarousel: React.FC = () => {
  const [data, setData] = useState<LayoutLMInferenceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [windowWidth, setWindowWidth] = useState<number | null>(null);
  const [isMounted, setIsMounted] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const [ref, inView] = useOptimizedInView({ threshold: 0.1 });

  // Track when component is mounted (client-side only)
  useEffect(() => {
    setIsMounted(true);
  }, []);

  // Detect window width for responsive behavior
  useEffect(() => {
    if (!isMounted) return;
    const updateWindowWidth = () => {
      setWindowWidth(window.innerWidth);
    };
    updateWindowWidth();
    window.addEventListener("resize", updateWindowWidth);
    return () => window.removeEventListener("resize", updateWindowWidth);
  }, [isMounted]);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch LayoutLM inference data
  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setResetKey((prev) => prev + 1);
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
      setCurrentIndex(0);
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

  // Get labeled words (words with predictions or ground truth)
  const labeledWords = useMemo((): LabeledWord[] => {
    if (!data?.original?.words || !data?.original?.predictions) return [];

    const predictionMap = new Map<string, LayoutLMInferenceResponse["original"]["predictions"][0]>();
    data.original.predictions.forEach((pred) => {
      const key = `${pred.line_id}:${pred.word_id}`;
      predictionMap.set(key, pred);
    });

    return data.original.words
      .map((word) => {
        const key = `${word.line_id}:${word.word_id}`;
        const prediction = predictionMap.get(key);
        if (!prediction) return null;
        if (prediction.predicted_label === "O" && !prediction.ground_truth_label_base) return null;
        return { word, prediction };
      })
      .filter((item): item is LabeledWord => item !== null);
  }, [data]);

  // Cycle through words
  useEffect(() => {
    if (!inView || labeledWords.length === 0 || isPaused) return;

    const interval = setInterval(() => {
      setCurrentIndex((prev) => (prev + 1) % labeledWords.length);
    }, CYCLE_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [inView, labeledWords.length, isPaused]);

  // Get current word
  const currentWord = useMemo(() => {
    if (labeledWords.length === 0) return null;
    return labeledWords[currentIndex];
  }, [labeledWords, currentIndex]);

  // Determine if mobile
  const isMobile = isMounted && windowWidth !== null && windowWidth <= 768;

  // Get image URL
  const imageUrl = useMemo(() => {
    if (!data?.original?.receipt || !formatSupport) return null;
    const receipt = data.original.receipt;
    return getBestImageUrl(receipt, formatSupport, isMobile ? "medium" : "full");
  }, [data, formatSupport, isMobile]);

  // Calculate bounding box coordinates for SVG overlay
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
        const x1 = word.top_left.x * svgWidth;
        const y1 = (1 - word.top_left.y) * svgHeight;
        const x2 = word.top_right.x * svgWidth;
        const y2 = (1 - word.top_right.y) * svgHeight;
        const x3 = word.bottom_right.x * svgWidth;
        const y3 = (1 - word.bottom_right.y) * svgHeight;
        const x4 = word.bottom_left.x * svgWidth;
        const y4 = (1 - word.bottom_left.y) * svgHeight;
        return `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;
      }

      // Fallback: Use bounding_box
      const left = word.bounding_box.x * svgWidth;
      const bottom = word.bounding_box.y * svgHeight;
      const width = word.bounding_box.width * svgWidth;
      const height = word.bounding_box.height * svgHeight;
      const top = (1 - word.bounding_box.y - word.bounding_box.height) * svgHeight;
      const right = left + width;
      const bottomY = top + height;

      return `${left},${top} ${right},${top} ${right},${bottomY} ${left},${bottomY}`;
    },
    [data]
  );

  // Animation for labels
  const labelTransitions = useTransition(
    inView && labeledWords.length > 0 ? labeledWords : [],
    {
      keys: (item) => `${resetKey}-${item.word.receipt_id}-${item.word.line_id}-${item.word.word_id}`,
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


  // Determine if mobile for height reservation
  const isMobileForHeight = isMounted && windowWidth !== null && windowWidth <= 768;
  const reservedHeight = isMobileForHeight ? 400 : 500; // Match imageWrapper max-height

  if (loading && !data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading} style={{ minHeight: `${reservedHeight}px`, display: "flex", alignItems: "center", justifyContent: "center" }}>
          Loading LayoutLM inference results...
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.error} style={{ minHeight: `${reservedHeight}px`, display: "flex", alignItems: "center", justifyContent: "center" }}>
          Error: {error}
        </div>
      </div>
    );
  }

  if (!data || !formatSupport || !imageUrl || labeledWords.length === 0) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading} style={{ minHeight: `${reservedHeight}px`, display: "flex", alignItems: "center", justifyContent: "center" }}>
          No labeled words found
        </div>
      </div>
    );
  }

  const receipt = data.original.receipt;
  const svgWidth = receipt.width;
  const svgHeight = receipt.height;

  // Calculate display dimensions - responsive
  const maxDisplayWidth = isMobile ? 350 : 600;
  const maxDisplayHeight = isMobile ? 400 : 500;
  const aspectRatio = svgWidth / svgHeight;

  let displayWidth = maxDisplayWidth;
  let displayHeight = maxDisplayWidth / aspectRatio;

  if (displayHeight > maxDisplayHeight) {
    displayHeight = maxDisplayHeight;
    displayWidth = maxDisplayHeight * aspectRatio;
  }


  return (
    <div ref={ref} className={styles.container}>
      <div className={styles.wrapper} style={{ position: "relative" }}>
        {/* Desktop: Image with overlay and side panel */}
        {!isMobile ? (
          <>
            <div className={styles.imageContainer}>
              {isDevelopment && (
                <button
                  onClick={fetchData}
                  disabled={loading}
                  className={styles.reloadButton}
                  title="Load a new random receipt"
                >
                  🔄
                </button>
              )}
              <div
                className={styles.imageWrapper}
                style={{
                  width: displayWidth,
                  height: displayHeight,
                  position: "relative",
                }}
              >
                <Image
                  key={`${resetKey}-${receipt.image_id}-${receipt.receipt_id}`}
                  src={imageUrl}
                  alt="Receipt with LayoutLM predictions"
                  width={svgWidth}
                  height={svgHeight}
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "contain",
                    borderRadius: "8px",
                  }}
                  priority
                />
                <svg
                  className={styles.overlay}
                  viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: "100%",
                    pointerEvents: "none",
                  }}
                >
                  {labelTransitions((style, item) => {
                    const { word, prediction } = item;
                    const points = getBoundingBox(word);
                    if (!points) return null;

                    const isCurrent = item === currentWord;
                    const labelBase = prediction.predicted_label_base || "O";
                    const labelColor = getLabelColor(labelBase);

                    return (
                      <animated.polygon
                        key={`${word.receipt_id}-${word.line_id}-${word.word_id}`}
                        points={points}
                        fill={isCurrent ? labelColor : "black"}
                        fillOpacity={isCurrent ? 0.3 : 0.1}
                        stroke={isCurrent ? labelColor : "black"}
                        strokeWidth={isCurrent ? 3 : 2}
                        style={style}
                      />
                    );
                  })}
                </svg>
              </div>
            </div>
            <div className={styles.labelsList}>
              {currentWord && (
                <div className={styles.wordInfo}>
                  <div className={styles.wordHeader}>
                    <h4 className={styles.wordText}>{currentWord.word.text}</h4>
                  </div>
                  <div className={styles.wordControls}>
                    <button
                      onClick={() => setIsPaused(!isPaused)}
                      className={styles.controlButton}
                      title={isPaused ? "Resume" : "Pause"}
                    >
                      {isPaused ? "▶" : "⏸"}
                    </button>
                    <button
                      onClick={() =>
                        setCurrentIndex((prev) => (prev - 1 + labeledWords.length) % labeledWords.length)
                      }
                      className={styles.controlButton}
                      title="Previous"
                    >
                      ←
                    </button>
                    <button
                      onClick={() => setCurrentIndex((prev) => (prev + 1) % labeledWords.length)}
                      className={styles.controlButton}
                      title="Next"
                    >
                      →
                    </button>
                  </div>

                  <div className={styles.wordCounter}>
                    Word {currentIndex + 1} of {labeledWords.length}
                  </div>

                  {/* Bar Chart - All 5 classes (4 labels + O) */}
                  {currentWord.prediction.all_class_probabilities_base &&
                    Object.keys(currentWord.prediction.all_class_probabilities_base).length > 0 && (
                      <div className={styles.barChart}>
                        {LABEL_ORDER.map((labelKey) => {
                          const prob = currentWord.prediction.all_class_probabilities_base?.[labelKey];
                          if (prob === undefined) return null;

                          const labelColor = getLabelColor(labelKey);
                          const maxProb = Math.max(
                            ...Object.values(currentWord.prediction.all_class_probabilities_base || {})
                          );
                          const isTopPrediction = labelKey === currentWord.prediction.predicted_label_base;
                          const displayName = getLabelDisplayName(labelKey);

                          return (
                            <React.Fragment key={labelKey}>
                              <div
                                className={styles.barLabel}
                                style={isTopPrediction ? { fontWeight: 600 } : {}}
                              >
                                {displayName}
                              </div>
                              <div className={styles.barContainer}>
                                <div
                                  className={styles.bar}
                                  style={{
                                    width: `${(prob / maxProb) * 100}%`,
                                    backgroundColor: labelColor,
                                  }}
                                />
                              </div>
                              <div className={styles.barValue}>{formatConfidence(prob)}</div>
                            </React.Fragment>
                          );
                        })}
                      </div>
                    )}
                </div>
              )}
            </div>
          </>
        ) : (
          /* Mobile: Stacked layout */
          <div className={styles.mobileContainer}>
            <div className={styles.imageContainer}>
              {isDevelopment && (
                <button
                  onClick={fetchData}
                  disabled={loading}
                  className={styles.reloadButton}
                  title="Load a new random receipt"
                >
                  🔄
                </button>
              )}
              <div
                className={styles.imageWrapper}
                style={{
                  width: displayWidth,
                  height: displayHeight,
                  position: "relative",
                }}
              >
                <Image
                  key={`${resetKey}-${receipt.image_id}-${receipt.receipt_id}`}
                  src={imageUrl}
                  alt="Receipt with LayoutLM predictions"
                  width={svgWidth}
                  height={svgHeight}
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "contain",
                    borderRadius: "8px",
                  }}
                  priority
                />
                <svg
                  className={styles.overlay}
                  viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: "100%",
                    pointerEvents: "none",
                  }}
                >
                  {labelTransitions((style, item) => {
                    const { word, prediction } = item;
                    const points = getBoundingBox(word);
                    if (!points) return null;

                    const isCurrent = item === currentWord;
                    const labelBase = prediction.predicted_label_base || "O";
                    const labelColor = getLabelColor(labelBase);

                    return (
                      <animated.polygon
                        key={`${word.receipt_id}-${word.line_id}-${word.word_id}`}
                        points={points}
                        fill={isCurrent ? labelColor : "black"}
                        fillOpacity={isCurrent ? 0.3 : 0.1}
                        stroke={isCurrent ? labelColor : "black"}
                        strokeWidth={isCurrent ? 3 : 2}
                        style={style}
                      />
                    );
                  })}
                </svg>
              </div>
            </div>
            {currentWord && (
              <div className={styles.labelsList}>
                <div className={styles.wordInfo}>
                  <div className={styles.wordHeader}>
                    <h4 className={styles.wordText}>{currentWord.word.text}</h4>
                  </div>
                  <div className={styles.wordControls}>
                    <button
                      onClick={() => setIsPaused(!isPaused)}
                      className={styles.controlButton}
                      title={isPaused ? "Resume" : "Pause"}
                    >
                      {isPaused ? "▶" : "⏸"}
                    </button>
                    <button
                      onClick={() =>
                        setCurrentIndex((prev) => (prev - 1 + labeledWords.length) % labeledWords.length)
                      }
                      className={styles.controlButton}
                      title="Previous"
                    >
                      ←
                    </button>
                    <button
                      onClick={() => setCurrentIndex((prev) => (prev + 1) % labeledWords.length)}
                      className={styles.controlButton}
                      title="Next"
                    >
                      →
                    </button>
                  </div>

                  <div className={styles.wordCounter}>
                    Word {currentIndex + 1} of {labeledWords.length}
                  </div>

                  {/* Bar Chart - All 5 classes (4 labels + O) */}
                  {currentWord.prediction.all_class_probabilities_base &&
                    Object.keys(currentWord.prediction.all_class_probabilities_base).length > 0 && (
                      <div className={styles.barChart}>
                        {LABEL_ORDER.map((labelKey) => {
                          const prob = currentWord.prediction.all_class_probabilities_base?.[labelKey];
                          if (prob === undefined) return null;

                          const labelColor = getLabelColor(labelKey);
                          const maxProb = Math.max(
                            ...Object.values(currentWord.prediction.all_class_probabilities_base || {})
                          );
                          const isTopPrediction = labelKey === currentWord.prediction.predicted_label_base;
                          const displayName = getLabelDisplayName(labelKey);

                          return (
                            <React.Fragment key={labelKey}>
                              <div
                                className={styles.barLabel}
                                style={isTopPrediction ? { fontWeight: 600 } : {}}
                              >
                                {displayName}
                              </div>
                              <div className={styles.barContainer}>
                                <div
                                  className={styles.bar}
                                  style={{
                                    width: `${(prob / maxProb) * 100}%`,
                                    backgroundColor: labelColor,
                                  }}
                                />
                              </div>
                              <div className={styles.barValue}>{formatConfidence(prob)}</div>
                            </React.Fragment>
                          );
                        })}
                      </div>
                    )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default LayoutLMInferenceCarousel;
