// ReceiptStack.tsx
import React, { useEffect, useState, useCallback } from "react";
import Image from "next/image";
import { api } from "../../../services/api";
import { Receipt, ReceiptApiResponse } from "../../../types/api";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import { useTransition, animated } from "@react-spring/web";
import {
  detectImageFormatSupport,
  getBestImageUrl,
} from "../../../utils/imageFormat";

const isDevelopment = process.env.NODE_ENV === "development";

// Component with automatic fallback handling
interface ReceiptImageProps {
  receipt: Receipt;
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean } | null;
  isClient: boolean;
  style: any;
}

const ReceiptImage: React.FC<ReceiptImageProps> = ({
  receipt,
  formatSupport,
  isClient,
  style,
}) => {
  const [currentSrc, setCurrentSrc] = useState<string>("");
  const [hasErrored, setHasErrored] = useState<boolean>(false);

  // Set initial URL using the same logic as pre-loading
  useEffect(() => {
    if (formatSupport && !currentSrc) {
      const bestUrl = getBestImageUrl(receipt, formatSupport);
      setCurrentSrc(bestUrl);
      setHasErrored(false);
    }
  }, [formatSupport, receipt, currentSrc]);

  const handleError = () => {
    const baseUrl = isDevelopment
      ? "https://dev.tylernorlund.com"
      : "https://www.tylernorlund.com";

    let fallbackUrl = "";

    if (currentSrc.includes(".avif")) {
      // AVIF failed, try WebP
      if (formatSupport?.supportsWebP && receipt.cdn_webp_s3_key) {
        fallbackUrl = `${baseUrl}/${receipt.cdn_webp_s3_key}`;
      } else {
        // No WebP, go to JPEG
        fallbackUrl = `${baseUrl}/${receipt.cdn_s3_key}`;
      }
    } else if (currentSrc.includes(".webp")) {
      // WebP failed, try JPEG
      fallbackUrl = `${baseUrl}/${receipt.cdn_s3_key}`;
    } else {
      // JPEG failed, nothing else to try
      setHasErrored(true);
      return;
    }

    if (fallbackUrl && fallbackUrl !== currentSrc) {
      setCurrentSrc(fallbackUrl);
    } else {
      setHasErrored(true);
    }
  };

  if (hasErrored) {
    return (
      <animated.div
        style={{
          position: "absolute",
          width: "100px",
          border: "1px solid #ccc",
          backgroundColor: "var(--background-color)",
          boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#999",
          fontSize: "12px",
          ...style,
        }}
      >
        Failed
      </animated.div>
    );
  }

  // Don't render if no valid src
  if (!currentSrc) {
    return null;
  }

  return (
    <animated.div
      style={{
        position: "absolute",
        width: "100px",
        border: "1px solid #ccc",
        backgroundColor: "var(--background-color)",
        boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
        ...style,
      }}
    >
      <Image
        src={currentSrc}
        alt={`Receipt ${receipt.receipt_id}`}
        width={100}
        height={150}
        style={{
          width: "100%",
          height: "auto",
          display: "block",
        }}
        onError={handleError}
      />
    </animated.div>
  );
};

const ReceiptStack: React.FC = () => {
  const [ref, inView] = useOptimizedInView({
    threshold: 0.1,
    triggerOnce: true,
  });

  const [prefetchedReceipts, setPrefetchedReceipts] = useState<Receipt[]>([]);
  const [prefetchedRotations, setPrefetchedRotations] = useState<number[]>([]);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [rotations, setRotations] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formatSupport, setFormatSupport] = useState<{
    supportsAVIF: boolean;
    supportsWebP: boolean;
  } | null>(null);
  const [isClient, setIsClient] = useState(false);

  // States for image pre-loading
  const [imagesLoading, setImagesLoading] = useState(false);
  const [imagesLoaded, setImagesLoaded] = useState(false);
  const [loadingProgress, setLoadingProgress] = useState({
    loaded: 0,
    total: 0,
  });

  // Configuration
  const maxReceipts = 40;
  const pageSize = 20;

  // Animate new items as they are added to `receipts`
  const transitions = useTransition(receipts, {
    from: { opacity: 0, transform: "translate(0px, -50px) rotate(0deg)" },
    enter: (item, index) => {
      const rotation = rotations[index] ?? 0;
      const topOffset = (Math.random() > 0.5 ? 1 : -1) * index * 1.5;
      const leftOffset = (Math.random() > 0.5 ? 1 : -1) * index * 1.5;
      return {
        opacity: 1,
        transform: `translate(${leftOffset}px, ${topOffset}px) rotate(${rotation}deg)`,
        delay: index * 100,
      };
    },
    keys: (item) => `${item.receipt_id}-${item.image_id}`,
  });

  // Ensure client-side hydration consistency
  useEffect(() => {
    setIsClient(true);
  }, []);

  // Detect format support on component mount (only client-side)
  useEffect(() => {
    if (!isClient) return;

    detectImageFormatSupport().then((support) => {
      setFormatSupport(support);
    });
  }, [isClient]);

  useEffect(() => {
    const loadAllReceipts = async () => {
      setLoading(true);
      setError(null);

      let lastEvaluatedKey: any | undefined;
      let totalFetched = 0;

      const allReceipts: Receipt[] = [];
      const allRotations: number[] = [];

      try {
        while (totalFetched < maxReceipts) {
          const response: ReceiptApiResponse = await api.fetchReceipts(
            pageSize,
            lastEvaluatedKey
          );

          if (!response || typeof response !== "object") {
            console.error("Invalid response structure:", response);
            break;
          }

          const newReceipts = response.receipts || [];

          if (!Array.isArray(newReceipts)) {
            console.error("Response receipts is not an array:", newReceipts);
            break;
          }

          allReceipts.push(...newReceipts);
          allRotations.push(...newReceipts.map(() => Math.random() * 60 - 30));

          totalFetched += newReceipts.length;

          if (!response.lastEvaluatedKey) {
            break;
          }
          lastEvaluatedKey = response.lastEvaluatedKey;
        }

        setPrefetchedReceipts(allReceipts.slice(0, maxReceipts));
        setPrefetchedRotations(allRotations.slice(0, maxReceipts));
      } catch (error) {
        console.error("Error loading receipts:", error);
        setError(
          error instanceof Error ? error.message : "Failed to load receipts"
        );
        setPrefetchedReceipts([]);
        setPrefetchedRotations([]);
      } finally {
        setLoading(false);
      }
    };

    loadAllReceipts();
  }, [maxReceipts, pageSize]);

  // Pre-load all images before starting animation
  const preloadAllImages = useCallback(
    async (receiptsToLoad: Receipt[]) => {
      if (!formatSupport || receiptsToLoad.length === 0) return;

      setImagesLoading(true);
      setImagesLoaded(false);
      setLoadingProgress({ loaded: 0, total: receiptsToLoad.length });

      const imagePromises = receiptsToLoad.map((receipt, index) => {
        return new Promise<void>((resolve) => {
          const imageUrl = getBestImageUrl(receipt, formatSupport);
          const img = new (window as any).Image() as HTMLImageElement;

          img.crossOrigin = "anonymous";

          const handleLoad = () => {
            setLoadingProgress((prev) => ({
              loaded: prev.loaded + 1,
              total: prev.total,
            }));
            resolve();
          };

          const handleError = () => {
            setLoadingProgress((prev) => ({
              loaded: prev.loaded + 1,
              total: prev.total,
            }));
            resolve(); // Still resolve to not block other images
          };

          img.onload = handleLoad;
          img.onerror = handleError;
          img.src = imageUrl;
        });
      });

      try {
        await Promise.all(imagePromises);
        setImagesLoaded(true);
      } catch (error) {
        console.warn("Some images failed to pre-load:", error);
        setImagesLoaded(true); // Still start animation even if some images failed
      } finally {
        setImagesLoading(false);
      }
    },
    [formatSupport]
  );

  // When the user scrolls to this component, pre-load images first, then start animation
  useEffect(() => {
    if (
      inView &&
      receipts.length === 0 &&
      prefetchedReceipts.length &&
      formatSupport !== null &&
      !imagesLoading &&
      !imagesLoaded
    ) {
      preloadAllImages(prefetchedReceipts);
    }
  }, [
    inView,
    prefetchedReceipts,
    receipts.length,
    formatSupport,
    imagesLoading,
    imagesLoaded,
    preloadAllImages,
  ]);

  // Start animation only after images are pre-loaded
  useEffect(() => {
    if (imagesLoaded && receipts.length === 0 && prefetchedReceipts.length) {
      setReceipts(prefetchedReceipts);
      setRotations(prefetchedRotations);
    }
  }, [imagesLoaded, receipts.length, prefetchedReceipts, prefetchedRotations]);

  // Show loading state once in view
  if (inView && (loading || imagesLoading)) {
    const loadingMessage = loading
      ? "Loading receipts..."
      : imagesLoading
      ? `Loading images... (${loadingProgress.loaded}/${loadingProgress.total})`
      : "Loading receipts...";

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
        <p>{loadingMessage}</p>
      </div>
    );
  }

  // Show error state once in view
  if (inView && error) {
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

  // Show empty state if data loaded but there are no receipts
  if (inView && !loading && receipts.length === 0) {
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
        <p>No receipts found</p>
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
          width: "150px",
          minHeight: "475px",
        }}
      >
        {transitions((style, receipt) => {
          if (!receipt) return null;

          return (
            <ReceiptImage
              key={`${receipt.receipt_id}-${receipt.image_id}`}
              receipt={receipt}
              formatSupport={formatSupport}
              isClient={isClient}
              style={style}
            />
          );
        })}
      </div>
    </div>
  );
};

export default ReceiptStack;
