import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import NextImage from "next/image";
import { api } from "../../../services/api";
import { Image, ImagesApiResponse } from "../../../types/api";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import {
  detectImageFormatSupport,
  getBestImageUrl,
} from "../../../utils/imageFormat";

const isDevelopment = process.env.NODE_ENV === "development";

interface ImageItemProps {
  image: Image;
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean } | null;
  position: { rotation: number; topOffset: number; leftPercent: number };
  index: number;
  onLoad: () => void;
  shouldAnimate: boolean;
  fadeDelay: number;
}

const ImageItem = React.memo<ImageItemProps>(
  ({
    image,
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
        const bestUrl = getBestImageUrl(image, formatSupport);
        setCurrentSrc(bestUrl);
      }
    }, [formatSupport, image]); // currentSrc removed to prevent infinite loop

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
        if (formatSupport?.supportsWebP && image.cdn_webp_s3_key) {
          fallbackUrl = `${baseUrl}/${image.cdn_webp_s3_key}`;
        } else if (image.cdn_s3_key) {
          fallbackUrl = `${baseUrl}/${image.cdn_s3_key}`;
        }
      } else if (currentSrc.includes(".webp") && image.cdn_s3_key) {
        fallbackUrl = `${baseUrl}/${image.cdn_s3_key}`;
      } else {
        setHasErrored(true);
        setImageLoaded(true); // Consider it "loaded" even on error
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
            width: "150px",
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
          width: "150px",
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
            alt={`Image ${image.image_id}`}
            width={image.width}
            height={image.height}
            sizes="150px"
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

ImageItem.displayName = "ImageItem";

interface ImageStackProps {
  maxImages?: number;
  pageSize?: number;
  fadeDelay?: number; // Delay between each image fade in (ms)
  initialCount?: number; // Number of images to load immediately
}

const ImageStack: React.FC<ImageStackProps> = ({
  maxImages = 20,
  pageSize = 20,
  fadeDelay = 50,
  initialCount = 6,
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

  const [images, setImages] = useState<Image[]>([]);
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
    const containerHeight = 500; // Height for image container
    const imageWidth = 150; // Display width
    // Based on actual dimensions: photos ~1.33 ratio, scans ~1.41 ratio
    // Average is about 1.37, so at 150px width, height is ~206px
    const imageHeight = 206;
    
    // Calculate safe max left percentage based on viewport width
    // We need to ensure image doesn't exceed container bounds
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
    
    const maxTopPx = containerHeight - imageHeight - 50; // 500 - 206 - 50 = 244px (with buffer)
    
    return Array.from({ length: maxImages }, (_, index) => {
      // Rotation for dynamic look
      const rotation = Math.random() * 40 - 20; // Reduced rotation to minimize overflow
      
      // Distribute images across the width, accounting for image width
      // Random distribution from 0% to calculated max
      const leftPercent = Math.random() * maxLeftPercent; // 0 to safe max%
      const topOffset = Math.random() * maxTopPx; // 0 to 244px
      
      // Add some bias to create depth - later images tend to be more centered
      const depthBias = index / maxImages;
      const centerPoint = maxLeftPercent / 2;
      const biasedLeft = leftPercent * (1 - depthBias * 0.4) + centerPoint * (depthBias * 0.4); // Bias toward center
      const biasedTop = topOffset * (1 - depthBias * 0.3) + (maxTopPx / 2) * (depthBias * 0.3);
      
      // Ensure minimum padding from edges
      const finalTop = Math.max(20, Math.min(biasedTop, maxTopPx - 20));
      
      return { 
        rotation, 
        topOffset: Math.round(finalTop),
        leftPercent: Math.min(biasedLeft, maxLeftPercent) // Ensure we don't exceed max
      };
    });
  }, [maxImages, windowWidth]);

  useEffect(() => {
    detectImageFormatSupport().then((support) => {
      setFormatSupport(support);
    });
  }, []);

  useEffect(() => {
    const loadInitialImages = async () => {
      if (!formatSupport) return;

      try {
        // Load just the initial subset first for faster rendering
        const response: ImagesApiResponse = await api.fetchImages(
          Math.min(initialCount, maxImages)
        );

        if (!response || !response.images) {
          throw new Error("Invalid response");
        }

        setImages(response.images.slice(0, initialCount));
        // Store the lastEvaluatedKey for pagination
        setLastEvaluatedKey(response.lastEvaluatedKey);

        // If we need more images and more data is available, load them after initial render
        if (initialCount < maxImages && response.lastEvaluatedKey) {
          setLoadingRemaining(true);
        }
      } catch (error) {
        console.error("Error loading initial images:", error);
        setError(
          error instanceof Error ? error.message : "Failed to load images"
        );
      }
    };

    if (formatSupport) {
      loadInitialImages();
    }
  }, [formatSupport, initialCount, maxImages]);

  // Load remaining images after initial set
  useEffect(() => {
    const loadRemainingImages = async () => {
      if (!formatSupport || !loadingRemaining || !lastEvaluatedKey || isLoadingRef.current) return;
      
      isLoadingRef.current = true;

      try {
        // Get current count without modifying state
        let currentImagesCount = 0;
        setImages(prevImages => {
          currentImagesCount = prevImages.length;
          return prevImages; // Don't modify state, just read it
        });

        // Check if we already have enough images
        if (currentImagesCount >= maxImages) {
          setLoadingRemaining(false);
          isLoadingRef.current = false;
          return;
        }

        // Calculate remaining needed based on actual current count
        const remainingNeeded = maxImages - currentImagesCount;
        const pagesNeeded = Math.ceil(remainingNeeded / pageSize);
        
        // Use the stored lastEvaluatedKey from initial fetch
        const firstPageResponse = await api.fetchImages(pageSize, lastEvaluatedKey);
        if (!firstPageResponse || !firstPageResponse.images) {
          throw new Error("Invalid response");
        }

        let allNewImages: Image[] = firstPageResponse.images;
        let currentKey = firstPageResponse.lastEvaluatedKey;

        // If we need more pages, fetch them sequentially
        if (pagesNeeded > 1 && currentKey) {
          for (let i = 1; i < pagesNeeded && currentKey; i++) {
            const response = await api.fetchImages(pageSize, currentKey);
            if (response && response.images) {
              allNewImages = [...allNewImages, ...response.images];
              currentKey = response.lastEvaluatedKey;
            } else {
              break;
            }
          }
        }

        // Combine with existing images and trim to maxImages
        setImages(prevImages => {
          const combinedImages = [...prevImages, ...allNewImages].slice(0, maxImages);
          return combinedImages;
        });
        setLoadingRemaining(false);
        isLoadingRef.current = false;
      } catch (error) {
        console.error("Error loading remaining images:", error);
        setLoadingRemaining(false);
        isLoadingRef.current = false;
      }
    };

    if (loadingRemaining && lastEvaluatedKey && !isLoadingRef.current) {
      // Delay loading remaining images until after initial render
      const timer = setTimeout(loadRemainingImages, 100);
      return () => clearTimeout(timer);
    }
  }, [formatSupport, loadingRemaining, maxImages, pageSize, lastEvaluatedKey, initialCount]); // initialCount is stable

  // Handle individual image load
  const handleImageLoad = useCallback((index: number) => {
    setLoadedImages((prev) => new Set(prev).add(index));
  }, []);

  // Start animation when in view and images are loaded
  useEffect(() => {
    if (inView && images.length > 0 && !startAnimation) {
      setStartAnimation(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, images.length]); // startAnimation removed to prevent infinite loop

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
          height: "600px",
          overflow: "hidden",
        }}
      >
        {images.map((image, index) => (
          <ImageItem
            key={image.image_id}
            image={image}
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

export default ImageStack;
