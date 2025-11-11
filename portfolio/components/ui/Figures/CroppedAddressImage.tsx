import NextImage from "next/image";
import React, { useMemo, useState, useCallback } from "react";
import { Line, Receipt, BoundingBox } from "../../../types/api";
import { getBestImageUrl } from "../../../utils/imageFormat";

interface CroppedAddressImageProps {
  receipt: Receipt;
  lines: Line[];
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean } | null;
  maxWidth?: string;
  priority?: boolean;
  onLoad?: () => void;
  onError?: (error: Error) => void;
  debug?: boolean;
  bbox?: BoundingBox;
}

/**
 * Component that displays a receipt image cropped to show only the address lines.
 *
 * Simple approach: Use CSS clip-path to crop the image to the bounding box region.
 */
const CroppedAddressImage: React.FC<CroppedAddressImageProps> = ({
  receipt,
  lines,
  formatSupport,
  maxWidth = "400px",
  priority = false,
  onLoad,
  onError,
  debug = false,
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

    // Collect all corner points
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
    const paddingY = (maxY - minY) * 0.05;

    // Bounding box in normalized coordinates
    const leftNorm = Math.max(0, minX - paddingX);
    const rightNorm = Math.min(1, maxX + paddingX);
    const bottomNorm = Math.max(0, minY - paddingY); // OCR: y=0 at bottom
    const topNorm = Math.min(1, maxY + paddingY); // OCR: y=1 at top

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

  // Handle image load to get actual dimensions
  // Must be defined before any early returns (Rules of Hooks)
  const handleImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (img.naturalWidth && img.naturalHeight) {
      setImageDimensions({
        width: img.naturalWidth,
        height: img.naturalHeight,
      });
    }
    onLoad?.();
  }, [onLoad]);

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

  if (!imageSrc) {
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

  if (!bbox) {
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
        <p style={{ color: "#999", margin: 0 }}>Unable to calculate crop region</p>
      </div>
    );
  }

  // Convert bounding box from pixel coordinates to normalized coordinates (0-1)
  // Our bbox is in CSS coordinates (y=0 at top), matching the example's NormalizedBox format
  const box = {
    x: bbox.left / receipt.width,        // Left edge as fraction of image width
    y: bbox.top / receipt.height,        // Top edge as fraction of image height (CSS: y=0 at top)
    w: bbox.width / receipt.width,       // Width as fraction of image width
    h: bbox.height / receipt.height,     // Height as fraction of image height
  };

  // Container aspect ratio: we need to account for the actual loaded image's aspect ratio
  // The bounding box is normalized (0-1) relative to the original receipt dimensions,
  // but the displayed image (medium) may have different dimensions.
  //
  // Use actual loaded image dimensions if available, otherwise fall back to receipt dimensions
  const displayWidth = imageDimensions?.width ?? receipt.width;
  const displayHeight = imageDimensions?.height ?? receipt.height;
  const displayAspectRatio = displayWidth / displayHeight;

  const cropAspectRatio = box.w / box.h;
  // Container height = width * (box.h / box.w) * (display.height / display.width)
  //                  = width * (1 / cropAspectRatio) * (1 / displayAspectRatio)
  const containerAspectRatio = cropAspectRatio * displayAspectRatio;

  // Ensure aspect ratio is valid (prevent division by zero)
  if (!isFinite(containerAspectRatio) || containerAspectRatio <= 0) {
    console.error("Invalid aspect ratio:", containerAspectRatio, "box:", box, "display:", { displayWidth, displayHeight });
  }

  // Container style: maintain aspect ratio using padding-top trick
  // This ensures the container always has the correct aspect ratio regardless of parent width
  // paddingTop percentage creates height = width / aspectRatio
  const containerStyle: React.CSSProperties = {
    position: "relative",
    width: "100%",                       // Container fills parent width
    paddingTop: `${(1 / containerAspectRatio) * 100}%`,  // Height accounts for both crop and receipt aspect ratios
    overflow: "hidden",                  // Clip everything outside the container
    backgroundColor: "#f0f0f0",
  };

  // Scale factor: how much to scale image so crop region fills container
  // We scale based on width to fill the container width
  const scale = 1 / box.w;  // Scale needed to make crop width fill container width

  // Calculate crop position as percentages of the original image dimensions
  const cropLeftPercent = box.x * 100;   // % from left of original image
  const cropTopPercent = box.y * 100;    // % from top of original image (CSS: y=0 at top)

  // Transform calculation (matching SimpleCroppedAddress):
  // Transform percentages are relative to the element's own size (the scaled image)
  // When image is scaled by `scale`, we need to divide by scale to get container-relative movement
  // This ensures the crop region aligns correctly with the container's top-left corner
  const translateXPercent = cropLeftPercent / scale;
  const translateYPercent = cropTopPercent / scale;

  const imageStyle: React.CSSProperties = {
    position: "absolute",
    top: 0,
    left: 0,
    width: `${scale * 100}%`,            // Scale image so crop region fills container
    height: "auto",                      // Maintain aspect ratio
    objectFit: "none",                   // Don't auto-scale, use our explicit width
    // Transform percentages are relative to the element's own size (scaled image)
    // Divide by scale to convert from original-image-relative to container-relative movement
    transform: `translate(-${translateXPercent}%, -${translateYPercent}%)`,
  };

  return (
    <div
      style={{
        width: "100%",
        maxWidth,
        border: debug ? "2px solid red" : "2px solid #ccc",
        borderRadius: "8px",
        overflow: "hidden",
        backgroundColor: "var(--background-color)",
        boxSizing: "border-box", // Ensure border is included in width calculation
      }}
    >
      {/*
        Container maintains crop region aspect ratio:
        - width: 100% (fills parent)
        - paddingTop: (1/aspectRatio) * 100% (creates height matching aspect ratio)
        - overflow: hidden clips the image to only show what's inside this container
        - This "padding-top trick" is a CSS technique to maintain aspect ratio responsively
        - The container height = width * (1/aspectRatio) = width * (box.h / box.w)
      */}
      <div
        style={containerStyle}
        title={`Container aspect ratio: ${containerAspectRatio.toFixed(3)} (crop: ${cropAspectRatio.toFixed(3)}, display: ${displayAspectRatio.toFixed(3)})`}
      >
        {/*
          Image scaled and positioned to show crop region:
          - width: scale * 100% (scales image so crop region fills container)
          - transform: translate() shifts image so crop region aligns with container top-left
          - The scale factor (1/box.w) ensures the crop width fills the container width
          - Negative translate offsets move the image so the crop region is visible
          - Container's overflow: hidden then clips to show only the crop region
        */}
        <img
          src={imageSrc}
          alt="Cropped address"
          style={imageStyle}
          onLoad={handleImageLoad}
          onError={(e) => {
            console.error("CroppedAddressImage: Image failed to load:", imageSrc, e);
            onError?.(new Error(`Failed to load image: ${imageSrc}`));
          }}
        />
        {/* Hidden NextImage for optimization/preloading */}
        <NextImage
          src={imageSrc}
          alt=""
          width={receipt.width}
          height={receipt.height}
          sizes={`${maxWidth}`}
          style={{
            position: "absolute",
            width: 1,
            height: 1,
            opacity: 0,
            pointerEvents: "none",
          }}
          priority={priority}
        />

        {/* Debug overlay */}
        {debug && (
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
              border: "3px solid red",
              pointerEvents: "none",
              zIndex: 10,
            }}
          />
        )}
      </div>

      {/* Debug: Show full receipt with bounding box overlay */}
      {debug && (
        <div
          style={{
            marginTop: "1rem",
            padding: "1rem",
            backgroundColor: "rgba(0, 0, 0, 0.95)",
            borderTop: "2px solid yellow",
          }}
        >
          <div style={{ fontWeight: "bold", marginBottom: "12px", color: "#ffff00", fontSize: "14px" }}>
            DEBUG: Full Receipt with Bounding Box
          </div>
          <div
            style={{
              position: "relative",
              width: "100%",
              maxWidth: "600px",
              margin: "0 auto",
              border: "2px solid #666",
            }}
          >
            <NextImage
              src={imageSrc}
              alt="Full receipt (debug)"
              width={receipt.width}
              height={receipt.height}
              sizes="600px"
              style={{
                width: "100%",
                height: "auto",
                display: "block",
              }}
            />
            {/* Bounding box overlay */}
            <div
              style={{
                position: "absolute",
                left: `${(bbox.left / receipt.width) * 100}%`,
                top: `${(bbox.top / receipt.height) * 100}%`,
                width: `${(bbox.width / receipt.width) * 100}%`,
                height: `${(bbox.height / receipt.height) * 100}%`,
                border: "3px solid red",
                backgroundColor: "rgba(255, 0, 0, 0.2)",
                pointerEvents: "none",
                boxSizing: "border-box",
              }}
            />
          </div>
        </div>
      )}

      {/* Debug info */}
      {debug && (
        <div
          style={{
            backgroundColor: "rgba(0, 0, 0, 0.95)",
            color: "white",
            padding: "12px",
            fontSize: "12px",
            fontFamily: "monospace",
            borderTop: "2px solid yellow",
          }}
        >
          <div style={{ fontWeight: "bold", marginBottom: "6px", color: "#ffff00" }}>
            DEBUG INFO
          </div>
          <div>Receipt: {receipt.width}×{receipt.height}px</div>
          <div>BBox (px): {bbox.left.toFixed(0)}, {bbox.top.toFixed(0)}, {bbox.width.toFixed(0)}×{bbox.height.toFixed(0)}</div>
          <div>Box (normalized): x={box.x.toFixed(3)}, y={box.y.toFixed(3)}, w={box.w.toFixed(3)}, h={box.h.toFixed(3)}</div>
          <div>Container: width=100%, paddingTop={(1 / containerAspectRatio) * 100}%</div>
          <div>Container aspect ratio: {containerAspectRatio.toFixed(3)} (crop: {cropAspectRatio.toFixed(3)}, display: {displayAspectRatio.toFixed(3)})</div>
          <div>Display image: {displayWidth}×{displayHeight}px {imageDimensions ? "(loaded)" : "(using receipt dimensions)"}</div>
          <div>Container height: If width=400px, height would be {(400 * (1/containerAspectRatio)).toFixed(1)}px</div>
          <div>Scale: {scale.toFixed(3)}x (1 / box.w = 1 / {box.w.toFixed(3)})</div>
          <div>Image: width={scale * 100}%, transform: translate(-{translateXPercent.toFixed(2)}%, -{translateYPercent.toFixed(2)}%)</div>
          <div>Scaled crop height: {(400 * box.h * scale * (displayHeight / displayWidth)).toFixed(1)}px (should match container height)</div>
          <div>Crop position: {cropLeftPercent.toFixed(2)}% from left, {cropTopPercent.toFixed(2)}% from top</div>
          <div>BBox (px): left={bbox.left.toFixed(0)}, top={bbox.top.toFixed(0)}, width={bbox.width.toFixed(0)}, height={bbox.height.toFixed(0)}</div>
          <div>Box (normalized): x={box.x.toFixed(3)}, y={box.y.toFixed(3)}, w={box.w.toFixed(3)}, h={box.h.toFixed(3)}</div>
          {apiBbox && (
            <>
              <div>API BBox (OCR): tl=({apiBbox.tl.x.toFixed(3)}, {apiBbox.tl.y.toFixed(3)}), br=({apiBbox.br.x.toFixed(3)}, {apiBbox.br.y.toFixed(3)})</div>
              <div>OCR to CSS conversion: topNorm={apiBbox.tl.y.toFixed(3)} → topPx={bbox.top.toFixed(0)}px ({box.y.toFixed(3)} normalized)</div>
            </>
          )}
          <div>Receipt: {receipt.width}×{receipt.height}px</div>
          <div>Receipt aspect: {(receipt.width / receipt.height).toFixed(3)}</div>
        </div>
      )}

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

export default CroppedAddressImage;
