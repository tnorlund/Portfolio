import React, { useEffect, useState, useMemo, useCallback } from "react";
import NextImage from "next/image";
import { api } from "../../../services/api";
import { Receipt, ReceiptApiResponse } from "../../../types/api";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import {
  detectImageFormatSupport,
  getBestImageUrl,
} from "../../../utils/imageFormat";
import { usePerformanceMonitor } from "../../../hooks/usePerformanceMonitor";

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
            top: `${topOffset}px`,
            border: "1px solid #ccc",
            backgroundColor: "var(--background-color)",
            boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
            transform: `rotate(${rotation}deg) translateY(${shouldAnimate && imageLoaded ? 0 : -50}px)`,
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
          width: "100px",
          left: `${leftPercent}%`,
          top: `${topOffset}px`,
          border: "1px solid #ccc",
          backgroundColor: "var(--background-color)",
          boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
          transform: `rotate(${rotation}deg) translateY(${shouldAnimate && imageLoaded ? 0 : -50}px)`,
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
        {currentSrc && (
          <NextImage
            src={currentSrc}
            alt={`Receipt ${receipt.receipt_id}`}
            width={receipt.width}
            height={receipt.height}
            sizes="100px"
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
  initialCount?: number; // Number of receipts to load immediately
}

const ReceiptStack: React.FC<ReceiptStackProps> = ({
  maxReceipts = 40,
  pageSize = 20,
  fadeDelay = 25,
  initialCount = 6,
}) => {
  // Add performance monitoring
  const { trackAPICall, trackImageLoad } = usePerformanceMonitor({
    componentName: 'ReceiptStack',
    trackRender: true,
  });

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
  const [loadingRemaining, setLoadingRemaining] = useState(false);
  const [lastEvaluatedKey, setLastEvaluatedKey] = useState<any>(null);

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
    const loadInitialReceipts = async () => {
      if (!formatSupport) return;

      try {
        // Load just the initial subset first for faster rendering
        const response: ReceiptApiResponse = await api.fetchReceipts(
          Math.min(initialCount, maxReceipts)
        );

        if (!response || !response.receipts) {
          throw new Error("Invalid response");
        }

        setReceipts(response.receipts.slice(0, initialCount));
        // Store the lastEvaluatedKey for pagination
        setLastEvaluatedKey(response.lastEvaluatedKey);

        // If we need more receipts and more data is available, load them after initial render
        if (initialCount < maxReceipts && response.lastEvaluatedKey) {
          setLoadingRemaining(true);
        }
      } catch (error) {
        console.error("Error loading initial receipts:", error);
        setError(
          error instanceof Error ? error.message : "Failed to load receipts"
        );
      }
    };

    if (formatSupport) {
      loadInitialReceipts();
    }
  }, [formatSupport, initialCount, maxReceipts]);

  // Load remaining receipts after initial set
  useEffect(() => {
    const loadRemainingReceipts = async () => {
      if (!formatSupport || !loadingRemaining || !lastEvaluatedKey) return;

      try {
        const currentReceiptsCount = receipts.length;
        if (currentReceiptsCount >= maxReceipts) {
          setLoadingRemaining(false);
          return;
        }

        const remainingNeeded = maxReceipts - currentReceiptsCount;
        const pagesNeeded = Math.ceil(remainingNeeded / pageSize);
        
        // Use the stored lastEvaluatedKey from initial fetch
        const firstPageResponse = await api.fetchReceipts(pageSize, lastEvaluatedKey);
        if (!firstPageResponse || !firstPageResponse.receipts) {
          throw new Error("Invalid response");
        }

        let allNewReceipts: Receipt[] = firstPageResponse.receipts;
        let currentKey = firstPageResponse.lastEvaluatedKey;

        // If we need more pages, fetch them in parallel batches
        if (pagesNeeded > 1 && currentKey) {
          // For simplicity, fetch remaining pages sequentially
          // (parallel would require knowing all cursor keys in advance)
          for (let i = 1; i < pagesNeeded && currentKey; i++) {
            const response = await api.fetchReceipts(pageSize, currentKey);
            if (response && response.receipts) {
              allNewReceipts = [...allNewReceipts, ...response.receipts];
              currentKey = response.lastEvaluatedKey;
            } else {
              break;
            }
          }
        }

        // Combine with existing receipts and trim to maxReceipts
        setReceipts(prevReceipts => {
          const combinedReceipts = [...prevReceipts, ...allNewReceipts].slice(0, maxReceipts);
          return combinedReceipts;
        });
        setLoadingRemaining(false);
      } catch (error) {
        console.error("Error loading remaining receipts:", error);
        setLoadingRemaining(false);
      }
    };

    if (loadingRemaining && lastEvaluatedKey) {
      // Delay loading remaining receipts until after initial render
      const timer = setTimeout(loadRemainingReceipts, 100);
      return () => clearTimeout(timer);
    }
  }, [formatSupport, loadingRemaining, maxReceipts, pageSize, lastEvaluatedKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // Note: receipts.length is intentionally NOT included to prevent infinite loop

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