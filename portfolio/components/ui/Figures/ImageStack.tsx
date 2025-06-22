import React, { useEffect, useState, useMemo, useCallback } from "react";
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
      if (formatSupport && !currentSrc) {
        const bestUrl = getBestImageUrl(image, formatSupport);
        setCurrentSrc(bestUrl);
      }
    }, [formatSupport, image, currentSrc]);

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
            alt={`Image ${image.image_id}`}
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

ImageItem.displayName = "ImageItem";

interface ImageStackProps {
  maxImages?: number;
  pageSize?: number;
  fadeDelay?: number; // Delay between each image fade in (ms)
}

const ImageStack: React.FC<ImageStackProps> = ({
  maxImages = 20,
  pageSize = 20,
  fadeDelay = 50,
}) => {
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

  // Pre-calculate positions as percentages for responsive layout
  const positions = useMemo(() => {
    const containerHeight = 475;
    const imageWidth = 100;
    const imageHeight = 150; // Approximate height
    
    // For percentage-based positioning
    // Assuming container is roughly 600-800px wide (typical article width)
    // We'll use percentages to keep images distributed nicely
    
    const maxTopPx = containerHeight - imageHeight; // 325px
    
    return Array.from({ length: maxImages }, (_, index) => {
      // More varied rotation for dynamic look
      const rotation = Math.random() * 50 - 25;
      
      // Distribute images across the full width using percentages
      // Random distribution from 0% to roughly 85% (leaving 15% for image width)
      const leftPercent = Math.random() * 85; // 0 to 85%
      const topOffset = Math.random() * maxTopPx; // 0 to 325px
      
      // Add some bias to create depth - later images tend to be more centered
      // This creates a natural stacking effect
      const depthBias = index / maxImages;
      const biasedLeft = leftPercent * (1 - depthBias * 0.4) + 42.5 * (depthBias * 0.4); // Bias toward center (42.5%)
      const biasedTop = topOffset * (1 - depthBias * 0.3) + (maxTopPx / 2) * (depthBias * 0.3);
      
      return { 
        rotation, 
        topOffset: Math.round(biasedTop),
        leftPercent: biasedLeft // Store as percentage
      };
    });
  }, [maxImages]);

  useEffect(() => {
    detectImageFormatSupport().then((support) => {
      setFormatSupport(support);
    });
  }, []);

  useEffect(() => {
    const loadImages = async () => {
      if (!formatSupport) return;

      try {
        let allImages: Image[] = [];
        let lastEvaluatedKey: any = undefined;

        // Fetch images in pages until we have enough
        while (allImages.length < maxImages) {
          const response: ImagesApiResponse = await api.fetchImages(
            pageSize,
            lastEvaluatedKey
          );

          if (!response || !response.images) {
            throw new Error("Invalid response");
          }

          allImages = [...allImages, ...response.images];

          if (!response.lastEvaluatedKey || allImages.length >= maxImages) {
            break;
          }
          lastEvaluatedKey = response.lastEvaluatedKey;
        }

        setImages(allImages.slice(0, maxImages));
      } catch (error) {
        console.error("Error loading images:", error);
        setError(
          error instanceof Error ? error.message : "Failed to load images"
        );
      }
    };

    if (formatSupport) {
      loadImages();
    }
  }, [formatSupport, maxImages, pageSize]);

  // Handle individual image load
  const handleImageLoad = useCallback((index: number) => {
    setLoadedImages((prev) => new Set(prev).add(index));
  }, []);

  // Start animation when in view and images are loaded
  useEffect(() => {
    if (inView && images.length > 0 && !startAnimation) {
      setStartAnimation(true);
    }
  }, [inView, images.length, startAnimation]);

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
          height: "475px",
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
