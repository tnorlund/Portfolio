import React, { useEffect, useState, useMemo, useCallback } from "react";
import NextImage from "next/image";
import { api } from "../../../services/api";
import { AddressSimilarityResponse, Receipt, Line } from "../../../types/api";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import {
  detectImageFormatSupport,
  getBestImageUrl,
} from "../../../utils/imageFormat";

interface SimilarReceiptItemProps {
  receipt: Receipt;
  lines: Line[];
  similarityDistance: number;
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean } | null;
  position: { rotation: number; topOffset: number; leftPercent: number };
  index: number;
  onLoad: () => void;
  shouldAnimate: boolean;
  fadeDelay: number;
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
  }) => {
    const [imageLoaded, setImageLoaded] = useState(false);
    const [currentSrc, setCurrentSrc] = useState<string>("");
    const [hasErrored, setHasErrored] = useState<boolean>(false);

    // Calculate bounding box for address lines
    const addressBoundingBox = useMemo(() => {
      if (!lines || lines.length === 0) return null;

      const allX = lines.flatMap((line) => [
        line.bounding_box.x,
        line.bounding_box.x + line.bounding_box.width,
      ]);
      const allY = lines.flatMap((line) => [
        line.bounding_box.y,
        line.bounding_box.y + line.bounding_box.height,
      ]);

      const minX = Math.min(...allX);
      const maxX = Math.max(...allX);
      const minY = Math.min(...allY);
      const maxY = Math.max(...allY);

      // Add padding (5% on each side)
      const paddingX = (maxX - minX) * 0.05;
      const paddingY = (maxY - minY) * 0.05;

      return {
        x: Math.max(0, minX - paddingX),
        y: Math.max(0, minY - paddingY),
        width: Math.min(1, maxX - minX + paddingX * 2),
        height: Math.min(1, maxY - minY + paddingY * 2),
      };
    }, [lines]);

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

    const { rotation, topOffset, leftPercent } = position;

    if (hasErrored) {
      return (
        <div
          style={{
            position: "absolute",
            width: "150px",
            left: `${leftPercent}%`,
            top: `${topOffset}px`,
            border: "1px solid #ccc",
            backgroundColor: "var(--background-color)",
            boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
            transform: `rotate(${rotation}deg) translateY(${
              shouldAnimate && imageLoaded ? 0 : -50
            }px)`,
            opacity: shouldAnimate && imageLoaded ? 1 : 0,
            transition: `transform 0.6s ease-out ${
              shouldAnimate ? index * fadeDelay : 0
            }ms, opacity 0.6s ease-out ${
              shouldAnimate ? index * fadeDelay : 0
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
          width: "150px",
          left: `${leftPercent}%`,
          top: `${topOffset}px`,
          border: "1px solid #ccc",
          backgroundColor: "var(--background-color)",
          boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
          transform: `rotate(${rotation}deg) translateY(${
            shouldAnimate && imageLoaded ? 0 : -50
          }px)`,
          opacity: shouldAnimate && imageLoaded ? 1 : 0,
          transition: `transform 0.6s ease-out ${
            shouldAnimate ? index * fadeDelay : 0
          }ms, opacity 0.6s ease-out ${
            shouldAnimate ? index * fadeDelay : 0
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
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: `${addressBoundingBox.width * 100}%`,
                height: `${addressBoundingBox.height * 100}%`,
                transform: `translate(${addressBoundingBox.x * 100}%, ${addressBoundingBox.y * 100}%)`,
                overflow: "hidden",
              }}
            >
              <NextImage
                src={currentSrc}
                alt={`Similar receipt ${receipt.receipt_id}`}
                width={receipt.width}
                height={receipt.height}
                sizes="150px"
                style={{
                  width: `${(1 / addressBoundingBox.width) * 100}%`,
                  height: `${(1 / addressBoundingBox.height) * 100}%`,
                  objectFit: "cover",
                  transform: `translate(${-addressBoundingBox.x * 100}%, ${-addressBoundingBox.y * 100}%)`,
                }}
                onLoad={handleImageLoad}
                onError={handleError}
                priority={index < 3}
              />
            </div>
          </div>
        )}
        {currentSrc && !addressBoundingBox && (
          <NextImage
            src={currentSrc}
            alt={`Similar receipt ${receipt.receipt_id}`}
            width={receipt.width}
            height={receipt.height}
            sizes="150px"
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

  useEffect(() => {
    const handleResize = () => {
      setWindowWidth(window.innerWidth);
    };

    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  // Calculate positions for similar receipts (right side)
  const positions = useMemo(() => {
    if (!data || !data.similar) return [];

    const containerHeight = 500;
    const imageWidth = 150;
    const imageHeight = 200; // Estimated height for cropped address section

    let containerWidth = windowWidth;
    if (windowWidth <= 480) {
      containerWidth = windowWidth * 0.9;
    } else if (windowWidth <= 768) {
      containerWidth = Math.min(windowWidth, 600);
    } else if (windowWidth <= 1024) {
      containerWidth = Math.min(windowWidth, 900);
    } else {
      containerWidth = Math.min(windowWidth, 1200);
    }

    const containerPadding = windowWidth <= 480 ? 16 : 32;
    const effectiveWidth = containerWidth - containerPadding * 2;
    const rightSideWidth = effectiveWidth * 0.5; // Right half of container
    const imageWidthPercent = (imageWidth / rightSideWidth) * 100;
    const maxLeftPercent = Math.max(10, 100 - imageWidthPercent - 5);

    const maxTopPx = containerHeight - imageHeight - 50;

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

      const finalTop = Math.max(20, Math.min(biasedTop, maxTopPx - 20));

      return {
        rotation,
        topOffset: Math.round(finalTop),
        leftPercent: Math.min(biasedLeft, maxLeftPercent),
      };
    });
  }, [data, windowWidth]);

  // Calculate bounding box for original address
  const originalAddressBoundingBox = useMemo(() => {
    if (!data || !data.original.lines || data.original.lines.length === 0)
      return null;

    const lines = data.original.lines;
    const allX = lines.flatMap((line) => [
      line.bounding_box.x,
      line.bounding_box.x + line.bounding_box.width,
    ]);
    const allY = lines.flatMap((line) => [
      line.bounding_box.y,
      line.bounding_box.y + line.bounding_box.height,
    ]);

    const minX = Math.min(...allX);
    const maxX = Math.max(...allX);
    const minY = Math.min(...allY);
    const maxY = Math.max(...allY);

    const paddingX = (maxX - minX) * 0.05;
    const paddingY = (maxY - minY) * 0.05;

    return {
      x: Math.max(0, minX - paddingX),
      y: Math.max(0, minY - paddingY),
      width: Math.min(1, maxX - minX + paddingX * 2),
      height: Math.min(1, maxY - minY + paddingY * 2),
    };
  }, [data]);

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
  const originalImageSrc = formatSupport
    ? getBestImageUrl(originalReceipt, formatSupport, "medium")
    : null;

  return (
    <div
      ref={ref}
      style={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        marginTop: "6rem",
        padding: "2rem",
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
          maxWidth: "1200px",
          alignItems: "flex-start",
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
          {originalImageSrc && originalAddressBoundingBox && (
            <div
              style={{
                width: "100%",
                maxWidth: "400px",
                border: "2px solid #ccc",
                borderRadius: "8px",
                overflow: "hidden",
                boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                backgroundColor: "var(--background-color)",
              }}
            >
              <div
                style={{
                  position: "relative",
                  width: "100%",
                  paddingTop: `${
                    (originalAddressBoundingBox.height /
                      originalAddressBoundingBox.width) *
                    100
                  }%`,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: `${originalAddressBoundingBox.width * 100}%`,
                    height: `${originalAddressBoundingBox.height * 100}%`,
                    transform: `translate(${originalAddressBoundingBox.x * 100}%, ${originalAddressBoundingBox.y * 100}%)`,
                    overflow: "hidden",
                  }}
                >
                  <NextImage
                    src={originalImageSrc}
                    alt="Original address"
                    width={originalReceipt.width}
                    height={originalReceipt.height}
                    sizes="400px"
                    style={{
                      width: `${(1 / originalAddressBoundingBox.width) * 100}%`,
                      height: `${(1 / originalAddressBoundingBox.height) * 100}%`,
                      objectFit: "cover",
                      transform: `translate(${-originalAddressBoundingBox.x * 100}%, ${-originalAddressBoundingBox.y * 100}%)`,
                    }}
                    priority
                  />
                </div>
              </div>
              <div
                style={{
                  padding: "1rem",
                  backgroundColor: "var(--background-color)",
                }}
              >
                <p style={{ fontSize: "0.9rem", color: "#666", margin: 0 }}>
                  {data.original.lines.map((line) => line.text).join(" ")}
                </p>
              </div>
            </div>
          )}
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
            style={{
              position: "relative",
              width: "100%",
              height: "500px",
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

