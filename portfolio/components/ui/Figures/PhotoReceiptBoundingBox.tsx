import React, { useEffect, useState } from "react";

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

/**
 * Compute the convex hull of a set of points using Graham scan algorithm
 */
const computeConvexHull = (
  points: { x: number; y: number }[]
): { x: number; y: number }[] => {
  if (points.length < 3) return points;

  // Find the bottom-most point (or left most point in case of tie)
  let start = 0;
  for (let i = 1; i < points.length; i++) {
    if (
      points[i].y < points[start].y ||
      (points[i].y === points[start].y && points[i].x < points[start].x)
    ) {
      start = i;
    }
  }

  // Swap start point to beginning
  [points[0], points[start]] = [points[start], points[0]];
  const startPoint = points[0];

  // Sort points by polar angle with respect to start point
  const sortedPoints = points.slice(1).sort((a, b) => {
    const angleA = Math.atan2(a.y - startPoint.y, a.x - startPoint.x);
    const angleB = Math.atan2(b.y - startPoint.y, b.x - startPoint.x);
    if (angleA === angleB) {
      // If angles are equal, sort by distance
      const distA =
        Math.pow(a.x - startPoint.x, 2) + Math.pow(a.y - startPoint.y, 2);
      const distB =
        Math.pow(b.x - startPoint.x, 2) + Math.pow(b.y - startPoint.y, 2);
      return distA - distB;
    }
    return angleA - angleB;
  });

  // Graham scan
  const hull = [startPoint, sortedPoints[0]];

  for (let i = 1; i < sortedPoints.length; i++) {
    // Remove points that make a right turn
    while (hull.length > 1) {
      const p1 = hull[hull.length - 2];
      const p2 = hull[hull.length - 1];
      const p3 = sortedPoints[i];

      // Cross product to determine turn direction
      const cross =
        (p2.x - p1.x) * (p3.y - p1.y) - (p2.y - p1.y) * (p3.x - p1.x);
      if (cross > 0) break; // Left turn, keep the point
      hull.pop(); // Right turn, remove the point
    }
    hull.push(sortedPoints[i]);
  }

  return hull;
};

/**
 * Compute the centroid of a convex hull (matching Python implementation)
 */
const computeHullCentroid = (
  hull: { x: number; y: number }[]
): { x: number; y: number } => {
  if (hull.length === 0) return { x: 0, y: 0 };

  const sumX = hull.reduce((sum, point) => sum + point.x, 0);
  const sumY = hull.reduce((sum, point) => sum + point.y, 0);

  return {
    x: sumX / hull.length,
    y: sumY / hull.length,
  };
};

/**
 * Compute receipt box from hull using the same algorithm as Python
 */
const computeReceiptBoxFromHull = (
  hull: { x: number; y: number }[],
  centroid: { x: number; y: number },
  avgAngle: number
): { x: number; y: number }[] => {
  if (hull.length < 3) return [];

  // Find extents of hull relative to centroid
  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;

  // Rotate points by negative average angle to align with receipt orientation
  const angleRad = (-avgAngle * Math.PI) / 180;
  const cosA = Math.cos(angleRad);
  const sinA = Math.sin(angleRad);

  hull.forEach((point) => {
    // Translate to centroid origin
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;

    // Rotate
    const rotX = relX * cosA - relY * sinA;
    const rotY = relX * sinA + relY * cosA;

    minX = Math.min(minX, rotX);
    maxX = Math.max(maxX, rotX);
    minY = Math.min(minY, rotY);
    maxY = Math.max(maxY, rotY);
  });

  // Create bounding box corners in rotated space
  const corners = [
    { x: minX, y: maxY }, // top-left
    { x: maxX, y: maxY }, // top-right
    { x: maxX, y: minY }, // bottom-right
    { x: minX, y: minY }, // bottom-left
  ];

  // Rotate back and translate to world coordinates
  const reverseAngleRad = -angleRad;
  const cosRA = Math.cos(reverseAngleRad);
  const sinRA = Math.sin(reverseAngleRad);

  return corners.map((corner) => ({
    x: corner.x * cosRA - corner.y * sinRA + centroid.x,
    y: corner.x * sinRA + corner.y * cosRA + centroid.y,
  }));
};

/**
 * Robust Theil–Sen estimator for a line x = m·y + b.
 */
const theilSen = (pts: { x: number; y: number }[]) => {
  if (pts.length < 2) return { slope: 0, intercept: pts[0] ? pts[0].x : 0 };

  // Collect all pair‑wise slopes.
  const slopes: number[] = [];
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      if (pts[i].y === pts[j].y) continue; // avoid div‑by‑zero
      slopes.push((pts[j].x - pts[i].x) / (pts[j].y - pts[i].y));
    }
  }
  slopes.sort((a, b) => a - b);
  const slope = slopes[Math.floor(slopes.length / 2)];

  // Median of the intercepts.
  const intercepts = pts.map((p) => p.x - slope * p.y).sort((a, b) => a - b);
  const intercept = intercepts[Math.floor(intercepts.length / 2)];

  return { slope, intercept };
};

/**
 * Compute either the left or right page edge by y‑binning extreme x values
 * and fitting a robust line through them.
 */
const computeEdge = (
  lines: any[],
  pick: "left" | "right",
  bins = 6
): {
  top: { x: number; y: number };
  bottom: { x: number; y: number };
} | null => {
  // One bucket per y‑range.
  const binPts: ({ x: number; y: number } | null)[] = Array.from(
    { length: bins },
    () => null
  );

  lines.forEach((l) => {
    // Normalised y in [0,1] (0 = bottom, 1 = top).
    const yMid = (l.top_left.y + l.bottom_left.y) / 2;
    const x =
      pick === "left"
        ? Math.min(l.top_left.x, l.bottom_left.x)
        : Math.max(l.top_right.x, l.bottom_right.x);

    const idx = Math.min(bins - 1, Math.floor(yMid * bins));
    const current = binPts[idx];

    if (!current) {
      binPts[idx] = { x, y: yMid };
    } else if (pick === "left" ? x < current.x : x > current.x) {
      binPts[idx] = { x, y: yMid };
    }
  });

  const selected = binPts.filter(Boolean) as { x: number; y: number }[];
  if (selected.length < 2) return null;

  const { slope, intercept } = theilSen(selected);
  return {
    top: { x: slope * 1 + intercept, y: 1 },
    bottom: { x: slope * 0 + intercept, y: 0 },
  };
};

/**
 * Estimate the full receipt quadrilateral from OCR word‑level lines.
 */
const estimateReceiptPolygonFromLines = (lines: any[]) => {
  const left = computeEdge(lines, "left");
  const right = computeEdge(lines, "right");
  if (!left || !right) return null;

  return {
    receipt_id: "computed",
    top_left: left.top,
    top_right: right.top,
    bottom_right: right.bottom,
    bottom_left: left.bottom,
  };
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
        stroke="var(--color-red)"
        strokeWidth="2"
      />
      <animated.circle
        cx={centroidX}
        cy={centroidSpring.cy}
        r={10}
        fill="var(--color-red)"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

// AnimatedConvexHull: component for animating convex hull calculation
interface AnimatedConvexHullProps {
  hullPoints: { x: number; y: number }[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedConvexHull: React.FC<AnimatedConvexHullProps> = ({
  hullPoints,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const [visiblePoints, setVisiblePoints] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      const interval = setInterval(() => {
        setVisiblePoints((prev) => {
          if (prev >= hullPoints.length) {
            clearInterval(interval);
            return prev;
          }
          return prev + 1;
        });
      }, 200); // Add a new point every 200ms

      return () => clearInterval(interval);
    }, delay);

    return () => clearTimeout(timer);
  }, [delay, hullPoints.length]);

  // Reset when hullPoints change (for animation reset)
  useEffect(() => {
    setVisiblePoints(0);
  }, [hullPoints]);

  if (hullPoints.length === 0) return null;

  // Convert normalized coordinates to SVG coordinates
  const svgPoints = hullPoints.map((point) => ({
    x: point.x * svgWidth,
    y: (1 - point.y) * svgHeight,
  }));

  // Create path for the visible portion of the hull
  const visibleSvgPoints = svgPoints.slice(0, visiblePoints);

  if (visibleSvgPoints.length < 2) {
    return (
      <>
        {visibleSvgPoints.map((point, index) => (
          <circle
            key={index}
            cx={point.x}
            cy={point.y}
            r={12}
            fill="var(--color-red)"
            opacity={1}
            strokeWidth="2"
          />
        ))}
      </>
    );
  }

  const pathData = visibleSvgPoints.reduce((acc, point, index) => {
    if (index === 0) return `M ${point.x} ${point.y}`;
    return `${acc} L ${point.x} ${point.y}`;
  }, "");

  // Close the path if we've shown all points
  const finalPath =
    visiblePoints >= hullPoints.length ? `${pathData} Z` : pathData;

  return (
    <>
      {/* Hull vertices */}
      {visibleSvgPoints.map((point, index) => (
        <circle
          key={index}
          cx={point.x}
          cy={point.y}
          r={12}
          fill="var(--color-red)"
          opacity={1}
          strokeWidth="2"
        />
      ))}
      {/* Hull edges */}
      <path
        d={finalPath}
        fill="none"
        stroke="var(--color-red)"
        strokeWidth="4"
        opacity={1}
      />
      {/* Fill when complete */}
      {visiblePoints >= hullPoints.length && (
        <path d={finalPath} fill="var(--color-red)" opacity={0.1} />
      )}
    </>
  );
};

// AnimatedHullCentroid: component for visualizing hull centroid calculation
interface AnimatedHullCentroidProps {
  centroid: { x: number; y: number };
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedHullCentroid: React.FC<AnimatedHullCentroidProps> = ({
  centroid,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const centroidSpring = useSpring({
    from: { opacity: 0, scale: 0 },
    to: { opacity: 1, scale: 1 },
    delay: delay,
    config: { duration: 600 },
  });

  const centroidX = centroid.x * svgWidth;
  const centroidY = (1 - centroid.y) * svgHeight;

  return (
    <animated.circle
      cx={centroidX}
      cy={centroidY}
      r={15}
      fill="var(--color-red)"
      strokeWidth="3"
      style={{
        opacity: centroidSpring.opacity,
        transform: centroidSpring.scale.to((s) => `scale(${s})`),
        transformOrigin: `${centroidX}px ${centroidY}px`,
      }}
    />
  );
};

// AnimatedReceiptFromHull: component using the proper Python algorithm
interface AnimatedReceiptFromHullProps {
  hull: { x: number; y: number }[];
  lines: any[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedReceiptFromHull: React.FC<AnimatedReceiptFromHullProps> = ({
  hull,
  lines,
  svgWidth,
  svgHeight,
  delay,
}) => {
  if (hull.length === 0 || lines.length === 0) return null;

  // Compute hull centroid
  const hullCentroid = computeHullCentroid(hull);

  // Compute average angle from lines (matching Python implementation)
  const avgAngle =
    lines.reduce((sum, line) => {
      // Calculate angle from line geometry - approximating from bounding box
      const dx = line.top_right.x - line.top_left.x;
      const dy = line.top_right.y - line.top_left.y;
      const angle = Math.atan2(dy, dx) * (180 / Math.PI);
      return sum + angle;
    }, 0) / lines.length;

  // Compute receipt box using the same algorithm as Python
  const receiptCorners = computeReceiptBoxFromHull(
    hull,
    hullCentroid,
    avgAngle
  );

  if (receiptCorners.length !== 4) return null;

  // Convert to SVG coordinates
  const svgCorners = receiptCorners.map((corner) => ({
    x: corner.x * svgWidth,
    y: (1 - corner.y) * svgHeight,
  }));

  const points = svgCorners.map((c) => `${c.x},${c.y}`).join(" ");

  // Animate the receipt bounding box
  const boxSpring = useSpring({
    from: { opacity: 0 },
    to: { opacity: 1 },
    delay: delay,
    config: { duration: 800 },
  });

  // Compute receipt centroid for visualization
  const receiptCentroidX = svgCorners.reduce((sum, c) => sum + c.x, 0) / 4;
  const receiptCentroidY = svgCorners.reduce((sum, c) => sum + c.y, 0) / 4;

  const centroidSpring = useSpring({
    from: { opacity: 0 },
    to: { opacity: 1 },
    delay: delay + 400,
    config: { duration: 600 },
  });

  return (
    <>
      <animated.polygon
        style={boxSpring}
        points={points}
        fill="none"
        stroke="var(--color-blue)"
        strokeWidth="4"
      />
      <animated.circle
        cx={receiptCentroidX}
        cy={receiptCentroidY}
        r={12}
        fill="var(--color-blue)"
        strokeWidth="2"
        style={{ opacity: centroidSpring.opacity }}
      />
    </>
  );
};

// Main PhotoReceiptBoundingBox component
const PhotoReceiptBoundingBox: React.FC = () => {
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
          api.fetchRandomImageDetails("PHOTO"),
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
  const computedReceipt = estimateReceiptPolygonFromLines(lines);
  const receipts = computedReceipt
    ? [computedReceipt]
    : imageDetails?.receipts ?? [];

  // Compute convex hull from all line corners (matching the backend process_photo function)
  const allLineCorners: { x: number; y: number }[] = [];
  lines.forEach((line) => {
    // Add all four corners of each line bounding box
    allLineCorners.push(
      { x: line.top_left.x, y: line.top_left.y },
      { x: line.top_right.x, y: line.top_right.y },
      { x: line.bottom_right.x, y: line.bottom_right.y },
      { x: line.bottom_left.x, y: line.bottom_left.y }
    );
  });
  const convexHull =
    allLineCorners.length > 2 ? computeConvexHull([...allLineCorners]) : [];

  // Compute hull centroid for animation
  const hullCentroid =
    convexHull.length > 0 ? computeHullCentroid(convexHull) : null;

  // Animate line bounding boxes using a transition.
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

  // Compute animation timing
  const totalDelayForLines =
    lines.length > 0 ? (lines.length - 1) * 30 + 800 : 0;
  const convexHullDelay = totalDelayForLines + 300; // Start convex hull after lines
  const convexHullDuration = convexHull.length * 200 + 500;
  const centroidDelay = convexHullDelay + convexHullDuration + 200; // Hull centroid after convex hull
  const receiptDelay = centroidDelay + 800; // Receipt after centroid

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
                    stroke="var(--color-red)"
                    strokeWidth="2"
                  />
                );
              })}

              {/* Render animated convex hull */}
              {convexHull.length > 0 && (
                <AnimatedConvexHull
                  key={`convex-hull-${resetKey}`}
                  hullPoints={convexHull}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={convexHullDelay}
                />
              )}

              {/* Render animated hull centroid */}
              {hullCentroid && (
                <AnimatedHullCentroid
                  key={`hull-centroid-${resetKey}`}
                  centroid={hullCentroid}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={centroidDelay}
                />
              )}

              {/* Render animated receipt using proper algorithm */}
              {convexHull.length > 0 && lines.length > 0 && (
                <AnimatedReceiptFromHull
                  key={`receipt-from-hull-${resetKey}`}
                  hull={convexHull}
                  lines={lines}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={receiptDelay}
                />
              )}
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

export default PhotoReceiptBoundingBox;
