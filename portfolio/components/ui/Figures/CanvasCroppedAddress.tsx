import React, { useRef, useEffect, useState, useMemo } from "react";
import { Line, Receipt, BoundingBox } from "../../../types/api";
import { getBestImageUrl } from "../../../utils/imageFormat";

interface CanvasCroppedAddressProps {
  receipt: Receipt;
  lines: Line[];
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean } | null;
  maxWidth?: string;
  onLoad?: () => void;
  onError?: (error: Error) => void;
  debug?: boolean;
  bbox?: BoundingBox;
}

/**
 * Canvas-based cropping implementation for comparison.
 *
 * PERFORMANCE CONSIDERATIONS:
 * - Requires full image to load before drawing
 * - JavaScript execution on every render/resize
 * - Memory: Full image + canvas buffer in memory
 * - CPU: Image decoding + canvas drawing
 * - No Next.js Image optimization benefits
 * - Lazy loading still works but requires JS execution
 *
 * USE CASES:
 * - When you need to extract cropped image data (toBlob, toDataURL)
 * - When you need image processing/manipulation
 * - When you need pixel-perfect control
 *
 * NOT RECOMMENDED FOR:
 * - Simple display-only cropping (CSS is better)
 * - Performance-critical scenarios
 * - Large images or many instances
 */
const CanvasCroppedAddress: React.FC<CanvasCroppedAddressProps> = ({
  receipt,
  lines,
  formatSupport,
  maxWidth = "400px",
  onLoad,
  onError,
  debug = false,
  bbox: apiBbox,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // Calculate bounding box (same as CSS version)
  const bbox = useMemo(() => {
    if (!receipt.width || !receipt.height) {
      return null;
    }

    if (apiBbox) {
      const leftNorm = apiBbox.tl.x;
      const rightNorm = apiBbox.tr.x;
      const topNorm = apiBbox.tl.y;
      const bottomNorm = apiBbox.bl.y;

      const leftPx = leftNorm * receipt.width;
      const rightPx = rightNorm * receipt.width;
      const widthPx = rightPx - leftPx;

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

    // Fallback calculation from lines...
    return null;
  }, [receipt, apiBbox, lines]);

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

  // Load image and draw to canvas
  useEffect(() => {
    if (!canvasRef.current || !bbox || !imageSrc) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Create image element
    const img = new Image();
    img.crossOrigin = "anonymous"; // Required if image is from different domain

    img.onload = () => {
      try {
        // Set canvas size to match crop region (scaled to display size)
        // For responsive sizing, we'll use CSS to scale the canvas
        const aspectRatio = bbox.width / bbox.height;
        const displayWidth = 400; // Base display width
        const displayHeight = displayWidth / aspectRatio;

        canvas.width = displayWidth;
        canvas.height = displayHeight;

        // Draw only the cropped region
        // drawImage(image, sx, sy, sWidth, sHeight, dx, dy, dWidth, dHeight)
        // sx, sy: source x, y (where to start cropping from original image)
        // sWidth, sHeight: source width, height (crop dimensions)
        // dx, dy: destination x, y (where to draw on canvas, usually 0, 0)
        // dWidth, dHeight: destination width, height (canvas size)
        ctx.drawImage(
          img,
          bbox.left,           // Source X: crop start position
          bbox.top,            // Source Y: crop start position
          bbox.width,          // Source width: crop width
          bbox.height,         // Source height: crop height
          0,                   // Destination X: start at canvas left
          0,                   // Destination Y: start at canvas top
          displayWidth,        // Destination width: fill canvas
          displayHeight        // Destination height: fill canvas
        );

        setImageLoaded(true);
        onLoad?.();
      } catch (err) {
        const error = err instanceof Error ? err : new Error("Failed to draw image");
        setError(error);
        onError?.(error);
      }
    };

    img.onerror = (e) => {
      const error = new Error(`Failed to load image: ${imageSrc}`);
      setError(error);
      onError?.(error);
    };

    img.src = imageSrc;

    // Cleanup
    return () => {
      img.onload = null;
      img.onerror = null;
    };
  }, [canvasRef, bbox, imageSrc, onLoad, onError]);

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

  const aspectRatio = bbox.width / bbox.height;

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
      {/* Canvas container with responsive sizing */}
      <div
        style={{
          position: "relative",
          width: "100%",
          paddingTop: `${(1 / aspectRatio) * 100}%`, // Maintain aspect ratio
        }}
      >
        <canvas
          ref={canvasRef}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            display: imageLoaded ? "block" : "none",
          }}
        />
        {!imageLoaded && !error && (
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "#f0f0f0",
              color: "#999",
            }}
          >
            Loading...
          </div>
        )}
        {error && (
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "#fee",
              color: "#c00",
              padding: "1rem",
            }}
          >
            Error: {error.message}
          </div>
        )}
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

export default CanvasCroppedAddress;

