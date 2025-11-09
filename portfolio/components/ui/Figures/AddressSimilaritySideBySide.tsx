import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { api } from "../../../services/api";
import { AddressSimilarityResponse, BoundingBox } from "../../../types/api";
import {
  detectImageFormatSupport,
} from "../../../utils/imageFormat";
import { getBestImageUrl } from "../../../utils/imageFormat";

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

// Generate random transform values with better distribution, constrained to container bounds
function getRandomTransform(
  seed: number,
  cardWidth: number,
  cardHeight: number,
  containerWidth: number,
  containerHeight: number,
  topPosition: number
): { rotation: number; translateX: number; translateY: number } {
  const random = seededRandom(seed);

  // Use multiple random calls to get better distribution
  const r1 = random();
  const r2 = random();
  const r3 = random();

  // Rotation: -15 to 15 degrees with better distribution
  const rotation = (r1 - 0.5) * 30;

  // Calculate rotated bounding box to account for rotation
  const rotatedBounds = getRotatedBoundingBox(cardWidth, cardHeight, rotation);

  // Shadow adds extra space (typically 8-16px on all sides for box-shadow)
  const shadowPadding = 16;

  // Calculate maximum safe translation
  // We need to ensure the rotated card + shadow stays within bounds
  const maxTranslateX = Math.max(0, (containerWidth - rotatedBounds.width) / 2 - shadowPadding);
  const maxTranslateY = Math.max(0, (containerHeight - rotatedBounds.height) / 2 - shadowPadding);

  // Also need to account for the card's position from top
  // The card is positioned at topPosition, so we need to ensure it doesn't go above or below
  // Account for rotated bounding box height, not just card height
  const cardCenterY = topPosition + (cardHeight / 2);
  const rotatedHalfHeight = rotatedBounds.height / 2;

  // Calculate how far the rotated card can move up/down while staying in bounds
  const distanceFromTop = cardCenterY - rotatedHalfHeight;
  const distanceFromBottom = containerHeight - cardCenterY - rotatedHalfHeight;

  // Clamp translateY to keep rotated card within vertical bounds
  // Use smaller range to keep cards closer to their target centers
  const maxTranslateYUp = Math.max(0, Math.min(30, distanceFromTop - shadowPadding));
  const maxTranslateYDown = Math.max(0, Math.min(30, distanceFromBottom - shadowPadding));

  // Translation: constrained to safe bounds, but keep it smaller for better spacing
  const translateX = Math.max(-maxTranslateX, Math.min(maxTranslateX, (r2 - 0.5) * maxTranslateX * 2));
  const translateY = Math.max(
    -maxTranslateYUp,
    Math.min(maxTranslateYDown, (r3 - 0.5) * (maxTranslateYUp + maxTranslateYDown))
  );

  return { rotation, translateX, translateY };
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
    return (
      <div
        style={{
          padding: "2rem",
          textAlign: "center",
          color: "#666",
        }}
      >
        Loading address similarity...
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
    apiBbox: BoundingBox | undefined,
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
    apiBbox: BoundingBox | undefined,
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
        maxWidth: "1200px",
        margin: "0 auto",
        padding: "1rem",
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
          flex: "1 1 auto",
          minWidth: 0,
        }}
      >
        {/* Stack container */}
        <div
          style={{
            position: "relative",
            width: "100%",
            height: "700px",
            overflow: "visible",
          }}
        >
          {data.similar.map((similar, index) => {
            // Distribute cards evenly across the height of the container (700px)
            // Position each card's center at (n+1)/(N+1) of the container height
            const containerHeight = 700;
            const containerWidth = 600; // Approximate width of the right column
            const totalCards = data.similar.length;

            // Calculate target center Y position: (index + 1) / (totalCards + 1) of container height
            const targetCenterY = ((index + 1) / (totalCards + 1)) * containerHeight;

            // Get card dimensions (use stored or estimate based on aspect ratio)
            // Estimate height based on container aspect ratio if not measured yet
            let cardDims = cardDimensions[similar.receipt.receipt_id];
            if (!cardDims) {
              // Estimate based on typical card width and aspect ratio
              // Cards are typically around 400-500px wide, height depends on crop aspect ratio
              const estimatedWidth = 450;
              // Use a reasonable default aspect ratio (most address crops are wide)
              const estimatedAspectRatio = 3.5; // width/height ratio
              cardDims = { width: estimatedWidth, height: estimatedWidth / estimatedAspectRatio };
            }

            // Position card so its center is at targetCenterY
            // topPosition = targetCenterY - (cardHeight / 2)
            const topPosition = targetCenterY - (cardDims.height / 2);

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
                // Update card dimensions when they're known (only if changed)
                setCardDimensions(prev => {
                  const existing = prev[similar.receipt.receipt_id];
                  // Only update if dimensions are different or don't exist
                  if (!existing || existing.width !== width || existing.height !== height) {
                    return {
                      ...prev,
                      [similar.receipt.receipt_id]: { width, height }
                    };
                  }
                  return prev; // No change, return same object
                });
              }
            );

            if (!receiptComponent) return null;

            // Generate consistent random values based on receipt ID and index
            // Use both receipt_id and index with a large multiplier to ensure variety
            // Also mix in image_id hash if available for more entropy
            const imageIdHash = similar.receipt.image_id
              ? similar.receipt.image_id.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0)
              : 0;
            const seed = (similar.receipt.receipt_id * 7919) + (index * 9973) + (imageIdHash * 1013);

            const { rotation, translateX, translateY } = getRandomTransform(
              seed,
              cardDims.width,
              cardDims.height,
              containerWidth,
              containerHeight,
              topPosition
            );

            // Horizontal offset for depth effect (smaller than before since we're spreading vertically)
            const horizontalOffset = index * 4; // 4px offset per card

            const zIndex = data.similar.length - index; // Higher z-index for cards on top

            // Base transform with random rotation and translation
            // translateY is already constrained by bounds checking, so we can apply it directly
            const baseTransform = `translate(${translateX + horizontalOffset}px, ${translateY}px) rotate(${rotation}deg)`;

            return (
              <div
                key={similar.receipt.receipt_id}
                style={{
                  position: "absolute",
                  top: `${topPosition}px`,
                  left: `${horizontalOffset}px`,
                  right: `${horizontalOffset}px`,
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
      </div>
    </div>
  );
};

export default AddressSimilaritySideBySide;

