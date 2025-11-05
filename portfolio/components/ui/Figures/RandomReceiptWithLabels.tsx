import React, { useEffect, useState, useMemo, useCallback } from "react";
import { useTransition, animated } from "@react-spring/web";
import Image from "next/image";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import { api } from "../../../services/api";
import { getBestImageUrl, detectImageFormatSupport } from "../../../utils/imageFormat";
import { RandomReceiptDetailsResponse, ReceiptWordLabel, ReceiptWord, FormatSupport } from "../../../types/api";
import styles from "../../../styles/RandomReceiptWithLabels.module.css";

const isDevelopment = process.env.NODE_ENV === "development";

// Color mapping for different label types
const getLabelColor = (label: string): string => {
  const labelLower = label.toLowerCase();
  if (labelLower.includes("total") || labelLower.includes("amount")) {
    return "var(--color-green)";
  }
  if (labelLower.includes("date") || labelLower.includes("time")) {
    return "var(--color-blue)";
  }
  if (labelLower.includes("merchant") || labelLower.includes("business") || labelLower.includes("name")) {
    return "var(--color-yellow)";
  }
  if (labelLower.includes("address") || labelLower.includes("location")) {
    return "var(--color-red)";
  }
  return "var(--color-blue)";
};

const RandomReceiptWithLabels: React.FC = () => {
  const [data, setData] = useState<RandomReceiptDetailsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);
  const [windowWidth, setWindowWidth] = useState<number | null>(null);
  const [resetKey, setResetKey] = useState(0); // Key to force reset of transitions
  const [ref, inView] = useOptimizedInView({ threshold: 0.1 });

  // Detect window width for responsive behavior (client-side only)
  useEffect(() => {
    const updateWindowWidth = () => {
      setWindowWidth(window.innerWidth);
    };
    
    // Only set on client to avoid hydration mismatch
    if (typeof window !== "undefined") {
      updateWindowWidth();
      window.addEventListener("resize", updateWindowWidth);
      return () => window.removeEventListener("resize", updateWindowWidth);
    }
  }, []);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch data when in view
  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      // Increment resetKey to force transitions to reset and Image remount
      setResetKey((prev) => prev + 1);
      // Clear previous data immediately to prevent overlay
      setData(null);
      
      const response = await api.fetchRandomReceiptDetails();
      setData(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load receipt");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch when component comes into view
  useEffect(() => {
    if (!inView || data) return;
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView]); // Only depend on inView to avoid refetch loops

  // Create a map of word_id to label for quick lookup
  const labelMap = useMemo(() => {
    if (!data?.labels) return new Map<number, ReceiptWordLabel>();
    const map = new Map<number, ReceiptWordLabel>();
    data.labels.forEach((label) => {
      map.set(label.word_id, label);
    });
    return map;
  }, [data?.labels]);

  // Filter words that have labels
  const labeledWords = useMemo(() => {
    if (!data?.words) return [];
    return data.words.filter((word) => labelMap.has(word.word_id));
  }, [data?.words, labelMap]);

  // Determine if mobile based on window width (default to false for SSR)
  const isMobile = windowWidth !== null && windowWidth <= 768;

  // Animate labels appearing - use resetKey to force reset when data changes
  const labelTransitions = useTransition(
    inView && labeledWords.length > 0 ? labeledWords : [],
    {
      keys: (word) => {
        const label = labelMap.get(word.word_id);
        return `${resetKey}-${word.receipt_id}-${label?.line_id || 0}-${word.word_id}`;
      },
      from: { opacity: 0, transform: "scale(0.8)" },
      enter: (item, index) => ({
        opacity: 1,
        transform: "scale(1)",
        delay: index * 50,
      }),
      leave: { opacity: 0, transform: "scale(0.8)" },
      config: { duration: 400 },
    }
  );

  // Show loading state - only show if we don't have existing data
  if (loading && !data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.header}>
          <h3>Receipt with Labels</h3>
          {isDevelopment && (
            <button
              onClick={fetchData}
              disabled={loading}
              className={styles.reloadButton}
              title="Load a new random receipt"
            >
              {loading ? "Loading..." : "🔄 Reload"}
            </button>
          )}
        </div>
        <div className={styles.loading}>Loading receipt with labels...</div>
      </div>
    );
  }
  
  // Show loading overlay when reloading with existing data
  const isLoadingNew = loading && data;

  // Show error state
  if (error && !data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.header}>
          <h3>Receipt with Labels</h3>
          {isDevelopment && (
            <button
              onClick={fetchData}
              disabled={loading}
              className={styles.reloadButton}
              title="Load a new random receipt"
            >
              {loading ? "Loading..." : "🔄 Reload"}
            </button>
          )}
        </div>
        <div className={styles.error}>Error: {error}</div>
      </div>
    );
  }

  // Don't render if we don't have data or format support yet
  if (!data || !formatSupport) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.header}>
          <h3>Receipt with Labels</h3>
          {isDevelopment && (
            <button
              onClick={fetchData}
              disabled={loading}
              className={styles.reloadButton}
              title="Load a new random receipt"
            >
              {loading ? "Loading..." : "🔄 Reload"}
            </button>
          )}
        </div>
      </div>
    );
  }

  const receipt = data.receipt;
  const svgWidth = receipt.width;
  const svgHeight = receipt.height;

  // Calculate display dimensions - responsive
  const maxDisplayWidth = isMobile ? 350 : 600;
  const maxDisplayHeight = isMobile ? 500 : 800;
  const aspectRatio = svgWidth / svgHeight;

  let displayWidth = maxDisplayWidth;
  let displayHeight = maxDisplayWidth / aspectRatio;

  if (displayHeight > maxDisplayHeight) {
    displayHeight = maxDisplayHeight;
    displayWidth = maxDisplayHeight * aspectRatio;
  }
  
  // For mobile, constrain the image height to a reasonable max
  // Use viewport height calculation for better responsiveness
  // 60vh ensures it doesn't take up too much screen space
  const mobileMaxHeight = isMobile && typeof window !== "undefined" 
    ? Math.min(500, window.innerHeight * 0.6) 
    : 500;

  const cdnUrl = getBestImageUrl(receipt, formatSupport, isMobile ? "medium" : "full");

  return (
    <div ref={ref} className={styles.container}>
      <div className={styles.header}>
        <h3>Receipt with Labels</h3>
        {isDevelopment && (
          <button
            onClick={fetchData}
            disabled={loading}
            className={styles.reloadButton}
            title="Load a new random receipt"
          >
            {loading ? "Loading..." : "🔄 Reload"}
          </button>
        )}
      </div>
      <p>
        This receipt shows how words are automatically labeled with semantic
        meaning, such as dates, totals, merchant names, and addresses.
      </p>

      <div className={styles.wrapper} style={{ position: "relative" }}>
        {/* Desktop: Image with overlay and side panel */}
        {!isMobile ? (
          <>
            <div className={styles.imageContainer}>
              <div
                className={styles.imageWrapper}
                style={{
                  width: displayWidth,
                  height: displayHeight,
                  position: "relative",
                  opacity: isLoadingNew ? 0.5 : 1,
                  transition: "opacity 0.2s ease",
                }}
              >
                {/* Loading overlay when reloading */}
                {isLoadingNew && (
                  <div className={styles.loadingOverlay}>
                    <div className={styles.loadingSpinner}>Loading new receipt...</div>
                  </div>
                )}
                <Image
                  key={`${resetKey}-${receipt.image_id}-${receipt.receipt_id}`}
                  src={cdnUrl}
                  alt="Receipt with labels"
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
                  {labelTransitions((style, word) => {
                    const label = labelMap.get(word.word_id);
                    if (!label) return null;

                    const x1 = word.top_left.x * svgWidth;
                    const y1 = (1 - word.top_left.y) * svgHeight;
                    const x2 = word.top_right.x * svgWidth;
                    const y2 = (1 - word.top_right.y) * svgHeight;
                    const x3 = word.bottom_right.x * svgWidth;
                    const y3 = (1 - word.bottom_right.y) * svgHeight;
                    const x4 = word.bottom_left.x * svgWidth;
                    const y4 = (1 - word.bottom_left.y) * svgHeight;
                    const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

                    const color = getLabelColor(label.label);
                    const centerX = (x1 + x2 + x3 + x4) / 4;
                    const centerY = (y1 + y2 + y3 + y4) / 4;

                    return (
                      <g key={`${word.receipt_id}-${label.line_id}-${word.word_id}`}>
                        <animated.polygon
                          style={style}
                          points={points}
                          fill={color}
                          fillOpacity={0.2}
                          stroke={color}
                          strokeWidth="2"
                        />
                        <animated.text
                          style={style}
                          x={centerX}
                          y={centerY}
                          fontSize="10"
                          fill={color}
                          textAnchor="middle"
                          dominantBaseline="middle"
                          fontWeight="bold"
                          pointerEvents="all"
                        >
                          {label.label}
                        </animated.text>
                      </g>
                    );
                  })}
                </svg>
              </div>
            </div>
            <div className={styles.labelsList}>
              <h4>Labels Found:</h4>
              <ul>
                {Array.from(labelMap.values()).map((label) => {
                  const word = data.words.find((w) => w.word_id === label.word_id);
                  if (!word) return null;
                  const color = getLabelColor(label.label);
                  return (
                    <li key={`${label.receipt_id}-${label.line_id}-${label.word_id}`}>
                      <span
                        className={styles.labelBadge}
                        style={{ backgroundColor: color }}
                      >
                        {label.label}
                      </span>
                      <span className={styles.wordText}>{word.text}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          </>
        ) : (
          /* Mobile: Stacked layout with image and label list */
          <div className={styles.mobileContainer}>
            <div 
              className={styles.mobileImageWrapper}
              style={{
                maxHeight: `${mobileMaxHeight}px`,
                overflow: "hidden",
                display: "flex",
                justifyContent: "center",
                alignItems: "flex-start",
              }}
            >
              <Image
                key={`${resetKey}-${receipt.image_id}-${receipt.receipt_id}`}
                src={cdnUrl}
                alt="Receipt with labels"
                width={svgWidth}
                height={svgHeight}
                style={{
                  width: "100%",
                  height: "auto",
                  maxHeight: `${mobileMaxHeight}px`,
                  objectFit: "contain",
                  borderRadius: "8px",
                }}
                priority
              />
            </div>
            <div className={styles.labelsList}>
              <h4>Labels Found:</h4>
              <ul>
                {Array.from(labelMap.values()).map((label) => {
                  const word = data.words.find((w) => w.word_id === label.word_id);
                  if (!word) return null;
                  const color = getLabelColor(label.label);
                  return (
                    <li key={`${label.receipt_id}-${label.line_id}-${label.word_id}`}>
                      <span
                        className={styles.labelBadge}
                        style={{ backgroundColor: color }}
                      >
                        {label.label}
                      </span>
                      <span className={styles.wordText}>{word.text}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default RandomReceiptWithLabels;

