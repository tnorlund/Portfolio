import NextImage from "next/image";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import { api } from "../../../services/api";
import { AddressBoundingBox, AddressSimilarityResponse, Line, Receipt } from "../../../types/api";
import {
  detectImageFormatSupport,
  getBestImageUrl,
} from "../../../utils/imageFormat";
import CroppedAddressImage from "./CroppedAddressImage";

interface SimilarReceiptItemProps {
  receipt: Receipt;
  lines: Line[];
  similarityDistance: number;
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean } | null;
  position: { rotation: number; topOffset: number; leftPercent: number; imageWidth: number };
  index: number;
  onLoad: () => void;
  shouldAnimate: boolean;
  fadeDelay: number;
  bbox?: AddressBoundingBox;
}

const SimilarReceiptItem = React.memo<SimilarReceiptItemProps>(
  ({
    receipt,
    lines,
    similarityDistance,
    formatSupport,
    position,
    index,
    onLoad,
    shouldAnimate,
    fadeDelay,
    bbox: apiBbox,
  }) => {
    const [imageLoaded, setImageLoaded] = useState(false);
    const [currentSrc, setCurrentSrc] = useState<string>("");
    const [hasErrored, setHasErrored] = useState<boolean>(false);

    // Use bbox from API if available, otherwise calculate from lines
    // Note: OCR coordinates have y=0 at bottom, CSS has y=0 at top
    const addressBoundingBox = useMemo(() => {
      // If API provides bbox, use it directly
      if (apiBbox) {
        // Convert from OCR coordinates (y=0 at bottom) to CSS (y=0 at top)
        const ocrX = apiBbox.tl.x;
        const ocrYTop = apiBbox.tl.y; // Top edge in OCR space
        const ocrWidth = apiBbox.tr.x - apiBbox.tl.x;
        const ocrHeight = apiBbox.tl.y - apiBbox.bl.y;
        const cssY = 1 - ocrYTop; // Convert to CSS top position

        return {
          x: ocrX,
          y: cssY,
          width: ocrWidth,
          height: ocrHeight,
        };
      }

      // Fallback: Calculate from lines (for backward compatibility)
      if (!lines || lines.length === 0) return null;

      // Collect all corner points from all lines
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

      const minX = Math.min(...allX);
      const maxX = Math.max(...allX);
      const minY = Math.min(...allYBottom);
      const maxY = Math.max(...allYTop);

      // Add padding (5% on each side)
      const paddingX = (maxX - minX) * 0.05;
      const paddingY = (maxY - minY) * 0.05;

      const ocrX = Math.max(0, minX - paddingX);
      const ocrYBottom = Math.max(0, minY - paddingY);
      const ocrYTop = Math.min(1, maxY + paddingY);
      const ocrWidth = Math.min(1, maxX - minX + paddingX * 2);
      const ocrHeight = ocrYTop - ocrYBottom;
      const cssY = 1 - ocrYTop;

      return {
        x: ocrX,
        y: cssY,
        width: ocrWidth,
        height: ocrHeight,
      };
    }, [lines, apiBbox]);

    useEffect(() => {
      if (formatSupport) {
        const bestUrl = getBestImageUrl(receipt, formatSupport, "small");
        setCurrentSrc(bestUrl);
      }
    }, [formatSupport, receipt]);

    const handleImageLoad = useCallback(() => {
      setImageLoaded(true);
      onLoad();
    }, [onLoad]);

    const handleError = () => {
      setHasErrored(true);
      setImageLoaded(true);
      onLoad();
    };

    const { rotation, topOffset, leftPercent, imageWidth } = position;

    if (hasErrored) {
      return (
        <div
          style={{
            position: "absolute",
            width: `${imageWidth}px`,
            left: `${leftPercent}%`,
            top: `${topOffset}px`,
            border: "1px solid #ccc",
            backgroundColor: "var(--background-color)",
            boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
            transform: `rotate(${rotation}deg) translateY(${shouldAnimate && imageLoaded ? 0 : -50
              }px)`,
            opacity: shouldAnimate && imageLoaded ? 1 : 0,
            transition: `transform 0.6s ease-out ${shouldAnimate ? index * fadeDelay : 0
              }ms, opacity 0.6s ease-out ${shouldAnimate ? index * fadeDelay : 0
              }ms`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#999",
            fontSize: "12px",
          }}
        >
          Failed
        </div>
      );
    }

    return (
      <div
        style={{
          position: "absolute",
          width: `${imageWidth}px`,
          left: `${leftPercent}%`,
          top: `${topOffset}px`,
          border: "1px solid #ccc",
          backgroundColor: "var(--background-color)",
          boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
          transform: `rotate(${rotation}deg) translateY(${shouldAnimate && imageLoaded ? 0 : -50
            }px)`,
          opacity: shouldAnimate && imageLoaded ? 1 : 0,
          transition: `transform 0.6s ease-out ${shouldAnimate ? index * fadeDelay : 0
            }ms, opacity 0.6s ease-out ${shouldAnimate ? index * fadeDelay : 0
            }ms`,
          willChange: "transform, opacity",
          overflow: "hidden",
        }}
      >
        {currentSrc && addressBoundingBox && (
          <div
            style={{
              position: "relative",
              width: "100%",
              paddingTop: `${(addressBoundingBox.height / addressBoundingBox.width) * 100}%`,
              overflow: "hidden",
            }}
          >
            {/* Scale factor: how much to scale image so bounding box fills container */}
            {/* Use the larger scale factor to ensure bounding box fills container */}
            {(() => {
              const scaleX = 1 / addressBoundingBox.width;
              const scaleY = 1 / addressBoundingBox.height;
              const scale = Math.max(scaleX, scaleY); // Use larger scale to fill container

              return (
                <NextImage
                  src={currentSrc}
                  alt={`Similar receipt ${receipt.receipt_id}`}
                  width={receipt.width}
                  height={receipt.height}
                  sizes={`${imageWidth}px`}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: `${scale * 100}%`,
                    height: `${scale * 100}%`,
                    objectFit: "none",
                    transform: `translate(${-addressBoundingBox.x * scale * 100}%, ${-addressBoundingBox.y * scale * 100}%)`,
                  }}
                  onLoad={handleImageLoad}
                  onError={handleError}
                  priority={index < 3}
                />
              );
            })()}
          </div>
        )}
        {currentSrc && !addressBoundingBox && (
          <NextImage
            src={currentSrc}
            alt={`Similar receipt ${receipt.receipt_id}`}
            width={receipt.width}
            height={receipt.height}
            sizes={`${imageWidth}px`}
            style={{
              width: "100%",
              height: "auto",
              display: "block",
            }}
            onLoad={handleImageLoad}
            onError={handleError}
            priority={index < 3}
          />
        )}
        <div
          style={{
            position: "absolute",
            bottom: "4px",
            right: "4px",
            backgroundColor: "rgba(0, 0, 0, 0.7)",
            color: "white",
            padding: "2px 6px",
            borderRadius: "4px",
            fontSize: "10px",
            fontWeight: "bold",
          }}
        >
          {similarityDistance.toFixed(2)}
        </div>
      </div>
    );
  }
);

SimilarReceiptItem.displayName = "SimilarReceiptItem";

interface AddressSimilarityProps {
  fadeDelay?: number;
}

const AddressSimilarity: React.FC<AddressSimilarityProps> = ({
  fadeDelay = 50,
}) => {
  const [ref, inView] = useOptimizedInView({
    threshold: 0.1,
    triggerOnce: true,
  });

  const [data, setData] = useState<AddressSimilarityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formatSupport, setFormatSupport] = useState<{
    supportsAVIF: boolean;
    supportsWebP: boolean;
  } | null>(null);
  const [startAnimation, setStartAnimation] = useState(false);
  const [loadedImages, setLoadedImages] = useState<Set<number>>(new Set());

  // Track window resize to recalculate positions
  const [windowWidth, setWindowWidth] = useState(
    typeof window !== "undefined" ? window.innerWidth : 1024
  );

  // Track actual container dimensions using a ref
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [containerDimensions, setContainerDimensions] = useState<{
    width: number;
    height: number;
  } | null>(null);

  // Responsive image dimensions based on screen size
  const getResponsiveImageDimensions = useCallback(() => {
    const isMobile = windowWidth <= 768;
    const isSmallMobile = windowWidth <= 480;

    if (isSmallMobile) {
      return { width: 100, height: 130 }; // Smaller on very small screens
    } else if (isMobile) {
      return { width: 120, height: 160 }; // Medium on mobile
    } else {
      return { width: 150, height: 200 }; // Full size on desktop
    }
  }, [windowWidth]);

  // Measure container dimensions
  const measureContainer = useCallback(() => {
    if (containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect();
      setContainerDimensions({
        width: rect.width,
        height: rect.height,
      });
    }
  }, []);

  useEffect(() => {
    const handleResize = () => {
      setWindowWidth(window.innerWidth);
      measureContainer();
    };

    // Initial measurement
    handleResize();

    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [measureContainer]);

  // Measure container when it becomes available
  useEffect(() => {
    if (containerRef.current && !containerDimensions) {
      measureContainer();
    }
  }, [data, containerDimensions, measureContainer]);

  // Calculate positions for similar receipts (right side)
  const positions = useMemo(() => {
    if (!data || !data.similar) return [];

    // Use responsive image dimensions
    const { width: imageWidth, height: imageHeight } = getResponsiveImageDimensions();

    // Use actual container dimensions if available, otherwise calculate responsive defaults
    const isMobile = windowWidth <= 768;
    let containerWidth: number;
    let containerHeight: number;

    if (containerDimensions) {
      // Use measured dimensions
      containerWidth = containerDimensions.width;
      containerHeight = containerDimensions.height;
    } else {
      // Fallback: calculate responsive container dimensions
      let calculatedContainerWidth = windowWidth;
      if (windowWidth <= 480) {
        calculatedContainerWidth = windowWidth * 0.9;
      } else if (windowWidth <= 768) {
        calculatedContainerWidth = Math.min(windowWidth, 600);
      } else if (windowWidth <= 1024) {
        calculatedContainerWidth = Math.min(windowWidth, 900);
      } else {
        calculatedContainerWidth = Math.min(windowWidth, 1200);
      }

      const containerPadding = windowWidth <= 480 ? 16 : 32;
      const effectiveWidth = calculatedContainerWidth - containerPadding * 2;
      // On mobile, items stack vertically so each side takes full width
      // On desktop, items are side-by-side so each side takes half width
      containerWidth = isMobile ? effectiveWidth : effectiveWidth * 0.5;

      // Responsive container height
      if (isMobile) {
        containerHeight = Math.min(windowWidth * 0.8, 400); // Scale with screen on mobile
      } else {
        containerHeight = 500; // Fixed on desktop
      }
    }

    // Calculate image size as percentage of container
    const imageWidthPercent = (imageWidth / containerWidth) * 100;
    // Ensure we don't exceed container bounds, with some margin
    const maxLeftPercent = Math.max(5, Math.min(95, 100 - imageWidthPercent - 5));

    // Use relative margins (percentage of container height) instead of fixed pixels
    const marginBottomPercent = 0.1; // 10% margin at bottom
    const marginTopPercent = 0.04; // 4% margin at top
    const maxTopPx = containerHeight * (1 - marginBottomPercent) - imageHeight;
    const minTopPx = containerHeight * marginTopPercent;

    return data.similar.map((_, index) => {
      const rotation = Math.random() * 40 - 20;
      const leftPercent = Math.random() * maxLeftPercent;
      const topOffset = Math.random() * maxTopPx;

      const depthBias = index / data.similar.length;
      const centerPoint = maxLeftPercent / 2;
      const biasedLeft =
        leftPercent * (1 - depthBias * 0.4) + centerPoint * (depthBias * 0.4);
      const biasedTop =
        topOffset * (1 - depthBias * 0.3) + (maxTopPx / 2) * (depthBias * 0.3);

      const finalTop = Math.max(minTopPx, Math.min(biasedTop, maxTopPx));

      return {
        rotation,
        topOffset: Math.round(finalTop),
        leftPercent: Math.min(Math.max(5, biasedLeft), maxLeftPercent), // Clamp between 5% and maxLeftPercent
        imageWidth, // Include responsive image width in position
      };
    });
  }, [data, windowWidth, containerDimensions, getResponsiveImageDimensions]);


  useEffect(() => {
    detectImageFormatSupport().then((support) => {
      setFormatSupport(support);
    });
  }, []);

  useEffect(() => {
    const loadData = async () => {
      if (!formatSupport) return;

      try {
        const response = await api.fetchAddressSimilarity();
        setData(response);
      } catch (error) {
        console.error("Error loading address similarity:", error);
        setError(
          error instanceof Error ? error.message : "Failed to load address similarity"
        );
      }
    };

    if (formatSupport) {
      loadData();
    }
  }, [formatSupport]);

  const handleImageLoad = useCallback((index: number) => {
    setLoadedImages((prev) => new Set(prev).add(index));
  }, []);

  useEffect(() => {
    if (
      inView &&
      data &&
      data.similar.length > 0 &&
      loadedImages.size >= data.similar.length &&
      !startAnimation
    ) {
      setStartAnimation(true);
    }
  }, [inView, data, loadedImages.size, startAnimation]);

  if (error) {
    return (
      <div
        ref={ref}
        style={{
          width: "100%",
          display: "flex",
          justifyContent: "center",
          marginTop: "6rem",
          padding: "2rem",
          color: "red",
        }}
      >
        <p>Error: {error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div
        ref={ref}
        style={{
          width: "100%",
          display: "flex",
          justifyContent: "center",
          marginTop: "6rem",
          padding: "2rem",
        }}
      >
        <p>Loading address similarity data...</p>
      </div>
    );
  }

  const originalReceipt = data.original.receipt;

  return (
    <div
      ref={ref}
      style={{
        width: "100%",
        maxWidth: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        marginTop: "6rem",
        padding: windowWidth <= 768 ? "1rem" : "2rem",
        boxSizing: "border-box",
        overflowX: "hidden",
      }}
    >
      <h2 style={{ marginBottom: "3rem", fontSize: "2rem", fontWeight: "bold" }}>
        Address Similarity Search
      </h2>

      <div
        style={{
          display: "flex",
          flexDirection: windowWidth <= 768 ? "column" : "row",
          gap: "3rem",
          width: "100%",
          maxWidth: "100%", // Respect parent container's max-width instead of fixed 1200px
          alignItems: "flex-start",
          boxSizing: "border-box",
        }}
      >
        {/* Original Address (Left Side) */}
        <div
          style={{
            flex: "1",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            minWidth: windowWidth <= 768 ? "100%" : "300px",
          }}
        >
          <h3 style={{ marginBottom: "1.5rem", fontSize: "1.5rem" }}>
            Original Address
          </h3>
          <CroppedAddressImage
            receipt={originalReceipt}
            lines={data.original.lines}
            formatSupport={formatSupport}
            maxWidth="400px"
            priority
            debug={true}
            bbox={data.original.bbox}
          />
        </div>

        {/* Similar Addresses (Right Side - Image Stack) */}
        <div
          style={{
            flex: "1",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            minWidth: windowWidth <= 768 ? "100%" : "300px",
          }}
        >
          <h3 style={{ marginBottom: "1.5rem", fontSize: "1.5rem" }}>
            Similar Addresses ({data.similar.length})
          </h3>
          <div
            ref={containerRef}
            style={{
              position: "relative",
              width: "100%",
              height: windowWidth <= 768
                ? `${Math.min(windowWidth * 0.8, 400)}px`
                : "500px",
              overflow: "hidden",
            }}
          >
            {data.similar.map((similar, index) => (
              <SimilarReceiptItem
                key={`${similar.receipt.image_id}-${similar.receipt.receipt_id}`}
                receipt={similar.receipt}
                lines={similar.lines}
                similarityDistance={similar.similarity_distance}
                formatSupport={formatSupport}
                position={positions[index]}
                index={index}
                onLoad={() => handleImageLoad(index)}
                shouldAnimate={startAnimation}
                fadeDelay={fadeDelay}
                bbox={similar.bbox}
              />
            ))}
          </div>
        </div>
      </div>

      {data.cached_at && (
        <p
          style={{
            marginTop: "2rem",
            fontSize: "0.875rem",
            color: "#999",
            fontStyle: "italic",
          }}
        >
          Cache updated: {new Date(data.cached_at).toLocaleString()}
        </p>
      )}
    </div>
  );
};

export default AddressSimilarity;

