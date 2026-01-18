import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../../services/api";
import { AddressBoundingBox, AddressSimilarityResponse } from "../../../types/api";
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

  // Rotated bounding box dimensions
  const rotatedWidth = width * cos + height * sin;
  const rotatedHeight = width * sin + height * cos;

  return { width: rotatedWidth, height: rotatedHeight };
}

interface CardPosition {
  x: number;
  y: number;
  rotation: number;
}

// Constraint-based position solver that guarantees cards stay within bounds
// (Same approach as WordSimilarity component)
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

  // For X, spread cards across the container width
  // Account for rotated card bounds so cards stay within container
  const rotatedHalfWidth = rotatedBounds.width / 2;
  const safeZoneX = {
    minX: rotatedHalfWidth + padding,
    maxX: containerWidth - rotatedHalfWidth - padding,
  };

  // If card is too tall, center it vertically
  if (safeZoneY.minY >= safeZoneY.maxY) {
    safeZoneY.minY = containerHeight / 2;
    safeZoneY.maxY = containerHeight / 2;
  }

  // Clamp target Y to safe zone
  const clampedY = Math.max(safeZoneY.minY, Math.min(safeZoneY.maxY, targetY));

  // Calculate X position - random within safe zone width
  const xRange = safeZoneX.maxX - safeZoneX.minX;
  const x = safeZoneX.minX + r2 * xRange;

  // Add small random Y offset within safe bounds
  const yRange = safeZoneY.maxY - safeZoneY.minY;
  const yOffset = (r3 - 0.5) * Math.min(yRange * 0.15, 30);
  const y = Math.max(safeZoneY.minY, Math.min(safeZoneY.maxY, clampedY + yOffset));

  return { x, y, rotation };
}

/**
 * Component that displays the original receipt on the left
 * and similar receipts stacked on the right, using CSS-only cropping.
 */
const AddressSimilaritySideBySide: React.FC = () => {
  const [data, setData] = useState<AddressSimilarityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formatSupport, setFormatSupport] = useState<{
    supportsAVIF: boolean;
    supportsWebP: boolean;
  } | null>(null);

  // Track actual loaded image dimensions for each receipt
  const [imageDimensions, setImageDimensions] = useState<{
    [key: string]: { width: number; height: number };
  }>({});

  // Track card dimensions for bounds checking
  const [cardDimensions, setCardDimensions] = useState<{
    [key: number]: { width: number; height: number };
  }>({});

  // Track window resize to adjust height on mobile
  const [windowWidth, setWindowWidth] = useState(
    typeof window !== "undefined" ? window.innerWidth : 1024
  );

  // Track actual container width for accurate positioning calculations
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState<number | null>(null);

  // Measure container dimensions
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

    // Initial measurement
    measureContainer();

    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [measureContainer]);

  // Measure container when data becomes available
  useEffect(() => {
    if (data && !containerWidth) {
      measureContainer();
    }
  }, [data, containerWidth, measureContainer]);

  // Detect image format support
  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  // Fetch address similarity data
  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await api.fetchAddressSimilarity();
        setData(response);
      } catch (err) {
        console.error("Failed to fetch address similarity:", err);
        setError(err instanceof Error ? err.message : "Failed to load data");
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  // Handle image load to get actual dimensions
  const handleImageLoad = useCallback((receiptId: number) => (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (img.naturalWidth && img.naturalHeight) {
      setImageDimensions((prev) => ({
        ...prev,
        [receiptId]: {
          width: img.naturalWidth,
          height: img.naturalHeight,
        },
      }));
    }
  }, []);

  if (loading) {
    // Reserve space to prevent layout shift - match final component height
    const reservedHeight = windowWidth <= 768 ? 280 : 450;
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
        {error || "No address similarity data available"}
      </div>
    );
  }

  const baseUrl =
    process.env.NODE_ENV === "development"
      ? "https://dev.tylernorlund.com"
      : "https://www.tylernorlund.com";

  // Helper function to calculate crop region from bbox or lines
  const calculateCropRegion = (
    apiBbox: AddressBoundingBox | undefined,
    lines: any[] | undefined,
    receipt: any
  ) => {
    // If API provides bbox, use it directly
    if (apiBbox && receipt?.width && receipt?.height) {
      const leftNorm = apiBbox.tl.x;
      const rightNorm = apiBbox.tr.x;
      const topNorm = apiBbox.tl.y;
      const bottomNorm = apiBbox.bl.y;

      const widthNorm = rightNorm - leftNorm;
      const heightNorm = topNorm - bottomNorm;

      const cssTopNorm = 1 - topNorm;
      const cssBottomNorm = 1 - bottomNorm;
      const cssHeightNorm = cssBottomNorm - cssTopNorm;

      return {
        leftNorm,
        rightNorm,
        widthNorm,
        topNorm,
        bottomNorm,
        heightNorm,
        cssTopNorm,
        cssBottomNorm,
        cssHeightNorm,
      };
    }

    // Fallback: Calculate from lines
    if (!lines || lines.length === 0) {
      return null;
    }

    const allX = lines.flatMap((line: any) => [
      line.top_left.x,
      line.top_right.x,
      line.bottom_left.x,
      line.bottom_right.x,
    ]);
    const allYBottom = lines.flatMap((line: any) => [
      line.bottom_left.y,
      line.bottom_right.y,
    ]);
    const allYTop = lines.flatMap((line: any) => [
      line.top_left.y,
      line.top_right.y,
    ]);

    const minX = Math.min(...allX);
    const maxX = Math.max(...allX);
    const minY = Math.min(...allYBottom);
    const maxY = Math.max(...allYTop);

    const paddingX = (maxX - minX) * 0.05;
    const paddingY = Math.max((maxY - minY) * 0.05, 0.02);

    const leftNorm = Math.max(0, minX - paddingX);
    const rightNorm = Math.min(1, maxX + paddingX);
    const bottomNorm = Math.max(0, minY - paddingY);
    const topNorm = Math.min(1, maxY + paddingY);

    const widthNorm = rightNorm - leftNorm;
    const heightNorm = topNorm - bottomNorm;

    const cssTopNorm = 1 - topNorm;
    const cssBottomNorm = 1 - bottomNorm;
    const cssHeightNorm = cssBottomNorm - cssTopNorm;

    return {
      leftNorm,
      rightNorm,
      widthNorm,
      topNorm,
      bottomNorm,
      heightNorm,
      cssTopNorm,
      cssBottomNorm,
      cssHeightNorm,
    };
  };

  // Render a single cropped receipt
  const renderCroppedReceipt = (
    receipt: any,
    lines: any[],
    apiBbox: AddressBoundingBox | undefined,
    receiptId: number,
    label?: string,
    noMargin?: boolean,
    hideLabel?: boolean,
    hideText?: boolean,
    onDimensionsReady?: (width: number, height: number) => void
  ) => {
    const cropRegion = calculateCropRegion(apiBbox, lines, receipt);
    if (!cropRegion) {
      return null;
    }

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

    const dims = imageDimensions[receiptId];
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
          // Measure card dimensions once when element is mounted
          if (el && onDimensionsReady) {
            // Use a flag on the element to track if we've measured
            if (!(el as any).__measured) {
              (el as any).__measured = true;
              // Measure after layout is complete
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
          marginBottom: noMargin ? "0" : "1rem",
          boxShadow: "0 2px 4px rgba(0, 0, 0, 0.1)",
        }}
      >
        {label && !hideLabel && (
          <div
            style={{
              padding: "0.5rem",
              backgroundColor: "#f5f5f5",
              borderBottom: "1px solid #ddd",
              fontSize: "0.875rem",
              fontWeight: "500",
            }}
          >
            {label}
          </div>
        )}
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
            alt={label || "Cropped receipt"}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: `${imageWidthPercent}%`,
              height: "auto",
              transform: `translate(-${shiftLeftPercent}%, -${shiftTopPercent}%)`,
              boxShadow: "0 4px 6px rgba(0, 0, 0, 0.1), 0 2px 4px rgba(0, 0, 0, 0.06)",
            }}
            onLoad={handleImageLoad(receiptId)}
            onError={(e) => {
              console.error("AddressSimilaritySideBySide: Image failed to load:", imageSrc, e);
            }}
          />
        </div>
        {lines && lines.length > 0 && !hideText && (
          <div
            style={{
              padding: "0.75rem",
              backgroundColor: "#fff",
            }}
          >
            <p style={{ fontSize: "0.875rem", color: "#666", margin: 0 }}>
              {lines.map((line: any) => line.text).join(" ")}
            </p>
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        gap: "1.5rem",
        width: "100%",
        maxWidth: "900px",
        margin: "0 auto",
        padding: 0,
      }}
    >
      {/* Original receipt on the left */}
      <div
        style={{
          flex: "0 0 45%",
          minWidth: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {renderCroppedReceipt(
          data.original.receipt,
          data.original.lines,
          data.original.bbox,
          data.original.receipt.receipt_id,
          "Original",
          false, // noMargin
          true,  // hideLabel
          true   // hideText
        )}
      </div>

      {/* Similar receipts stacked on the right */}
      <div
        style={{
          flex: "0 0 45%",
          minWidth: 0,
        }}
      >
        {/* Stack container */}
        <div
          ref={containerRef}
          style={{
            position: "relative",
            width: "100%",
            height: windowWidth <= 768 ? "280px" : "450px",
            overflow: "visible",
          }}
        >
          {data.similar.map((similar, index) => {
            // Distribute cards evenly across the height of the container
            const containerHeight = windowWidth <= 768 ? 280 : 450;

            // Use measured container width if available, otherwise calculate responsive estimate
            const effectiveContainerWidth = containerWidth ?? (windowWidth <= 768 ? 180 : 405);

            const totalCards = data.similar.length;

            // Target Y position distributed evenly across container height
            const targetCenterY = ((index + 1) / (totalCards + 1)) * containerHeight;

            // Card width: responsive sizing
            const cardWidth = windowWidth <= 768
              ? Math.min(effectiveContainerWidth * 0.9, 180)
              : Math.min(effectiveContainerWidth * 0.85, 350);

            // Get card dimensions (use stored or estimate)
            let cardDims = cardDimensions[similar.receipt.receipt_id];
            if (!cardDims) {
              const estimatedAspectRatio = 3.5;
              cardDims = { width: cardWidth, height: cardWidth / estimatedAspectRatio };
            }

            const receiptComponent = renderCroppedReceipt(
              similar.receipt,
              similar.lines,
              similar.bbox,
              similar.receipt.receipt_id,
              `Similar #${index + 1} (distance: ${similar.similarity_distance.toFixed(4)})`,
              true, // noMargin for stacked cards
              true, // hideLabel for stacked cards
              true, // hideText for stacked cards
              (width, height) => {
                setCardDimensions(prev => {
                  const existing = prev[similar.receipt.receipt_id];
                  if (!existing || existing.width !== width || existing.height !== height) {
                    return {
                      ...prev,
                      [similar.receipt.receipt_id]: { width, height }
                    };
                  }
                  return prev;
                });
              }
            );

            if (!receiptComponent) return null;

            // Generate seed for consistent randomness
            const imageIdHash = similar.receipt.image_id
              ? similar.receipt.image_id.split('').reduce((acc: number, char: string) => acc + char.charCodeAt(0), 0)
              : 0;
            const seed = (similar.receipt.receipt_id * 7919) + (index * 9973) + (imageIdHash * 1013) + 12345;

            // Use constraint-based solver to get position that stays in bounds
            const { x, y, rotation } = solveCardPosition(
              seed,
              cardDims.width,
              cardDims.height,
              effectiveContainerWidth,
              containerHeight,
              targetCenterY
            );

            const zIndex = data.similar.length - index;

            // Position card centered at (x, y) with rotation
            return (
              <div
                key={similar.receipt.receipt_id}
                style={{
                  position: "absolute",
                  top: `${y}px`,
                  left: `${x}px`,
                  width: `${cardWidth}px`,
                  zIndex,
                  transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
                  transformOrigin: "center center",
                }}
              >
                {receiptComponent}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default AddressSimilaritySideBySide;

