import React, { useEffect, useState, useMemo, useCallback } from "react";
import NextImage from "next/image";
import { api } from "../../../services/api";
import { Receipt, ReceiptApiResponse } from "../../../types/api";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import {
  detectImageFormatSupport,
  getBestImageUrl,
} from "../../../utils/imageFormat";

const isDevelopment = process.env.NODE_ENV === "development";

interface ReceiptItemProps {
  receipt: Receipt;
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean } | null;
  position: { rotation: number; topOffset: number; leftPercent: number };
  index: number;
  onLoad: () => void;
  shouldAnimate: boolean;
  fadeDelay: number;
}

const ReceiptItem = React.memo<ReceiptItemProps>(
  ({
    receipt,
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

    useEffect(() => {
      if (formatSupport && !currentSrc) {
        const bestUrl = getBestImageUrl(receipt, formatSupport);
        setCurrentSrc(bestUrl);
      }
    }, [formatSupport, receipt, currentSrc]);

    const handleImageLoad = useCallback(() => {
      setImageLoaded(true);
      onLoad();
    }, [onLoad]);

    const handleError = () => {
      const baseUrl = isDevelopment
        ? "https://dev.tylernorlund.com"
        : "https://www.tylernorlund.com";

      let fallbackUrl = "";

      if (currentSrc.includes(".avif")) {
        if (formatSupport?.supportsWebP && receipt.cdn_webp_s3_key) {
          fallbackUrl = `${baseUrl}/${receipt.cdn_webp_s3_key}`;
        } else if (receipt.cdn_s3_key) {
          fallbackUrl = `${baseUrl}/${receipt.cdn_s3_key}`;
        }
      } else if (currentSrc.includes(".webp") && receipt.cdn_s3_key) {
        fallbackUrl = `${baseUrl}/${receipt.cdn_s3_key}`;
      } else {
        setHasErrored(true);
        setImageLoaded(true);
        onLoad();
        return;
      }

      if (fallbackUrl && fallbackUrl !== currentSrc) {
        setCurrentSrc(fallbackUrl);
      } else {
        setHasErrored(true);
        setImageLoaded(true);
        onLoad();
      }
    };

    const { rotation, topOffset, leftPercent } = position;

    if (hasErrored) {
      return (
        <div
          style={{
            position: "absolute",
            width: "100px",
            left: `${leftPercent}%`,
            top: shouldAnimate && imageLoaded ? `${topOffset}px` : `${topOffset - 50}px`,
            border: "1px solid #ccc",
            backgroundColor: "var(--background-color)",
            boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
            transform: `rotate(${rotation}deg)`,
            opacity: shouldAnimate && imageLoaded ? 1 : 0,
            transition: `all 0.6s ease-out ${
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
          width: "100px",
          left: `${leftPercent}%`,
          top: shouldAnimate && imageLoaded ? `${topOffset}px` : `${topOffset - 50}px`,
          border: "1px solid #ccc",
          backgroundColor: "var(--background-color)",
          boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
          transform: `rotate(${rotation}deg)`,
          opacity: shouldAnimate && imageLoaded ? 1 : 0,
          transition: `all 0.6s ease-out ${
            shouldAnimate ? index * fadeDelay : 0
          }ms`,
          willChange: "top, opacity, transform",
        }}
      >
        {currentSrc && (
          <NextImage
            src={currentSrc}
            alt={`Receipt ${receipt.receipt_id}`}
            width={100}
            height={150}
            style={{
              width: "100%",
              height: "auto",
              display: "block",
            }}
            onLoad={handleImageLoad}
            onError={handleError}
            priority={index < 5}
          />
        )}
      </div>
    );
  }
);

ReceiptItem.displayName = "ReceiptItem";

interface ReceiptStackProps {
  maxReceipts?: number;
  pageSize?: number;
  fadeDelay?: number;
}

const ReceiptStack: React.FC<ReceiptStackProps> = ({
  maxReceipts = 40,
  pageSize = 20,
  fadeDelay = 25,
}) => {
  // Track window resize to recalculate positions
  const [windowWidth, setWindowWidth] = useState(
    typeof window !== 'undefined' ? window.innerWidth : 1024
  );

  useEffect(() => {
    const handleResize = () => {
      setWindowWidth(window.innerWidth);
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const [ref, inView] = useOptimizedInView({
    threshold: 0.1,
    triggerOnce: true,
  });

  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [formatSupport, setFormatSupport] = useState<{
    supportsAVIF: boolean;
    supportsWebP: boolean;
  } | null>(null);
  const [, setLoadedImages] = useState<Set<number>>(new Set());
  const [startAnimation, setStartAnimation] = useState(false);

  // Pre-calculate positions as percentages for responsive layout
  const positions = useMemo(() => {
    const containerHeight = 700; // Further increased to prevent clipping
    const imageWidth = 100;
    // Receipts are typically 1.5-2x taller than wide, so at 100px width, they're ~150-250px tall
    const imageHeight = 250; // Conservative estimate for receipt height
    // Account for rotation and be extra conservative
    const rotationBuffer = 50;
    const safetyMargin = 50; // Extra margin to ensure no clipping
    const maxTopPx = containerHeight - imageHeight - rotationBuffer - safetyMargin; // 700 - 250 - 50 - 50 = 350px

    // Calculate safe max left percentage based on viewport width
    // Match the CSS media query breakpoints exactly
    let containerWidth = windowWidth;
    if (windowWidth <= 480) {
      containerWidth = windowWidth * 0.9; // 90% max-width on mobile
    } else if (windowWidth <= 768) {
      containerWidth = Math.min(windowWidth, 600);
    } else if (windowWidth <= 834) {
      containerWidth = Math.min(windowWidth, 700);
    } else if (windowWidth <= 1024) {
      containerWidth = Math.min(windowWidth, 900);
    } else if (windowWidth <= 1440) {
      containerWidth = Math.min(windowWidth, 1024);
    } else {
      containerWidth = Math.min(windowWidth, 1200);
    }
    
    // Account for container padding (2rem = ~32px on desktop, 1rem = ~16px on mobile)
    const containerPadding = windowWidth <= 480 ? 16 : 32;
    const effectiveWidth = containerWidth - (containerPadding * 2);
    
    const imageWidthPercent = (imageWidth / effectiveWidth) * 100;
    const maxLeftPercent = Math.max(10, 100 - imageWidthPercent - 5); // 5% safety margin

    return Array.from({ length: maxReceipts }, (_, index) => {
      // Rotation between -20 and 20 degrees (reduced to minimize height impact)
      const rotation = Math.random() * 40 - 20;

      // Distribute receipts across the calculated safe width
      const leftPercent = Math.random() * maxLeftPercent; // 0 to safe max%
      const topOffset = Math.random() * maxTopPx; // 0 to 350px

      // Add depth bias - later receipts tend to be more centered
      const depthBias = index / maxReceipts;
      const centerPoint = maxLeftPercent / 2;
      const biasedLeft = leftPercent * (1 - depthBias * 0.4) + centerPoint * (depthBias * 0.4);
      const biasedTop = topOffset * (1 - depthBias * 0.3) + (maxTopPx / 2) * (depthBias * 0.3);
      
      // Ensure generous padding from edges - more conservative to prevent clipping
      const finalTop = Math.max(20, Math.min(biasedTop, maxTopPx - 20));

      return {
        rotation,
        topOffset: Math.round(finalTop),
        leftPercent: Math.min(biasedLeft, maxLeftPercent), // Ensure we don't exceed max
      };
    });
  }, [maxReceipts, windowWidth]);

  useEffect(() => {
    detectImageFormatSupport().then((support) => {
      setFormatSupport(support);
    });
  }, []);

  useEffect(() => {
    const loadReceipts = async () => {
      if (!formatSupport) return;

      try {
        let allReceipts: Receipt[] = [];
        let lastEvaluatedKey: any = undefined;

        // Fetch receipts in pages until we have enough
        while (allReceipts.length < maxReceipts) {
          const response: ReceiptApiResponse = await api.fetchReceipts(
            pageSize,
            lastEvaluatedKey
          );

          if (!response || !response.receipts) {
            throw new Error("Invalid response");
          }

          allReceipts = [...allReceipts, ...response.receipts];

          if (!response.lastEvaluatedKey || allReceipts.length >= maxReceipts) {
            break;
          }
          lastEvaluatedKey = response.lastEvaluatedKey;
        }

        setReceipts(allReceipts.slice(0, maxReceipts));
      } catch (error) {
        console.error("Error loading receipts:", error);
        setError(
          error instanceof Error ? error.message : "Failed to load receipts"
        );
      }
    };

    if (formatSupport) {
      loadReceipts();
    }
  }, [formatSupport, maxReceipts, pageSize]);

  // Handle individual image load
  const handleImageLoad = useCallback((index: number) => {
    setLoadedImages((prev) => new Set(prev).add(index));
  }, []);

  // Start animation when in view and receipts are loaded
  useEffect(() => {
    if (inView && receipts.length > 0 && !startAnimation) {
      setStartAnimation(true);
    }
  }, [inView, receipts.length, startAnimation]);

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

  return (
    <div
      ref={ref}
      style={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        marginTop: "6rem",
      }}
    >
      <div
        style={{
          position: "relative",
          width: "100%",
          height: "700px",
          overflow: "hidden",
        }}
      >
        {receipts.map((receipt, index) => (
          <ReceiptItem
            key={`${receipt.receipt_id}-${receipt.image_id}`}
            receipt={receipt}
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
  );
};

export default ReceiptStack;