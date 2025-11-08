import React, { useEffect, useState, useMemo, useCallback } from "react";
import { api } from "../../../services/api";
import { AddressSimilarityResponse, BoundingBox } from "../../../types/api";
import {
  detectImageFormatSupport,
} from "../../../utils/imageFormat";
import { getBestImageUrl } from "../../../utils/imageFormat";

/**
 * Simple component that fetches address similarity data
 * and displays the receipt image with CSS-only cropping.
 */
const SimpleReceiptImage: React.FC = () => {
  const [data, setData] = useState<AddressSimilarityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formatSupport, setFormatSupport] = useState<{
    supportsAVIF: boolean;
    supportsWebP: boolean;
  } | null>(null);

  // Track actual loaded image dimensions (may differ from receipt.width/height for medium images)
  const [imageDimensions, setImageDimensions] = useState<{
    width: number;
    height: number;
  } | null>(null);

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

  // Calculate crop region from API bbox or lines (normalized coordinates 0..1)
  // Must be called before early returns to maintain hook order
  const cropRegion = useMemo(() => {
    const apiBbox = data?.original?.bbox;
    const lines = data?.original?.lines;
    const receipt = data?.original?.receipt;

    // If API provides bbox, use it directly
    if (apiBbox && receipt?.width && receipt?.height) {
      // Bounding box in normalized coordinates (OCR space)
      const leftNorm = apiBbox.tl.x;
      const rightNorm = apiBbox.tr.x;
      const topNorm = apiBbox.tl.y; // Top edge in OCR space (y=0 at bottom)
      const bottomNorm = apiBbox.bl.y; // Bottom edge in OCR space

      // Crop dimensions in normalized space
      const widthNorm = rightNorm - leftNorm;
      const heightNorm = topNorm - bottomNorm; // In OCR space (y increases upward)

      // Convert OCR Y to CSS Y (y=0 at top)
      // OCR topNorm = 0.9 means 90% from bottom = 10% from top in CSS
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
    // OCR: y=0 at bottom, y=1 at top
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

    // Crop dimensions in normalized space
    const widthNorm = rightNorm - leftNorm;
    const heightNorm = topNorm - bottomNorm; // In OCR space (y increases upward)

    // Convert OCR Y to CSS Y (y=0 at top)
    // OCR topNorm = 0.9 means 90% from bottom = 10% from top in CSS
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
  }, [data?.original?.bbox, data?.original?.lines, data?.original?.receipt]);

  // Handle image load to get actual dimensions
  // Must be defined before early returns (Rules of Hooks)
  const handleImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (img.naturalWidth && img.naturalHeight) {
      setImageDimensions({
        width: img.naturalWidth,
        height: img.naturalHeight,
      });
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
        Loading receipt image...
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
        {error || "No receipt data available"}
      </div>
    );
  }

  // Get receipt - must be defined early, before it's used
  const receipt = data.original.receipt;

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
          padding: "2rem",
          textAlign: "center",
          color: "#999",
        }}
      >
        Image not available
      </div>
    );
  }

  if (!cropRegion) {
    return (
      <div
        style={{
          padding: "2rem",
          textAlign: "center",
          color: "#999",
        }}
      >
        No crop region available
      </div>
    );
  }

  // Get receipt dimensions (fallback if image not loaded yet)
  const displayWidth = imageDimensions?.width ?? receipt.width;
  const displayHeight = imageDimensions?.height ?? receipt.height;
  const displayAspectRatio = displayWidth / displayHeight;

  // Crop region aspect ratio (in normalized coordinates)
  const cropAspectRatio = cropRegion.widthNorm / cropRegion.cssHeightNorm;

  // Container aspect ratio needs to account for display image aspect ratio
  // When we scale the image, the container height should match the scaled crop height
  // Container height = width * (crop.height / crop.width) * (display.height / display.width)
  const containerAspectRatio = cropAspectRatio * displayAspectRatio;

  // Scale image so crop region fills container
  // If crop width is 40% of image, scale by 1/0.4 = 2.5x
  const imageWidthPercent = (1 / cropRegion.widthNorm) * 100;

  // Shift image to show crop region
  // Shift left by crop start position
  const shiftLeftPercent = cropRegion.leftNorm * 100;
  // Shift up by crop top position (in CSS coordinates)
  const shiftTopPercent = cropRegion.cssTopNorm * 100;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        width: "100%",
        maxWidth: "600px",
        margin: "0 auto",
      }}
    >
      <div
        style={{
          width: "100%",
          border: "2px solid #ccc",
          borderRadius: "8px",
          overflow: "hidden",
          backgroundColor: "#ff00ff", // Ugly background color to see what's working
        }}
      >
        {/* Container sized to crop region using padding-top trick */}
        <div
          style={{
            position: "relative",
            width: "100%",
            paddingTop: `${(1 / containerAspectRatio) * 100}%`,
            overflow: "hidden",
            backgroundColor: "#00ffff", // Another ugly color for the inner container
          }}
        >
          {/* Image positioned absolutely, shifted to show crop region */}
          <img
            src={imageSrc}
            alt="Cropped receipt"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: `${imageWidthPercent}%`,
              height: "auto",
              transform: `translate(-${shiftLeftPercent}%, -${shiftTopPercent}%)`,
              boxShadow: "0 4px 6px rgba(0, 0, 0, 0.1), 0 2px 4px rgba(0, 0, 0, 0.06)",
            }}
            onError={(e) => {
              console.error("SimpleReceiptImage image failed to load:", imageSrc, e);
            }}
            onLoad={(e) => {
              handleImageLoad(e);
              console.log("SimpleReceiptImage crop region:", {
                leftNorm: cropRegion.leftNorm.toFixed(3),
                rightNorm: cropRegion.rightNorm.toFixed(3),
                widthNorm: cropRegion.widthNorm.toFixed(3),
                topNorm: cropRegion.topNorm.toFixed(3),
                bottomNorm: cropRegion.bottomNorm.toFixed(3),
                cssTopNorm: cropRegion.cssTopNorm.toFixed(3),
                cssHeightNorm: cropRegion.cssHeightNorm.toFixed(3),
                cropAspectRatio: cropAspectRatio.toFixed(3),
                displayAspectRatio: displayAspectRatio.toFixed(3),
                containerAspectRatio: containerAspectRatio.toFixed(3),
                imageWidthPercent: imageWidthPercent.toFixed(1),
                shiftLeftPercent: shiftLeftPercent.toFixed(1),
                shiftTopPercent: shiftTopPercent.toFixed(1),
                receipt: `${receipt.width}×${receipt.height}`,
                display: imageDimensions ? `${imageDimensions.width}×${imageDimensions.height}` : "not loaded yet",
              });
            }}
          />
        </div>
      </div>
    </div>
  );
};

export default SimpleReceiptImage;


