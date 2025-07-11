import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import NextImage from "next/image";
import { api } from "../../../services/api";
import { Receipt, ReceiptApiResponse } from "../../../types/api";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import {
  detectImageFormatSupport,
  getBestImageUrl,
  ImageSize,
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
      if (formatSupport) {
        // Use thumbnail size for ReceiptStack since receipts are displayed at 100px
        const bestUrl = getBestImageUrl(receipt, formatSupport, 'thumbnail');
        setCurrentSrc(bestUrl);
      }
    }, [formatSupport, receipt]); // currentSrc removed to prevent infinite loop

    const handleImageLoad = useCallback(() => {
      setImageLoaded(true);
      onLoad();
    }, [onLoad]);

    const handleError = () => {
      // Try progressively larger sizes if thumbnail fails
      if (currentSrc.includes("_thumbnail")) {
        // Try small size
        const smallUrl = getBestImageUrl(receipt, formatSupport || { supportsAVIF: false, supportsWebP: false }, 'small');
        if (smallUrl && smallUrl !== currentSrc) {
          setCurrentSrc(smallUrl);
          return;
        }
      } else if (currentSrc.includes("_small")) {
        // Try medium size
        const mediumUrl = getBestImageUrl(receipt, formatSupport || { supportsAVIF: false, supportsWebP: false }, 'medium');
        if (mediumUrl && mediumUrl !== currentSrc) {
          setCurrentSrc(mediumUrl);
          return;
        }
      } else if (currentSrc.includes("_medium")) {
        // Try full size
        const fullUrl = getBestImageUrl(receipt, formatSupport || { supportsAVIF: false, supportsWebP: false }, 'full');
        if (fullUrl && fullUrl !== currentSrc) {
          setCurrentSrc(fullUrl);
          return;
        }
      }

      // If all sizes fail, mark as errored
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
  // TEMPORARILY DISABLED: Performance monitor causes infinite render loop
  // const { trackAPICall, trackImageLoad } = usePerformanceMonitor({
  //   componentName: 'ReceiptStack',
  //   trackRender: true,
  // });
  const trackAPICall = async <T,>(endpoint: string, apiCall: () => Promise<T>): Promise<T> => apiCall();
  const trackImageLoad = () => {};

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
  const isLoadingRef = useRef(false);

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
      if (!formatSupport || !loadingRemaining || !lastEvaluatedKey || isLoadingRef.current) return;
      
      isLoadingRef.current = true;

      try {
        // Get current count without modifying state
        let currentReceiptsCount = 0;
        setReceipts(prevReceipts => {
          currentReceiptsCount = prevReceipts.length;
          return prevReceipts; // Don't modify state, just read it
        });

        // Check if we already have enough receipts
        if (currentReceiptsCount >= maxReceipts) {
          setLoadingRemaining(false);
          isLoadingRef.current = false;
          return;
        }

        // Calculate remaining needed based on actual current count
        const remainingNeeded = maxReceipts - currentReceiptsCount;
        const pagesNeeded = Math.ceil(remainingNeeded / pageSize);
        
        // Use the stored lastEvaluatedKey from initial fetch
        const firstPageResponse = await api.fetchReceipts(pageSize, lastEvaluatedKey);
        if (!firstPageResponse || !firstPageResponse.receipts) {
          throw new Error("Invalid response");
        }

        let allNewReceipts: Receipt[] = firstPageResponse.receipts;
        let currentKey = firstPageResponse.lastEvaluatedKey;

        // If we need more pages, fetch them sequentially
        if (pagesNeeded > 1 && currentKey) {
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
        isLoadingRef.current = false;
      } catch (error) {
        console.error("Error loading remaining receipts:", error);
        setLoadingRemaining(false);
        isLoadingRef.current = false;
      }
    };

    if (loadingRemaining && lastEvaluatedKey && !isLoadingRef.current) {
      // Delay loading remaining receipts until after initial render
      const timer = setTimeout(loadRemainingReceipts, 100);
      return () => clearTimeout(timer);
    }
  }, [formatSupport, loadingRemaining, maxReceipts, pageSize, lastEvaluatedKey, initialCount]); // initialCount is stable

  // Handle individual image load
  const handleImageLoad = useCallback((index: number) => {
    setLoadedImages((prev) => new Set(prev).add(index));
  }, []);

  // Start animation when in view and receipts are loaded
  useEffect(() => {
    if (inView && receipts.length > 0 && !startAnimation) {
      setStartAnimation(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, receipts.length]); // startAnimation removed to prevent infinite loop

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