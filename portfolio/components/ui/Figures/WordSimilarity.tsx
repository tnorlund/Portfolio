import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../../../services/api";
import { AddressBoundingBox, MilkSimilarityResponse, MilkReceiptData } from "../../../types/api";
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
    </div>
  );
};

export default WordSimilarity;
