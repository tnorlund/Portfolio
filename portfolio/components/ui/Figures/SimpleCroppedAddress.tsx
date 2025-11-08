import React, { useMemo, useState, useCallback } from "react";
import { Receipt, Line, BoundingBox } from "../../../types/api";
import { getBestImageUrl } from "../../../utils/imageFormat";

interface SimpleCroppedAddressProps {
  receipt: Receipt;
  lines: Line[];
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean } | null;
  maxWidth?: string;
  bbox?: BoundingBox;
}

/**
 * Simple CSS-only cropped view component.
 *
 * Method: Wrap image in container sized to crop region, hide overflow,
 * position image absolutely with negative offset to show desired region.
 */
const SimpleCroppedAddress: React.FC<SimpleCroppedAddressProps> = ({
  receipt,
  lines,
  formatSupport,
  maxWidth = "400px",
  bbox: apiBbox,
}) => {
  // Track actual loaded image dimensions (may differ from receipt.width/height for medium images)
  const [imageDimensions, setImageDimensions] = useState<{
    width: number;
    height: number;
  } | null>(null);

  // Use bbox from API if available, otherwise calculate from lines
  const bbox = useMemo(() => {
    if (!receipt.width || !receipt.height) {
      return null;
    }

    // If API provides bbox, use it directly
    if (apiBbox) {
      // Bounding box in normalized coordinates (OCR space)
      const leftNorm = apiBbox.tl.x;
      const rightNorm = apiBbox.tr.x;
      const topNorm = apiBbox.tl.y; // Top edge in OCR space (y=0 at bottom)
      const bottomNorm = apiBbox.bl.y; // Bottom edge in OCR space

      // Convert to pixels
      const leftPx = leftNorm * receipt.width;
      const rightPx = rightNorm * receipt.width;
      const widthPx = rightPx - leftPx;

      // Convert OCR Y (bottom=0) to CSS Y (top=0)
      const topPx = receipt.height - topNorm * receipt.height;
      const bottomPx = receipt.height - bottomNorm * receipt.height;
      const heightPx = bottomPx - topPx;

      return {
        left: leftPx,
        top: topPx,
        width: widthPx,
        height: heightPx,
      };
    }

    // Fallback: Calculate from lines (for backward compatibility)
    if (!lines || lines.length === 0) {
      return null;
    }

    // Get all corner points
    const allX = lines.flatMap((line) => [
      line.top_left.x,
      line.top_right.x,
      line.bottom_left.x,
      line.bottom_right.x,
    ]);
    const allYBottom = lines.flatMap((line) => [
      line.bottom_left.y,
      line.bottom_right.y,
    ]);
    const allYTop = lines.flatMap((line) => [
      line.top_left.y,
      line.top_right.y,
    ]);

    // Bounds in normalized coordinates (0-1)
    const minX = Math.min(...allX);
    const maxX = Math.max(...allX);
    const minY = Math.min(...allYBottom); // Bottom edge (OCR: y=0 at bottom)
    const maxY = Math.max(...allYTop); // Top edge (OCR: y=1 at top)

    // Add padding
    const paddingX = (maxX - minX) * 0.05;
    const paddingY = Math.max((maxY - minY) * 0.05, 0.02);

    // Bounding box in normalized coordinates
    const leftNorm = Math.max(0, minX - paddingX);
    const rightNorm = Math.min(1, maxX + paddingX);
    const bottomNorm = Math.max(0, minY - paddingY);
    const topNorm = Math.min(1, maxY + paddingY);

    // Convert to pixels
    const leftPx = leftNorm * receipt.width;
    const rightPx = rightNorm * receipt.width;
    const widthPx = rightPx - leftPx;

    // Convert OCR Y (bottom=0) to CSS Y (top=0)
    const topPx = receipt.height - topNorm * receipt.height;
    const bottomPx = receipt.height - bottomNorm * receipt.height;
    const heightPx = bottomPx - topPx;

    return {
      left: leftPx,
      top: topPx,
      width: widthPx,
      height: heightPx,
    };
  }, [lines, receipt.width, receipt.height, apiBbox]);

  // Get image URL
  const baseUrl =
    process.env.NODE_ENV === "development"
      ? "https://dev.tylernorlund.com"
      : "https://www.tylernorlund.com";
  const imageSrc = formatSupport
    ? getBestImageUrl(receipt, formatSupport, "medium")
    : receipt.cdn_medium_s3_key
      ? `${baseUrl}/${receipt.cdn_medium_s3_key}`
      : receipt.cdn_s3_key
        ? `${baseUrl}/${receipt.cdn_s3_key}`
        : null;

  if (!imageSrc || !bbox) {
    return (
      <div
        style={{
          width: "100%",
          maxWidth,
          padding: "2rem",
          border: "2px solid #ccc",
          borderRadius: "8px",
          backgroundColor: "#f9f9f9",
          textAlign: "center",
        }}
      >
        <p style={{ color: "#999", margin: 0 }}>Image not available</p>
      </div>
    );
  }

  // Container aspect ratio matches bounding box
  const aspectRatio = bbox.width / bbox.height;

  // Use actual loaded image dimensions if available, otherwise fall back to receipt dimensions
  // This is important because medium images may have different dimensions than the original
  const displayWidth = imageDimensions?.width ?? receipt.width;
  const displayHeight = imageDimensions?.height ?? receipt.height;

  // Scale factor: how much to scale image so crop region fills container
  // The bbox is in pixels relative to receipt.width/height, but we need to scale relative to display dimensions
  // Convert bbox to normalized coordinates first, then scale based on display dimensions
  const bboxWidthNorm = bbox.width / receipt.width;
  const bboxHeightNorm = bbox.height / receipt.height;

  // Scale needed to make crop region fill container
  const scaleX = 1 / bboxWidthNorm;  // If bbox is 50% of image width, scale by 2x
  const scaleY = 1 / bboxHeightNorm;  // If bbox is 10% of image height, scale by 10x
  // Use the larger scale to ensure crop region fills container
  let scale = Math.max(scaleX, scaleY);

  // Cap scale to prevent extreme values that break rendering
  const MAX_SCALE = 20; // Cap at 20x to prevent rendering issues
  if (scale > MAX_SCALE) {
    console.warn(`Scale ${scale.toFixed(2)}x exceeds max ${MAX_SCALE}x, capping. BBox: ${bbox.width.toFixed(0)}×${bbox.height.toFixed(0)}px (${(bboxWidthNorm*100).toFixed(1)}%×${(bboxHeightNorm*100).toFixed(1)}%), Receipt: ${receipt.width}×${receipt.height}px, Display: ${displayWidth}×${displayHeight}px`);
    scale = MAX_SCALE;
  }

  // Calculate crop position as percentages of image dimensions
  // bbox.left and bbox.top are in pixels relative to receipt dimensions
  // Convert to normalized, then to percentage of display dimensions
  const cropLeftPercent = (bbox.left / receipt.width) * 100;
  const cropTopPercent = (bbox.top / receipt.height) * 100;

  // Handle image load to get actual dimensions
  const handleImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (img.naturalWidth && img.naturalHeight) {
      setImageDimensions({
        width: img.naturalWidth,
        height: img.naturalHeight,
      });
    }
  }, []);

  return (
    <div
      style={{
        width: "100%",
        maxWidth,
        border: "2px solid #ccc",
        borderRadius: "8px",
        overflow: "hidden",
        backgroundColor: "var(--background-color)",
      }}
    >
      {/* Container sized to crop region (w×h) */}
      <div
        style={{
          position: "relative",
          width: "100%",
          // Use padding-top trick to maintain aspect ratio
          paddingTop: `${(1 / aspectRatio) * 100}%`,
          overflow: "hidden", // Hide overflow
          backgroundColor: "#f0f0f0", // Debug: show container bounds
          minHeight: "50px", // Ensure container has minimum height
        }}
      >
        {/* Image positioned absolutely, shifted by negative of crop's top-left corner */}
        {/*
          The transform percentages are relative to the element's own size (the scaled image).
          When image is scaled by `scale`, we need to translate by the crop position
          as a percentage of the SCALED image size, not the original.

          If crop.left is 3.48% of original image, and image is scaled 9.59x:
          - Scaled image width = 9.59 * container width
          - Crop.left in scaled image = 3.48% * 9.59 = 33.4% of container width
          - But we want to move it by crop.left as % of container, not scaled image
          - So: translateX = -(cropLeftPercent / scale) * 100%
        */}
        <img
          src={imageSrc}
          alt="Cropped address"
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            // Scale image so crop region fills container
            width: `${scale * 100}%`,
            height: "auto",
            objectFit: "none", // Don't auto-scale, use our explicit width
            // Transform percentages are relative to element's own size (scaled image)
            // So we need to divide by scale to get container-relative movement
            transform: `translate(-${cropLeftPercent / scale}%, -${cropTopPercent / scale}%)`,
            minWidth: "100%", // Ensure image is at least container width
          }}
          onLoad={(e) => {
            handleImageLoad(e);
            console.log("SimpleCroppedAddress image loaded", {
              scale: scale.toFixed(2),
              cropLeft: cropLeftPercent.toFixed(2),
              cropTop: cropTopPercent.toFixed(2),
              transformX: (cropLeftPercent / scale).toFixed(2),
              transformY: (cropTopPercent / scale).toFixed(2),
              bbox: `${bbox.width.toFixed(0)}×${bbox.height.toFixed(0)} (${(bboxWidthNorm*100).toFixed(1)}%×${(bboxHeightNorm*100).toFixed(1)}%)`,
              receipt: `${receipt.width}×${receipt.height}`,
              display: imageDimensions ? `${imageDimensions.width}×${imageDimensions.height}` : "not loaded yet",
            });
          }}
          onError={(e) => {
            console.error("SimpleCroppedAddress image failed to load:", imageSrc, e);
          }}
        />
      </div>

      {/* Address text */}
      <div
        style={{
          padding: "1rem",
          backgroundColor: "var(--background-color)",
        }}
      >
        <p style={{ fontSize: "0.9rem", color: "#666", margin: 0 }}>
          {lines.map((line) => line.text).join(" ")}
        </p>
      </div>
    </div>
  );
};

export default SimpleCroppedAddress;

