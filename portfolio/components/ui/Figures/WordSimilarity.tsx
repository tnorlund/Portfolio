import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../../../services/api";
import { AddressBoundingBox, MilkSimilarityResponse, MilkReceiptData, MilkSimilarityTiming } from "../../../types/api";
import {
  detectImageFormatSupport,
  getBestImageUrl,
} from "../../../utils/imageFormat";

// Simple seeded random number generator for consistent randomness
function seededRandom(seed: number): () => number {
  let value = seed;
  return () => {
    value = (value * 9301 + 49297) % 233280;
    return value / 233280;
  };
}

// Calculate bounding box of rotated rectangle
function getRotatedBoundingBox(width: number, height: number, rotationDeg: number): { width: number; height: number } {
  const rotationRad = (rotationDeg * Math.PI) / 180;
  const cos = Math.abs(Math.cos(rotationRad));
  const sin = Math.abs(Math.sin(rotationRad));

  const rotatedWidth = width * cos + height * sin;
  const rotatedHeight = width * sin + height * cos;

  return { width: rotatedWidth, height: rotatedHeight };
}

interface CardPosition {
  x: number;
  y: number;
  rotation: number;
}

interface SafeZone {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

// Constraint-based position solver that guarantees cards stay within bounds
function solveCardPosition(
  seed: number,
  cardWidth: number,
  cardHeight: number,
  containerWidth: number,
  containerHeight: number,
  targetY: number
): CardPosition {
  const random = seededRandom(seed);
  const r1 = random();
  const r2 = random();
  const r3 = random();

  // Calculate rotation first (±15 degrees)
  const rotation = (r1 - 0.5) * 30;

  // Get the bounding box of the rotated card
  const rotatedBounds = getRotatedBoundingBox(cardWidth, cardHeight, rotation);
  const padding = 4; // Minimal padding from edges

  // Calculate safe zone for Y - where the card center can be placed vertically
  // Use rotated bounds for Y to prevent vertical overflow
  const safeZoneY = {
    minY: rotatedBounds.height / 2 + padding,
    maxY: containerHeight - rotatedBounds.height / 2 - padding,
  };

  // For X, allow significant horizontal spread for visual variety
  // Cards positioned via center point, so we need enough range to see the spread
  // Use 15% of container width as max offset from center (±75px for 500px container)
  const horizontalSpread = containerWidth * 0.15;
  const safeZoneX = {
    minX: containerWidth / 2 - horizontalSpread,
    maxX: containerWidth / 2 + horizontalSpread,
  };

  // If card is too tall, center it vertically
  if (safeZoneY.minY >= safeZoneY.maxY) {
    safeZoneY.minY = containerHeight / 2;
    safeZoneY.maxY = containerHeight / 2;
  }

  // Clamp target Y to safe zone
  const clampedY = Math.max(safeZoneY.minY, Math.min(safeZoneY.maxY, targetY));

  // Calculate X position - random within safe zone
  const xRange = safeZoneX.maxX - safeZoneX.minX;
  const centerX = containerWidth / 2;
  // Use full range for horizontal spread
  const xOffset = (r2 - 0.5) * xRange;
  const x = centerX + xOffset;

  // Add small random Y offset within safe bounds
  const yRange = safeZoneY.maxY - safeZoneY.minY;
  const yOffset = (r3 - 0.5) * Math.min(yRange * 0.15, 30);
  const y = Math.max(safeZoneY.minY, Math.min(safeZoneY.maxY, clampedY + yOffset));

  return { x, y, rotation };
}

/**
 * Component that displays milk product similarity results.
 * Shows cropped receipt images stacked with random transforms,
 * and a summary table below with merchant/product/price data.
 */
const WordSimilarity: React.FC = () => {
  const [data, setData] = useState<MilkSimilarityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formatSupport, setFormatSupport] = useState<{
    supportsAVIF: boolean;
    supportsWebP: boolean;
  } | null>(null);

  const [imageDimensions, setImageDimensions] = useState<{
    [key: string]: { width: number; height: number };
  }>({});

  const [cardDimensions, setCardDimensions] = useState<{
    [key: string]: { width: number; height: number };
  }>({});

  const [windowWidth, setWindowWidth] = useState(
    typeof window !== "undefined" ? window.innerWidth : 1024
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState<number | null>(null);

  // Track card elements for ResizeObserver
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const measureContainer = useCallback(() => {
    if (containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect();
      setContainerWidth(rect.width);
    }
  }, []);

  useEffect(() => {
    const handleResize = () => {
      setWindowWidth(window.innerWidth);
      measureContainer();
    };

    measureContainer();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [measureContainer]);

  useEffect(() => {
    if (data && !containerWidth) {
      measureContainer();
    }
  }, [data, containerWidth, measureContainer]);

  // ResizeObserver to measure actual card dimensions
  useLayoutEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const updates: { [key: string]: { width: number; height: number } } = {};

      entries.forEach((entry) => {
        const key = entry.target.getAttribute("data-receipt-key");
        if (key) {
          const { width, height } = entry.contentRect;
          if (width > 0 && height > 0) {
            updates[key] = { width, height };
          }
        }
      });

      if (Object.keys(updates).length > 0) {
        setCardDimensions((prev) => {
          // Only update if dimensions actually changed
          const hasChanges = Object.entries(updates).some(
            ([key, dims]) =>
              !prev[key] ||
              prev[key].width !== dims.width ||
              prev[key].height !== dims.height
          );
          if (hasChanges) {
            return { ...prev, ...updates };
          }
          return prev;
        });
      }
    });

    // Observe all card elements
    cardRefs.current.forEach((el) => {
      observer.observe(el);
    });

    return () => observer.disconnect();
  }, [data]);

  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await api.fetchWordSimilarity();
        setData(response);
      } catch (err) {
        console.error("Failed to fetch word similarity:", err);
        setError(err instanceof Error ? err.message : "Failed to load data");
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const handleImageLoad = useCallback((receiptKey: string) => (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (img.naturalWidth && img.naturalHeight) {
      setImageDimensions((prev) => ({
        ...prev,
        [receiptKey]: {
          width: img.naturalWidth,
          height: img.naturalHeight,
        },
      }));
    }
  }, []);

  if (loading) {
    const reservedHeight = windowWidth <= 768 ? 600 : 900;
    return (
      <div
        style={{
          padding: "2rem",
          textAlign: "center",
          color: "#666",
          minHeight: `${reservedHeight}px`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        Loading...
      </div>
    );
  }

  if (error || !data) {
    return (
      <div
        style={{
          padding: "2rem",
          textAlign: "center",
          color: "#999",
        }}
      >
        {error || "No word similarity data available"}
      </div>
    );
  }

  // Check if data has the new format (receipts array)
  if (!data.receipts || !Array.isArray(data.receipts)) {
    return (
      <div
        style={{
          padding: "2rem",
          textAlign: "center",
          color: "#999",
        }}
      >
        Waiting for cache update. Please trigger the cache generator Lambda.
      </div>
    );
  }

  const baseUrl =
    process.env.NODE_ENV === "development"
      ? "https://dev.tylernorlund.com"
      : "https://www.tylernorlund.com";

  // Helper function to calculate crop region from bbox
  const calculateCropRegion = (
    apiBbox: AddressBoundingBox | null,
    receipt: MilkReceiptData["receipt"]
  ) => {
    if (!apiBbox || !receipt?.width || !receipt?.height) {
      return null;
    }

    const leftNorm = apiBbox.tl.x;
    const rightNorm = apiBbox.tr.x;
    const topNorm = apiBbox.tl.y;
    const bottomNorm = apiBbox.bl.y;

    const widthNorm = rightNorm - leftNorm;

    const cssTopNorm = 1 - topNorm;
    const cssBottomNorm = 1 - bottomNorm;
    const cssHeightNorm = cssBottomNorm - cssTopNorm;

    return {
      leftNorm,
      rightNorm,
      widthNorm,
      topNorm,
      bottomNorm,
      cssTopNorm,
      cssBottomNorm,
      cssHeightNorm,
    };
  };

  // Render a single cropped receipt
  const renderCroppedReceipt = (receiptData: MilkReceiptData) => {
    const cropRegion = calculateCropRegion(receiptData.bbox, receiptData.receipt);
    if (!cropRegion) {
      return null;
    }

    const receipt = receiptData.receipt;
    const imageSrc = formatSupport
      ? getBestImageUrl(receipt, formatSupport, "medium")
      : receipt.cdn_medium_s3_key
        ? `${baseUrl}/${receipt.cdn_medium_s3_key}`
        : receipt.cdn_s3_key
          ? `${baseUrl}/${receipt.cdn_s3_key}`
          : null;

    if (!imageSrc) {
      return null;
    }

    const receiptKey = `${receipt.image_id}-${receipt.receipt_id}`;
    const dims = imageDimensions[receiptKey];
    const displayWidth = dims?.width ?? receipt.width;
    const displayHeight = dims?.height ?? receipt.height;
    const displayAspectRatio = displayWidth / displayHeight;

    const cropAspectRatio = cropRegion.widthNorm / cropRegion.cssHeightNorm;
    const containerAspectRatio = cropAspectRatio * displayAspectRatio;

    const imageWidthPercent = (1 / cropRegion.widthNorm) * 100;
    const shiftLeftPercent = cropRegion.leftNorm * 100;
    const shiftTopPercent = cropRegion.cssTopNorm * 100;

    return (
      <div
        style={{
          width: "100%",
          border: "2px solid #ccc",
          borderRadius: "8px",
          overflow: "hidden",
          backgroundColor: "#fff",
          boxShadow: "0 2px 4px rgba(0, 0, 0, 0.1)",
        }}
      >
        <div
          style={{
            position: "relative",
            width: "100%",
            paddingTop: `${(1 / containerAspectRatio) * 100}%`,
            overflow: "hidden",
            backgroundColor: "#f0f0f0",
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageSrc}
            alt={`${receiptData.product} from ${receiptData.merchant}`}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: `${imageWidthPercent}%`,
              height: "auto",
              transform: `translate(-${shiftLeftPercent}%, -${shiftTopPercent}%)`,
            }}
            onLoad={handleImageLoad(receiptKey)}
            onError={(e) => {
              console.error("WordSimilarity: Image failed to load:", imageSrc, e);
            }}
          />
        </div>
      </div>
    );
  };

  // Take first 8 receipts for visual display
  const displayReceipts = data.receipts.slice(0, 8);
  const containerHeight = windowWidth <= 768 ? 400 : 600;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "2rem",
        width: "100%",
        maxWidth: "1000px",
        margin: "0 auto",
        padding: 0,
      }}
    >
      {/* Receipt images stack */}
      <div
        ref={containerRef}
        style={{
          position: "relative",
          width: "100%",
          maxWidth: "500px",
          height: `${containerHeight}px`,
          margin: "0 auto",
          overflow: "visible",
        }}
      >
        {displayReceipts.map((receiptData, index) => {
          const effectiveContainerWidth = containerWidth ?? 500;
          const totalCards = displayReceipts.length;

          // Target Y position distributed evenly across container height
          const targetCenterY = ((index + 1) / (totalCards + 1)) * containerHeight;

          const receiptKey = `${receiptData.receipt.image_id}-${receiptData.receipt.receipt_id}`;

          // Get measured dimensions or use estimates
          let cardDims = cardDimensions[receiptKey];
          const hasMeasuredDimensions = !!cardDims;
          if (!cardDims) {
            // Initial estimate for first render
            const cardWidthPercent = 0.85;
            const estimatedWidth = effectiveContainerWidth * cardWidthPercent;
            const estimatedAspectRatio = 3.5;
            cardDims = { width: estimatedWidth, height: estimatedWidth / estimatedAspectRatio };
          }

          const receiptComponent = renderCroppedReceipt(receiptData);

          if (!receiptComponent) return null;

          // Generate seed for consistent randomness
          const imageIdHash = receiptData.receipt.image_id
            ? receiptData.receipt.image_id.split('').reduce((acc: number, char: string) => acc + char.charCodeAt(0), 0)
            : 0;
          const seed = (receiptData.receipt.receipt_id * 7919) + (index * 9973) + (imageIdHash * 1013) + 12345;

          // Use constraint-based solver to get position that stays in bounds
          const { x, y, rotation } = solveCardPosition(
            seed,
            cardDims.width,
            cardDims.height,
            effectiveContainerWidth,
            containerHeight,
            targetCenterY
          );

          const zIndex = displayReceipts.length - index;

          // Position card centered at (x, y) with rotation
          // Use translate(-50%, -50%) to center the card at the calculated position
          return (
            <div
              key={receiptKey}
              ref={(el) => {
                if (el) {
                  cardRefs.current.set(receiptKey, el);
                } else {
                  cardRefs.current.delete(receiptKey);
                }
              }}
              data-receipt-key={receiptKey}
              style={{
                position: "absolute",
                top: `${y}px`,
                left: `${x}px`,
                width: "85%",
                zIndex,
                transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
                transformOrigin: "center center",
                // Fade in once we have measured dimensions
                opacity: hasMeasuredDimensions ? 1 : 0.8,
                transition: "opacity 0.2s ease-in-out",
              }}
            >
              {receiptComponent}
            </div>
          );
        })}
      </div>

      {/* Summary table */}
      <div
        style={{
          width: "100%",
          overflowX: "auto",
        }}
      >
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: windowWidth <= 768 ? "0.8rem" : "0.9rem",
          }}
        >
          <thead>
            <tr style={{ backgroundColor: "var(--code-background)", borderBottom: "2px solid var(--text-color)" }}>
              {windowWidth > 768 ? (
                <>
                  <th style={{ padding: "0.75rem 0.5rem", textAlign: "left", fontWeight: 600, color: "var(--text-color)" }}>Merchant</th>
                  <th style={{ padding: "0.75rem 0.5rem", textAlign: "left", fontWeight: 600, color: "var(--text-color)" }}>Product</th>
                  <th style={{ padding: "0.75rem 0.5rem", textAlign: "left", fontWeight: 600, color: "var(--text-color)" }}>Size</th>
                  <th style={{ padding: "0.75rem 0.5rem", textAlign: "right", fontWeight: 600, color: "var(--text-color)" }}>Count</th>
                  <th style={{ padding: "0.75rem 0.5rem", textAlign: "right", fontWeight: 600, color: "var(--text-color)" }}>Avg Price</th>
                </>
              ) : (
                <th style={{ padding: "0.75rem 0.5rem", textAlign: "left", fontWeight: 600, color: "var(--text-color)" }}>Item</th>
              )}
              <th style={{ padding: "0.75rem 0.5rem", textAlign: "right", fontWeight: 600, color: "var(--text-color)" }}>Total</th>
            </tr>
          </thead>
          <tbody>
            {(() => {
              // Calculate max total per merchant for group ordering
              const merchantMaxTotal = new Map<string, number>();
              data.summary_table.forEach((row) => {
                const current = merchantMaxTotal.get(row.merchant) || 0;
                merchantMaxTotal.set(row.merchant, Math.max(current, row.total || 0));
              });

              const sorted = [...data.summary_table].sort((a, b) => {
                // Primary sort: Merchant groups by their max total (descending)
                const aMax = merchantMaxTotal.get(a.merchant) || 0;
                const bMax = merchantMaxTotal.get(b.merchant) || 0;
                const merchantDiff = bMax - aMax;
                if (merchantDiff !== 0) return merchantDiff;
                // Secondary sort: Within merchant, by total descending
                return (b.total || 0) - (a.total || 0);
              });

              let lastMerchant = "";
              return sorted.map((row, index) => {
                const showMerchant = row.merchant !== lastMerchant;
                lastMerchant = row.merchant;

                return (
              <tr
                key={`${row.merchant}-${row.product}-${row.size}`}
                style={{
                  backgroundColor: index % 2 === 0 ? "var(--background-color)" : "var(--code-background)",
                  color: "var(--text-color)",
                }}
              >
                {windowWidth > 768 ? (
                  <>
                    <td style={{
                      padding: "0.5rem",
                      maxWidth: "150px",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      fontWeight: showMerchant ? 500 : 400,
                    }}>
                      {showMerchant ? row.merchant : ""}
                    </td>
                    <td style={{ padding: "0.5rem" }}>{row.product}</td>
                    <td style={{ padding: "0.5rem" }}>{row.size}</td>
                    <td style={{ padding: "0.5rem", textAlign: "right" }}>{row.count}</td>
                    <td style={{ padding: "0.5rem", textAlign: "right" }}>
                      {row.avg_price ? `$${row.avg_price.toFixed(2)}` : "-"}
                    </td>
                  </>
                ) : (
                  <td style={{ padding: "0.5rem", verticalAlign: "middle", height: "3.5em" }}>
                    {showMerchant ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: "0.125rem" }}>
                        <span style={{ fontSize: "0.7rem", lineHeight: "1.2", opacity: 0.7 }}>
                          {row.merchant}
                        </span>
                        <span style={{ paddingLeft: "0.75rem" }}>
                          {row.product}{row.size ? ` (${row.size})` : ""}
                        </span>
                      </div>
                    ) : (
                      <div style={{ display: "flex", alignItems: "center", height: "100%" }}>
                        <span style={{ paddingLeft: "0.75rem" }}>
                          {row.product}{row.size ? ` (${row.size})` : ""}
                        </span>
                      </div>
                    )}
                  </td>
                )}
                <td style={{ padding: "0.5rem", textAlign: "right" }}>
                  {row.total ? `$${row.total.toFixed(2)}` : "-"}
                </td>
              </tr>
                );
              });
            })()}
          </tbody>
          <tfoot>
            <tr style={{ backgroundColor: "var(--background-color)", borderTop: "2px solid var(--text-color)", fontWeight: 600, color: "var(--text-color)" }}>
              <td colSpan={windowWidth > 768 ? 5 : 1} style={{ padding: "0.75rem 0.5rem" }}>Total</td>
              <td style={{ padding: "0.75rem 0.5rem", textAlign: "right" }}>
                ${data.summary_table.reduce((sum, row) => sum + (row.total || 0), 0).toFixed(2)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {/* Timing breakdown */}
      {data.timing && (
        <TimingBreakdown timing={data.timing} windowWidth={windowWidth} />
      )}
    </div>
  );
};

// Generate SVG path for a pie slice from 12 o'clock, filling clockwise
const getPieSlicePath = (progress: number, cx: number, cy: number, r: number): string => {
  if (progress <= 0) return '';
  if (progress >= 100) return `M ${cx} ${cy} m -${r} 0 a ${r} ${r} 0 1 0 ${r * 2} 0 a ${r} ${r} 0 1 0 -${r * 2} 0`;

  const angle = (progress / 100) * 2 * Math.PI;
  // Start at 12 o'clock (-π/2)
  const startAngle = -Math.PI / 2;
  const endAngle = startAngle + angle;

  const x1 = cx + r * Math.cos(startAngle);
  const y1 = cy + r * Math.sin(startAngle);
  const x2 = cx + r * Math.cos(endAngle);
  const y2 = cy + r * Math.sin(endAngle);

  const largeArcFlag = progress > 50 ? 1 : 0;

  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArcFlag} 1 ${x2} ${y2} Z`;
};

/**
 * Component that displays the timing breakdown for cache generation.
 */
const TimingBreakdown: React.FC<{
  timing: MilkSimilarityTiming;
  windowWidth: number;
}> = ({ timing, windowWidth }) => {
  const [progress, setProgress] = useState(0);
  const [hasAnimated, setHasAnimated] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Animation duration - scale down for reasonable viewing (e.g., 33s -> 3.3s)
  const animationDuration = Math.min(timing.total_ms / 10, 5000); // Cap at 5 seconds

  // Intersection observer to trigger animation when visible
  useEffect(() => {
    if (hasAnimated) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !hasAnimated) {
          setHasAnimated(true);
          const startTime = performance.now();

          const animate = (currentTime: number) => {
            const elapsed = currentTime - startTime;
            const newProgress = Math.min((elapsed / animationDuration) * 100, 100);
            setProgress(newProgress);

            if (newProgress < 100) {
              requestAnimationFrame(animate);
            }
          };

          requestAnimationFrame(animate);
        }
      },
      { threshold: 0.3 }
    );

    if (containerRef.current) {
      observer.observe(containerRef.current);
    }

    return () => observer.disconnect();
  }, [animationDuration, hasAnimated]);

  const formatMs = (ms: number): string => {
    if (ms >= 1000) {
      return `${(ms / 1000).toFixed(2)}s`;
    }
    return `${ms.toFixed(1)}ms`;
  };

  const calculatePercent = (ms: number): string => {
    return `${((ms / timing.total_ms) * 100).toFixed(1)}%`;
  };

  // Build timing steps using CSS color variables
  const steps: Array<{ name: string; ms: number; color: string }> = [
    { name: "S3 Download", ms: timing.s3_download_ms, color: "var(--color-green)" },
    { name: "ChromaDB Init", ms: timing.chromadb_init_ms, color: "var(--color-blue)" },
    { name: "ChromaDB Fetch", ms: timing.chromadb_fetch_all_ms, color: "var(--color-purple)" },
    { name: "Filter Lines", ms: timing.filter_lines_ms, color: "var(--color-yellow)" },
    { name: "DynamoDB Queries", ms: timing.dynamo_fetch_total_ms, color: "var(--color-orange)" },
  ];

  // Calculate "other" time (upload, processing, etc.)
  const accountedTime = steps.reduce((sum, step) => sum + step.ms, 0);
  const otherTime = timing.total_ms - accountedTime;
  if (otherTime > 0) {
    steps.push({ name: "Other", ms: otherTime, color: "var(--color-red)" });
  }

  // Calculate cumulative percentages for each step
  const stepsWithCumulative = steps.map((step, index) => {
    const cumulativeMs = steps.slice(0, index + 1).reduce((sum, s) => sum + s.ms, 0);
    const cumulativePercent = (cumulativeMs / timing.total_ms) * 100;
    const startPercent = index === 0 ? 0 : (steps.slice(0, index).reduce((sum, s) => sum + s.ms, 0) / timing.total_ms) * 100;
    return { ...step, cumulativePercent, startPercent };
  });

  // Current displayed time based on progress
  const displayedTime = (progress / 100) * timing.total_ms;

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        padding: "1rem",
        backgroundColor: "var(--code-background)",
        borderRadius: "8px",
        fontSize: windowWidth <= 768 ? "0.75rem" : "0.85rem",
        boxSizing: "border-box",
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: "0.75rem", color: "var(--text-color)" }}>
        Document Retrieval: {formatMs(displayedTime)}
      </div>

      {/* Progress bar visualization */}
      <div
        style={{
          position: "relative",
          width: "100%",
          height: "24px",
          marginBottom: "0.75rem",
          borderRadius: "4px",
          overflow: "hidden",
        }}
      >
        {/* Background (unfilled) */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            backgroundColor: "var(--background-color)",
            borderRadius: "4px",
          }}
        />
        {/* Colored segments */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            display: "flex",
            width: "100%",
            height: "100%",
            clipPath: `inset(0 ${100 - progress}% 0 0)`,
          }}
        >
          {stepsWithCumulative.map((step) => {
            const widthPercent = (step.ms / timing.total_ms) * 100;

            return (
              <div
                key={step.name}
                title={`${step.name}: ${formatMs(step.ms)} (${calculatePercent(step.ms)})`}
                style={{
                  width: `${widthPercent}%`,
                  height: "100%",
                  backgroundColor: step.color,
                  flexShrink: 0,
                }}
              />
            );
          })}
        </div>
      </div>

      {/* Legend */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: windowWidth <= 768 ? "repeat(3, 1fr)" : "repeat(auto-fit, minmax(140px, auto))",
          gap: windowWidth <= 768 ? "0.25rem 0.5rem" : "0.5rem 1.5rem",
        }}
      >
        {stepsWithCumulative.map((step, index) => {
          // Calculate circle fill progress (0-100 within this step)
          let circleFill = 0;
          if (progress >= step.cumulativePercent) {
            circleFill = 100;
          } else if (progress > step.startPercent) {
            const segmentProgress = progress - step.startPercent;
            const segmentSize = step.cumulativePercent - step.startPercent;
            circleFill = (segmentProgress / segmentSize) * 100;
          }

          // Check if previous steps are complete (for opacity)
          const previousStepsComplete = index === 0 || progress >= stepsWithCumulative[index - 1].cumulativePercent;
          const isActive = progress > step.startPercent && progress < step.cumulativePercent;
          const isComplete = progress >= step.cumulativePercent;

          const circleSize = windowWidth <= 768 ? 10 : 14;

          return (
            <div
              key={step.name}
              style={{
                display: "flex",
                alignItems: "center",
                gap: windowWidth <= 768 ? "0.25rem" : "0.5rem",
                color: "var(--text-color)",
                opacity: isComplete ? 1 : isActive ? 1 : previousStepsComplete ? 0.6 : 0.3,
                transition: "opacity 0.15s ease",
              }}
            >
              <svg
                width={circleSize}
                height={circleSize}
                viewBox="0 0 14 14"
                style={{ flexShrink: 0 }}
              >
                {/* Background circle (unfilled outline) */}
                <circle
                  cx="7"
                  cy="7"
                  r="6"
                  fill="none"
                  stroke={step.color}
                  strokeWidth="1.5"
                  opacity={isComplete || isActive ? "0.3" : "0.5"}
                />
                {/* Pie slice fill - grows clockwise from 12 o'clock */}
                {circleFill > 0 && (
                  <path
                    d={getPieSlicePath(circleFill, 7, 7, 6)}
                    fill={step.color}
                  />
                )}
              </svg>
              <span style={{ fontSize: windowWidth <= 768 ? "0.7rem" : "0.85rem", fontWeight: 500 }}>{step.name}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default WordSimilarity;
