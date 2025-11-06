import { animated, useTransition } from "@react-spring/web";
import Image from "next/image";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import { api } from "../../../services/api";
import styles from "../../../styles/RandomReceiptWithLabels.module.css";
import { FormatSupport, RandomReceiptDetailsResponse, ReceiptWord, ReceiptWordLabel } from "../../../types/api";
import { detectImageFormatSupport, getBestImageUrl } from "../../../utils/imageFormat";

const isDevelopment = process.env.NODE_ENV === "development";

// CORE_LABELS categories (4 categories)
const LABEL_CATEGORIES = {
  merchant: {
    name: "Merchant",
    color: "var(--color-yellow)",
    labels: [
      "MERCHANT_NAME",
      "STORE_HOURS",
      "PHONE_NUMBER",
      "WEBSITE",
      "LOYALTY_ID",
      "ADDRESS_LINE",
    ],
  },
  transaction: {
    name: "Transaction",
    color: "var(--color-blue)",
    labels: ["DATE", "TIME", "PAYMENT_METHOD", "COUPON", "DISCOUNT"],
  },
  lineItems: {
    name: "Line Items",
    color: "var(--color-red)",
    labels: ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE"],
  },
  totals: {
    name: "Currency",
    color: "var(--color-green)",
    labels: ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"],
  },
} as const;

// Color mapping for different label types
const getLabelColor = (label: string): string => {
  // Check each category
  for (const category of Object.values(LABEL_CATEGORIES)) {
    if (category.labels.includes(label)) {
      return category.color;
    }
  }
  // Default fallback
  return "var(--color-blue)";
};

// Get category for a label
const getLabelCategory = (label: string): keyof typeof LABEL_CATEGORIES | null => {
  for (const [key, category] of Object.entries(LABEL_CATEGORIES)) {
    if (category.labels.includes(label)) {
      return key as keyof typeof LABEL_CATEGORIES;
    }
  }
  return null;
};

// Format label name for display (e.g., "ADDRESS_LINE" -> "address line")
const formatLabelName = (label: string): string => {
  return label.toLowerCase().replace(/_/g, " ");
};

// CORE_LABELS descriptions from receipt_label/constants.py
const LABEL_DESCRIPTIONS: Record<string, string> = {
  MERCHANT_NAME: "Trading name or brand of the store issuing the receipt.",
  STORE_HOURS: "Printed business hours or opening times for the merchant.",
  PHONE_NUMBER: "Telephone number printed on the receipt (store's main line).",
  WEBSITE: "Web or email address printed on the receipt (e.g., sprouts.com).",
  LOYALTY_ID: "Customer loyalty / rewards / membership identifier.",
  ADDRESS_LINE: "Full address line (street + city etc.) printed on the receipt.",
  DATE: "Calendar date of the transaction.",
  TIME: "Time of the transaction.",
  PAYMENT_METHOD: "Payment instrument summary (e.g., VISA ••••1234, CASH).",
  COUPON: "Coupon code or description that reduces price.",
  DISCOUNT: "Any non-coupon discount line item (e.g., 10% member discount).",
  PRODUCT_NAME: "Descriptive text of a purchased product (item name).",
  QUANTITY: "Numeric count or weight of the item (e.g., 2, 1.31 lb).",
  UNIT_PRICE: "Price per single unit / weight before tax.",
  LINE_TOTAL: "Extended price for that line (quantity x unit price).",
  SUBTOTAL: "Sum of all line totals before tax and discounts.",
  TAX: "Any tax line (sales tax, VAT, bottle deposit).",
  GRAND_TOTAL: "Final amount due after all discounts, taxes and fees.",
};

const RandomReceiptWithLabels: React.FC = () => {
  const [data, setData] = useState<RandomReceiptDetailsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);
  const [windowWidth, setWindowWidth] = useState<number | null>(null);
  const [isMounted, setIsMounted] = useState(false); // Track if component is mounted (client-side)
  const [resetKey, setResetKey] = useState(0); // Key to force reset of transitions
  const [ref, inView] = useOptimizedInView({ threshold: 0.1 });

  // Track when component is mounted (client-side only)
  useEffect(() => {
    setIsMounted(true);
  }, []);

  // Detect window width for responsive behavior (client-side only)
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

  // Filter to only VALID labels
  const validLabels = useMemo(() => {
    if (!data?.labels) return [];
    return data.labels.filter((label) => label.validation_status === "VALID");
  }, [data?.labels]);

  // Group labels by receipt_id and get unique CORE_LABELS per receipt (only VALID)
  const labelsByReceipt = useMemo(() => {
    if (validLabels.length === 0) return new Map<number, Set<string>>();
    const map = new Map<number, Set<string>>();
    validLabels.forEach((label) => {
      if (!map.has(label.receipt_id)) {
        map.set(label.receipt_id, new Set());
      }
      map.get(label.receipt_id)!.add(label.label);
    });
    return map;
  }, [validLabels]);

  // Create a map of (receipt_id:line_id:word_id) to all VALID labels
  // This ensures labels match the correct word even if word_ids are duplicated across receipts
  const labelMap = useMemo(() => {
    if (validLabels.length === 0) return new Map<string, ReceiptWordLabel[]>();
    const map = new Map<string, ReceiptWordLabel[]>();
    validLabels.forEach((label) => {
      // Use composite key: receipt_id:line_id:word_id
      const key = `${label.receipt_id}:${label.line_id}:${label.word_id}`;
      if (!map.has(key)) {
        map.set(key, []);
      }
      map.get(key)!.push(label);
    });
    return map;
  }, [validLabels]);

  // Filter words that have labels and match by receipt_id, line_id, and word_id
  // Ensure we only render each word once (deduplicate by composite key)
  const labeledWords = useMemo(() => {
    if (!data?.words) return [];
    const seen = new Set<string>();
    const uniqueWords: ReceiptWord[] = [];

    for (const word of data.words) {
      // Match using the same composite key
      const key = `${word.receipt_id}:${word.line_id}:${word.word_id}`;
      if (labelMap.has(key) && !seen.has(key)) {
        seen.add(key);
        uniqueWords.push(word);
      }
    }

    return uniqueWords;
  }, [data?.words, labelMap]);

  // Helper to get labels for a word using composite key
  const getWordLabels = useCallback((word: ReceiptWord): ReceiptWordLabel[] => {
    const key = `${word.receipt_id}:${word.line_id}:${word.word_id}`;
    return labelMap.get(key) || [];
  }, [labelMap]);

  // Determine if mobile based on window width (only after mount to avoid hydration mismatch)
  // Default to false during SSR and initial render to match server output
  const isMobile = isMounted && windowWidth !== null && windowWidth <= 768;

  // Animate labels appearing - use resetKey to force reset when data changes
  const labelTransitions = useTransition(
    inView && labeledWords.length > 0 ? labeledWords : [],
    {
      keys: (word) => {
        const labels = getWordLabels(word);
        return `${resetKey}-${word.receipt_id}-${word.line_id}-${word.word_id}`;
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
  // Reduced max heights to make receipts more compact
  const maxDisplayWidth = isMobile ? 350 : 600;
  const maxDisplayHeight = isMobile ? 400 : 500;
  const aspectRatio = svgWidth / svgHeight;

  let displayWidth = maxDisplayWidth;
  let displayHeight = maxDisplayWidth / aspectRatio;

  // Ensure height doesn't exceed max, maintaining aspect ratio
  if (displayHeight > maxDisplayHeight) {
    displayHeight = maxDisplayHeight;
    displayWidth = maxDisplayHeight * aspectRatio;
  }

  // For mobile, use viewport height for better responsiveness
  // 50vh ensures it doesn't take up too much screen space
  const mobileMaxHeight = isMobile && typeof window !== "undefined"
    ? Math.min(400, window.innerHeight * 0.5)
    : 400;

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
                    const labels = getWordLabels(word);
                    // Double-check: only render if we have labels (shouldn't happen but safety check)
                    if (!labels || labels.length === 0) return null;

                    // Use word corner coordinates directly (normalized 0-1 range)
                    // Convert to pixel coordinates for SVG
                    // Flip Y coordinate: receipt coordinates have Y=0 at bottom, SVG has Y=0 at top
                    const x1 = word.top_left.x * svgWidth;
                    const y1 = (1 - word.top_left.y) * svgHeight;
                    const x2 = word.top_right.x * svgWidth;
                    const y2 = (1 - word.top_right.y) * svgHeight;
                    const x3 = word.bottom_right.x * svgWidth;
                    const y3 = (1 - word.bottom_right.y) * svgHeight;
                    const x4 = word.bottom_left.x * svgWidth;
                    const y4 = (1 - word.bottom_left.y) * svgHeight;
                    const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

                    // Use the first label for color
                    // If multiple labels exist, prefer the one that matches our category system
                    const primaryLabel = labels[0];

                    // Try to find a label that matches our category system first
                    // This ensures colors match what's shown in the category list
                    let labelToUse = primaryLabel;
                    for (const label of labels) {
                      if (getLabelCategory(label.label) !== null) {
                        labelToUse = label;
                        break;
                      }
                    }

                    const color = getLabelColor(labelToUse.label);

                    return (
                      <animated.polygon
                        key={`${word.receipt_id}-${word.line_id}-${word.word_id}`}
                        style={style}
                        points={points}
                        fill={color}
                        fillOpacity={0.2}
                        stroke={color}
                        strokeWidth="2"
                      />
                    );
                  })}
                </svg>
              </div>
            </div>
            <div className={styles.labelsList}>
              {Array.from(labelsByReceipt.entries()).map(([receiptId, labels]) => (
                <div key={receiptId} className={styles.receiptLabelsSection}>
                  <div className={styles.legendContainer}>
                    {Object.entries(LABEL_CATEGORIES).map(([categoryKey, category]) => {
                      const labelsInCategory = Array.from(labels)
                        .filter((label) => category.labels.includes(label))
                        .sort();
                      if (labelsInCategory.length === 0) return null;

                      return (
                        <div key={categoryKey} className={styles.categoryGroup}>
                          <div className={styles.categoryHeaderContainer}>
                            <div
                              className={styles.categoryColorSquare}
                              style={{ backgroundColor: category.color }}
                            />
                            <div className={styles.categoryHeader}>
                              {category.name}
                            </div>
                          </div>
                          {!isMobile && (
                            <ul className={styles.labelList}>
                              {labelsInCategory.map((label) => (
                                <li key={`${receiptId}-${label}`}>
                                  {formatLabelName(label)}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          /* Mobile: Side-by-side layout with image and label list */
          <div className={styles.mobileContainer}>
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
                    const labels = getWordLabels(word);
                    // Double-check: only render if we have labels (shouldn't happen but safety check)
                    if (!labels || labels.length === 0) return null;

                    // Use word corner coordinates directly (normalized 0-1 range)
                    // Convert to pixel coordinates for SVG
                    // Flip Y coordinate: receipt coordinates have Y=0 at bottom, SVG has Y=0 at top
                    const x1 = word.top_left.x * svgWidth;
                    const y1 = (1 - word.top_left.y) * svgHeight;
                    const x2 = word.top_right.x * svgWidth;
                    const y2 = (1 - word.top_right.y) * svgHeight;
                    const x3 = word.bottom_right.x * svgWidth;
                    const y3 = (1 - word.bottom_right.y) * svgHeight;
                    const x4 = word.bottom_left.x * svgWidth;
                    const y4 = (1 - word.bottom_left.y) * svgHeight;
                    const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

                    // Use the first label for color
                    // If multiple labels exist, prefer the one that matches our category system
                    const primaryLabel = labels[0];

                    // Try to find a label that matches our category system first
                    // This ensures colors match what's shown in the category list
                    let labelToUse = primaryLabel;
                    for (const label of labels) {
                      if (getLabelCategory(label.label) !== null) {
                        labelToUse = label;
                        break;
                      }
                    }

                    const color = getLabelColor(labelToUse.label);

                    return (
                      <animated.polygon
                        key={`${word.receipt_id}-${word.line_id}-${word.word_id}`}
                        style={style}
                        points={points}
                        fill={color}
                        fillOpacity={0.2}
                        stroke={color}
                        strokeWidth="2"
                      />
                    );
                  })}
                </svg>
              </div>
            </div>
            <div className={styles.labelsList}>
              {Array.from(labelsByReceipt.entries()).map(([receiptId, labels]) => (
                <div key={receiptId} className={styles.receiptLabelsSection}>
                  <div className={styles.legendContainer}>
                    {Object.entries(LABEL_CATEGORIES).map(([categoryKey, category]) => {
                      const labelsInCategory = Array.from(labels)
                        .filter((label) => category.labels.includes(label))
                        .sort();
                      if (labelsInCategory.length === 0) return null;

                      return (
                        <div key={categoryKey} className={styles.categoryGroup}>
                          <div className={styles.categoryHeaderContainer}>
                            <div
                              className={styles.categoryColorSquare}
                              style={{ backgroundColor: category.color }}
                            />
                            <div className={styles.categoryHeader}>
                              {category.name}
                            </div>
                          </div>
                          {!isMobile && (
                            <ul className={styles.labelList}>
                              {labelsInCategory.map((label) => (
                                <li key={`${receiptId}-${label}`}>
                                  {formatLabelName(label)}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default RandomReceiptWithLabels;

