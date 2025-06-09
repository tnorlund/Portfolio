import React, { useEffect, useState, Fragment } from "react";

import { api } from "../../../services/api";
import {
  ImageDetailsApiResponse,
  type Image as ImageType,
} from "../../../types/api";
import { useSpring, useTransition, animated } from "@react-spring/web";

const isDevelopment = process.env.NODE_ENV === "development";

// Browser format detection utilities
const detectImageFormatSupport = (): Promise<{
  supportsAVIF: boolean;
  supportsWebP: boolean;
}> => {
  return new Promise((resolve) => {
    const userAgent = navigator.userAgent;

    // Safari version detection
    const getSafariVersion = (): number | null => {
      if (userAgent.includes("Chrome")) return null; // Chrome has Safari in UA, exclude it

      const safariMatch = userAgent.match(/Version\/([0-9.]+).*Safari/);
      if (safariMatch) {
        return parseFloat(safariMatch[1]);
      }
      return null;
    };

    const isChrome =
      userAgent.includes("Chrome") && userAgent.includes("Google Chrome");
    const isFirefox = userAgent.includes("Firefox");
    const safariVersion = getSafariVersion();
    const isSafari = safariVersion !== null;

    // WebP support detection
    let supportsWebP = false;

    if (isChrome || isFirefox) {
      // Chrome and Firefox have excellent WebP support
      supportsWebP = true;
    } else if (isSafari && safariVersion && safariVersion >= 14) {
      // Safari 14+ supports WebP (macOS Big Sur, iOS 14)
      supportsWebP = true;
    } else {
      // Try canvas test as fallback
      try {
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          const webpDataUrl = canvas.toDataURL("image/webp", 0.5);
          supportsWebP = webpDataUrl.indexOf("data:image/webp") === 0;
        }
      } catch (error) {
        supportsWebP = false;
      }
    }

    // AVIF support detection
    const detectAVIF = (): Promise<boolean> => {
      if (isChrome) {
        // Chrome 85+ supports AVIF (September 2020)
        const chromeMatch = userAgent.match(/Chrome\/([0-9]+)/);
        if (chromeMatch && parseInt(chromeMatch[1]) >= 85) {
          return Promise.resolve(true);
        }
      }

      if (isFirefox) {
        // Firefox 93+ supports AVIF (October 2021)
        const firefoxMatch = userAgent.match(/Firefox\/([0-9]+)/);
        if (firefoxMatch && parseInt(firefoxMatch[1]) >= 93) {
          return Promise.resolve(true);
        }
      }

      if (isSafari && safariVersion && safariVersion >= 16.4) {
        // Safari 16.4+ supports AVIF (March 2023)
        return Promise.resolve(true);
      }

      // Fallback to image test for unknown browsers or older versions
      return new Promise<boolean>((resolveAVIF) => {
        const img = new Image();
        img.onload = () => resolveAVIF(true);
        img.onerror = () => resolveAVIF(false);
        img.src =
          "data:image/avif;base64,AAAAIGZ0eXBhdmlmAAAAAGF2aWZtaWYxbWlhZk1BMUIAAADybWV0YQAAAAAAAAAoaGRscgAAAAAAAAAAcGljdAAAAAAAAAAAAAAAAGxpYmF2aWYAAAAADnBpdG0AAAAAAAEAAAAeaWxvYwAAAABEAAABAAEAAAABAAABGgAAAB0AAAAoaWluZgAAAAAAAQAAABppbmZlAgAAAAABAABhdjAxQ29sb3IAAAAAamlwcnAAAABLaXBjbwAAABRpc3BlAAAAAAAAAAEAAAABAAAAEHBpeGkAAAAAAwgICAAAAAxhdjFDgQ0MAAAAABNjb2xybmNseAACAAIAAYAAAAAXaXBtYQAAAAAAAAABAAEEAQKDBAAAACVtZGF0EgAKCBgABogQEAwgMg8f8D///8WfhwB8+ErK42A=";
      });
    };

    detectAVIF().then((supportsAVIF) => {
      resolve({ supportsAVIF, supportsWebP });
    });
  });
};

// Get the best available image URL based on browser support and available formats
const getBestImageUrl = (
  image: ImageType,
  formatSupport: { supportsAVIF: boolean; supportsWebP: boolean }
): string => {
  const baseUrl = isDevelopment
    ? "https://dev.tylernorlund.com"
    : "https://www.tylernorlund.com";

  // Try AVIF first (best compression)
  if (formatSupport.supportsAVIF && image.cdn_avif_s3_key) {
    return `${baseUrl}/${image.cdn_avif_s3_key}`;
  }

  // Try WebP second (good compression, wide support)
  if (formatSupport.supportsWebP && image.cdn_webp_s3_key) {
    return `${baseUrl}/${image.cdn_webp_s3_key}`;
  }

  // Fallback to JPEG (universal support)
  return `${baseUrl}/${image.cdn_s3_key}`;
};

// AnimatedLineBox: already defined for words
interface AnimatedLineBoxProps {
  line: any; // Adjust type as needed
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedLineBox: React.FC<AnimatedLineBoxProps> = ({
  line,
  svgWidth,
  svgHeight,
  delay,
}) => {
  // Convert normalized coordinates to absolute pixel values.
  const x1 = line.top_left.x * svgWidth;
  const y1 = (1 - line.top_left.y) * svgHeight;
  const x2 = line.top_right.x * svgWidth;
  const y2 = (1 - line.top_right.y) * svgHeight;
  const x3 = line.bottom_right.x * svgWidth;
  const y3 = (1 - line.bottom_right.y) * svgHeight;
  const x4 = line.bottom_left.x * svgWidth;
  const y4 = (1 - line.bottom_left.y) * svgHeight;
  const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

  // Compute the polygon's centroid.
  const centroidX = (x1 + x2 + x3 + x4) / 4;
  const centroidY = (y1 + y2 + y3 + y4) / 4;

  // Animate the polygon scaling from 0 to 1, with the centroid as the origin.
  const polygonSpring = useSpring({
    from: { transform: "scale(0)" },
    to: { transform: "scale(1)" },
    delay: delay,
    config: { duration: 800 },
  });

  // Animate the centroid marker:
  // 1. Fade in at the computed centroid.
  // 2. Then animate its y coordinate to mid‑Y.
  const centroidSpring = useSpring({
    from: { opacity: 0, cy: centroidY },
    to: async (next) => {
      await next({ opacity: 1, cy: centroidY, config: { duration: 300 } });
      await next({ cy: svgHeight / 2, config: { duration: 800 } });
    },
    delay: delay + 30,
  });

  return (
    <>
      <animated.polygon
        style={{
          ...polygonSpring,
          transformOrigin: "50% 50%",
          transformBox: "fill-box",
        }}
        points={points}
        fill="none"
        stroke="red"
        strokeWidth="2"
      />
      <animated.circle
        cx={centroidX}
        cy={centroidSpring.cy}
        r={10}
        fill="red"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

// AnimatedReceipt: component for receipt bounding box and centroid animation
interface AnimatedReceiptProps {
  receipt: any; // Adjust type accordingly
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedReceipt: React.FC<AnimatedReceiptProps> = ({
  receipt,
  svgWidth,
  svgHeight,
  delay,
}) => {
  // Compute the receipt bounding box corners.
  const x1 = receipt.top_left.x * svgWidth;
  const y1 = (1 - receipt.top_left.y) * svgHeight;
  const x2 = receipt.top_right.x * svgWidth;
  const y2 = (1 - receipt.top_right.y) * svgHeight;
  const x3 = receipt.bottom_right.x * svgWidth;
  const y3 = (1 - receipt.bottom_right.y) * svgHeight;
  const x4 = receipt.bottom_left.x * svgWidth;
  const y4 = (1 - receipt.bottom_left.y) * svgHeight;
  const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;

  // Compute the centroid of the receipt bounding box.
  const centroidX = (x1 + x2 + x3 + x4) / 4;
  const centroidY = (y1 + y2 + y3 + y4) / 4;
  const midY = svgHeight / 2;

  // Animate the receipt bounding box to fade in.
  const boxSpring = useSpring({
    from: { opacity: 0 },
    to: { opacity: 1 },
    delay: delay,
    config: { duration: 800 },
  });

  // Animate the receipt centroid marker:
  // Start at midY, then animate to the computed receipt centroid.
  const centroidSpring = useSpring({
    from: { opacity: 0, cy: midY },
    to: async (next) => {
      await next({ opacity: 1, cy: midY, config: { duration: 400 } });
      await next({ cy: centroidY, config: { duration: 800 } });
    },
    delay: delay, // Same delay as the bounding box fade in.
  });

  return (
    <>
      <animated.polygon
        style={boxSpring}
        points={points}
        fill="none"
        stroke="blue"
        strokeWidth="4"
      />
      <animated.circle
        cx={centroidX}
        cy={centroidSpring.cy}
        r={10}
        fill="blue"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

// Main ImageBoundingBox component
const ImageBoundingBox: React.FC = () => {
  const [imageDetails, setImageDetails] =
    useState<ImageDetailsApiResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [formatSupport, setFormatSupport] = useState<{
    supportsAVIF: boolean;
    supportsWebP: boolean;
  } | null>(null);
  const [isClient, setIsClient] = useState(false);
  const [resetKey, setResetKey] = useState(0);

  // Ensure client-side hydration consistency
  useEffect(() => {
    setIsClient(true);
  }, []);

  useEffect(() => {
    if (!isClient) return; // Only run on client-side

    const loadImageDetails = async () => {
      try {
        // Run format detection and API call in parallel
        const [details, support] = await Promise.all([
          api.fetchRandomImageDetails(),
          detectImageFormatSupport(),
        ]);

        setImageDetails(details);
        setFormatSupport(support);
      } catch (err) {
        console.error("Error loading image details:", err);
        setError(err as Error);
      }
    };

    loadImageDetails();
  }, [isClient]);

  // Reserve default dimensions while waiting for the API.
  const defaultSvgWidth = 400;
  const defaultSvgHeight = 565.806;

  // Extract lines and receipts
  const lines = imageDetails?.lines ?? [];
  const receipts = imageDetails?.receipts ?? [];

  // Animate word bounding boxes using a transition.
  const lineTransitions = useTransition(lines, {
    // Include resetKey in the key so that each item gets a new key on reset.
    keys: (line) => `${resetKey}-${line.line_id}`,
    from: { opacity: 0, transform: "scale(0.8)" },
    enter: (item, index) => ({
      opacity: 1,
      transform: "scale(1)",
      delay: index * 30,
    }),
    config: { duration: 800 },
  });

  // Compute the total delay for word animations.
  const totalDelayForLines =
    lines.length > 0 ? (lines.length - 1) * 30 + 1130 : 0;

  // Use the first image from the API.
  const firstImage = imageDetails?.image;

  // Get the optimal image URL based on browser support and available formats
  // Use fallback URL during SSR/initial render to prevent hydration mismatch
  const cdnUrl =
    firstImage && formatSupport && isClient
      ? getBestImageUrl(firstImage, formatSupport)
      : firstImage
      ? `${
          isDevelopment
            ? "https://dev.tylernorlund.com"
            : "https://www.tylernorlund.com"
        }/${firstImage.cdn_s3_key}`
      : "";

  // When imageDetails is loaded, compute these values;
  // otherwise, fall back on default dimensions.
  const svgWidth = firstImage ? firstImage.width : defaultSvgWidth;
  const svgHeight = firstImage ? firstImage.height : defaultSvgHeight;

  // Scale the displayed SVG (using the API data if available).
  const maxDisplayWidth = 400;
  const scaleFactor = Math.min(1, maxDisplayWidth / svgWidth);
  const displayWidth = svgWidth * scaleFactor;
  const displayHeight = svgHeight * scaleFactor;

  if (error) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          minHeight: displayHeight,
          alignItems: "center",
        }}
      >
        Error loading image details
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          minHeight: displayHeight,
          alignItems: "center",
        }}
      >
        <div
          style={{
            height: displayHeight,
            width: displayWidth,
            borderRadius: "15px",
            overflow: "hidden",
          }}
        >
          {imageDetails && formatSupport ? (
            <svg
              key={resetKey}
              onClick={() => setResetKey((k) => k + 1)}
              viewBox={`0 0 ${svgWidth} ${svgHeight}`}
              width={displayWidth}
              height={displayHeight}
            >
              <image
                href={cdnUrl}
                x="0"
                y="0"
                width={svgWidth}
                height={svgHeight}
              />

              {/* Render animated word bounding boxes (via transition) */}
              {lineTransitions((style, line) => {
                const x1 = line.top_left.x * svgWidth;
                const y1 = (1 - line.top_left.y) * svgHeight;
                const x2 = line.top_right.x * svgWidth;
                const y2 = (1 - line.top_right.y) * svgHeight;
                const x3 = line.bottom_right.x * svgWidth;
                const y3 = (1 - line.bottom_right.y) * svgHeight;
                const x4 = line.bottom_left.x * svgWidth;
                const y4 = (1 - line.bottom_left.y) * svgHeight;
                const points = `${x1},${y1} ${x2},${y2} ${x3},${y3} ${x4},${y4}`;
                return (
                  <animated.polygon
                    key={`${line.line_id}`}
                    style={style}
                    points={points}
                    fill="none"
                    stroke="red"
                    strokeWidth="2"
                  />
                );
              })}

              {/* Render animated word centroids */}
              {lines.map((line, index) => (
                <AnimatedLineBox
                  key={`${line.line_id}`}
                  line={line}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={index * 30}
                />
              ))}

              {/* Render animated receipt bounding boxes and centroids */}
              {receipts.map((receipt, index) => (
                <AnimatedReceipt
                  key={`receipt-${receipt.receipt_id}`}
                  receipt={receipt}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={totalDelayForLines + index * 100}
                />
              ))}
            </svg>
          ) : (
            // While loading, show a "Loading" message centered in the reserved space.
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                width: "100%",
                height: "100%",
              }}
            >
              Loading...
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ImageBoundingBox;
