import React, { useCallback, useEffect, useRef, useState } from "react";
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

// Generate random transform values with better distribution
function getRandomTransform(
  seed: number,
  cardWidth: number,
  cardHeight: number,
  containerWidth: number,
  containerHeight: number,
  topPosition: number
): { rotation: number; translateX: number; translateY: number } {
  const random = seededRandom(seed);

  const r1 = random();
  const r2 = random();
  const r3 = random();

  const rotation = (r1 - 0.5) * 30;
  const rotatedBounds = getRotatedBoundingBox(cardWidth, cardHeight, rotation);
  const shadowPadding = 16;

  const maxTranslateX = Math.max(0, (containerWidth - rotatedBounds.width) / 2 - shadowPadding);
  const maxTranslateY = Math.max(0, (containerHeight - rotatedBounds.height) / 2 - shadowPadding);

  const cardCenterY = topPosition + (cardHeight / 2);
  const rotatedHalfHeight = rotatedBounds.height / 2;

  const distanceFromTop = cardCenterY - rotatedHalfHeight;
  const distanceFromBottom = containerHeight - cardCenterY - rotatedHalfHeight;

  const maxTranslateYUp = Math.max(0, Math.min(30, distanceFromTop - shadowPadding));
  const maxTranslateYDown = Math.max(0, Math.min(30, distanceFromBottom - shadowPadding));

  const translateX = Math.max(-maxTranslateX, Math.min(maxTranslateX, (r2 - 0.5) * maxTranslateX * 2));
  const translateY = Math.max(
    -maxTranslateYUp,
    Math.min(maxTranslateYDown, (r3 - 0.5) * (maxTranslateYUp + maxTranslateYDown))
  );

  return { rotation, translateX, translateY };
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
  const renderCroppedReceipt = (
    receiptData: MilkReceiptData,
    index: number,
    onDimensionsReady?: (width: number, height: number) => void
  ) => {
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
        ref={(el) => {
          if (el && onDimensionsReady) {
            if (!(el as any).__measured) {
              (el as any).__measured = true;
              requestAnimationFrame(() => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                  onDimensionsReady(rect.width, rect.height);
                }
              });
            }
          }
        }}
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
              boxShadow: "0 4px 6px rgba(0, 0, 0, 0.1), 0 2px 4px rgba(0, 0, 0, 0.06)",
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

          const targetCenterY = ((index + 1) / (totalCards + 1)) * containerHeight;

          const receiptKey = `${receiptData.receipt.image_id}-${receiptData.receipt.receipt_id}`;
          let cardDims = cardDimensions[receiptKey];
          if (!cardDims) {
            const cardWidthPercent = 0.85;
            const estimatedWidth = effectiveContainerWidth * cardWidthPercent;
            const estimatedAspectRatio = 3.5;
            cardDims = { width: estimatedWidth, height: estimatedWidth / estimatedAspectRatio };
          }

          const topPosition = targetCenterY - (cardDims.height / 2);

          const receiptComponent = renderCroppedReceipt(
            receiptData,
            index,
            (width, height) => {
              setCardDimensions(prev => {
                const existing = prev[receiptKey];
                if (!existing || existing.width !== width || existing.height !== height) {
                  return { ...prev, [receiptKey]: { width, height } };
                }
                return prev;
              });
            }
          );

          if (!receiptComponent) return null;

          const imageIdHash = receiptData.receipt.image_id
            ? receiptData.receipt.image_id.split('').reduce((acc: number, char: string) => acc + char.charCodeAt(0), 0)
            : 0;
          const seed = (receiptData.receipt.receipt_id * 7919) + (index * 9973) + (imageIdHash * 1013) + 12345;

          const { rotation, translateX, translateY } = getRandomTransform(
            seed,
            cardDims.width,
            cardDims.height,
            effectiveContainerWidth,
            containerHeight,
            topPosition
          );

          const zIndex = displayReceipts.length - index;
          const baseTransform = `translate(${translateX}px, ${translateY}px) rotate(${rotation}deg)`;

          return (
            <div
              key={receiptKey}
              style={{
                position: "absolute",
                top: `${topPosition}px`,
                left: "0px",
                right: "0px",
                zIndex,
                transform: baseTransform,
                transformOrigin: "center center",
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
            fontSize: windowWidth <= 768 ? "0.75rem" : "0.875rem",
          }}
        >
          <thead>
            <tr style={{ backgroundColor: "#f5f5f5", borderBottom: "2px solid #ddd" }}>
              <th style={{ padding: "0.75rem 0.5rem", textAlign: "left", fontWeight: 600 }}>Merchant</th>
              <th style={{ padding: "0.75rem 0.5rem", textAlign: "left", fontWeight: 600 }}>Product</th>
              <th style={{ padding: "0.75rem 0.5rem", textAlign: "left", fontWeight: 600 }}>Size</th>
              <th style={{ padding: "0.75rem 0.5rem", textAlign: "right", fontWeight: 600 }}>Count</th>
              <th style={{ padding: "0.75rem 0.5rem", textAlign: "right", fontWeight: 600 }}>Avg Price</th>
              <th style={{ padding: "0.75rem 0.5rem", textAlign: "right", fontWeight: 600 }}>Total</th>
            </tr>
          </thead>
          <tbody>
            {data.summary_table.map((row, index) => (
              <tr
                key={`${row.merchant}-${row.product}-${row.size}`}
                style={{
                  backgroundColor: index % 2 === 0 ? "#fff" : "#fafafa",
                  borderBottom: "1px solid #eee",
                }}
              >
                <td style={{ padding: "0.5rem", maxWidth: "150px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {row.merchant}
                </td>
                <td style={{ padding: "0.5rem" }}>{row.product}</td>
                <td style={{ padding: "0.5rem" }}>{row.size}</td>
                <td style={{ padding: "0.5rem", textAlign: "right" }}>{row.count}</td>
                <td style={{ padding: "0.5rem", textAlign: "right" }}>
                  {row.avg_price ? `$${row.avg_price.toFixed(2)}` : "-"}
                </td>
                <td style={{ padding: "0.5rem", textAlign: "right" }}>
                  {row.total ? `$${row.total.toFixed(2)}` : "-"}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ backgroundColor: "#f5f5f5", borderTop: "2px solid #ddd", fontWeight: 600 }}>
              <td colSpan={3} style={{ padding: "0.75rem 0.5rem" }}>Total</td>
              <td style={{ padding: "0.75rem 0.5rem", textAlign: "right" }}>
                {data.summary_table.reduce((sum, row) => sum + row.count, 0)}
              </td>
              <td style={{ padding: "0.75rem 0.5rem", textAlign: "right" }}>-</td>
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
