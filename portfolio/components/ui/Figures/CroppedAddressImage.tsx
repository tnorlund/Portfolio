import NextImage from "next/image";
import React, { useMemo } from "react";
import { Line, Receipt } from "../../../types/api";
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
}) => {
  // Calculate bounding box in pixels
  const bbox = useMemo(() => {
    if (!lines || lines.length === 0 || !receipt.width || !receipt.height) {
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
  }, [lines, receipt.width, receipt.height]);

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

  // Container aspect ratio matches bounding box
  const aspectRatio = bbox.width / bbox.height;

  // Scale factor: how much to scale image so bbox width fills container width
  const scale = receipt.width / bbox.width;

  // Calculate clip-path as percentages of the ORIGINAL image dimensions
  // clip-path: inset(top% right% bottom% left%)
  // These percentages are relative to the element's bounding box (the displayed image)
  // When image is displayed at width: 100% with height: auto, it maintains aspect ratio
  // So clip-path percentages should work correctly relative to the displayed image
  const clipTopPercent = (bbox.top / receipt.height) * 100;
  const clipRightPercent = ((receipt.width - bbox.left - bbox.width) / receipt.width) * 100;
  const clipBottomPercent = ((receipt.height - bbox.top - bbox.height) / receipt.height) * 100;
  const clipLeftPercent = (bbox.left / receipt.width) * 100;

  // Calculate background position for cropping
  // backgroundPosition: "X% Y%" aligns point at X% of image with point at X% of container
  // We want bbox.left (at bbox.left/receipt.width % of image) to align with container left (0%)
  // So we need: backgroundPosition X = 0% to align image 0% with container 0%
  // But that's wrong - we need bbox.left point, not image left edge
  //
  // Actually, when image is scaled, backgroundPosition works like this:
  // The percentage refers to where in the container the image should be positioned
  // For a scaled image, we need to calculate the offset differently
  //
  // Let's try using calc() with pixel values converted to percentages
  // Or better: use the bbox coordinates as percentages directly and let CSS handle it
  // When backgroundSize is set, backgroundPosition percentages should work relative to the scaled size
  const bgPosX = `${(bbox.left / receipt.width) * 100}%`;
  const bgPosY = `${(bbox.top / receipt.height) * 100}%`;

  return (
    <div
      style={{
        width: "100%",
        maxWidth,
        border: debug ? "2px solid red" : "2px solid #ccc",
        borderRadius: "8px",
        overflow: "hidden",
        backgroundColor: "var(--background-color)",
      }}
    >
      {/* Simple CSS cropping using img tag with object-fit/object-position */}
      <div
        style={{
          position: "relative",
          width: "100%",
          paddingTop: `${(1 / aspectRatio) * 100}%`,
          overflow: "hidden",
          backgroundColor: "#f0f0f0",
        }}
      >
        {/* Use regular img tag with transform for precise positioning */}
        <img
          src={imageSrc}
          alt="Cropped address"
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            // Scale image so bbox width fills container width
            width: `${scale * 100}%`,
            height: "auto",
            objectFit: "none",
            // Use transform to shift image so bbox.top-left aligns with container.top-left
            // transform translate percentages are relative to the element's own size
            // bbox.left is at (bbox.left / receipt.width) * 100% of the image width
            // To shift it to container left (0), translate left by that percentage
            // Since the image is scaled, we need to account for that in the calculation
            // When image width = scale * containerWidth, bbox.left in scaled = (bbox.left / receipt.width) * scale * containerWidth
            // To shift to container left: translateX(-(bbox.left / receipt.width) * scale * 100%)
            // But translate % is relative to element size, so we might not need the scale factor
            // Let's try: translate by bbox.left as % of image width
            transform: `translate(${-(bbox.left / receipt.width) * 100}%, ${-(bbox.top / receipt.height) * 100}%)`,
          }}
          onLoad={onLoad}
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
          <div>BBox: {bbox.left.toFixed(0)}, {bbox.top.toFixed(0)}, {bbox.width.toFixed(0)}×{bbox.height.toFixed(0)}px</div>
          <div>Scale: {scale.toFixed(2)}x</div>
          <div>Image width: {scale * 100}%</div>
          <div>bgPosX: {bgPosX}</div>
          <div>bgPosY: {bgPosY}</div>
          <div>bbox.left %: {((bbox.left / receipt.width) * 100).toFixed(2)}%</div>
          <div>bbox.top %: {((bbox.top / receipt.height) * 100).toFixed(2)}%</div>
          <div>Clip-path: inset({clipTopPercent.toFixed(1)}% {clipRightPercent.toFixed(1)}% {clipBottomPercent.toFixed(1)}% {clipLeftPercent.toFixed(1)}%)</div>
          <div>Receipt: {receipt.width}×{receipt.height}px</div>
          <div>BBox aspect: {aspectRatio.toFixed(3)}</div>
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
