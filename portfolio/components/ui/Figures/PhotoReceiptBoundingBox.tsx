import React, { useEffect, useState } from "react";

import { api } from "../../../services/api";
import {
  ImageDetailsApiResponse,
  type Image as ImageType,
} from "../../../types/api";
import { useSpring, useTransition, animated } from "@react-spring/web";

// Define simple point and line-segment shapes
type Point = { x: number; y: number };
type LineSegment = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  key: string;
};

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
 * Find hull extents relative to centroid (matching Python implementation)
 */
const findHullExtentsRelativeToCentroid = (
  hull: { x: number; y: number }[],
  centroid: { x: number; y: number }
): {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  leftPoint: { x: number; y: number };
  rightPoint: { x: number; y: number };
  topPoint: { x: number; y: number };
  bottomPoint: { x: number; y: number };
} => {
  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;
  let leftPoint = hull[0],
    rightPoint = hull[0];
  let topPoint = hull[0],
    bottomPoint = hull[0];

  hull.forEach((point) => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;

    if (relX < minX) {
      minX = relX;
      leftPoint = point;
    }
    if (relX > maxX) {
      maxX = relX;
      rightPoint = point;
    }
    if (relY < minY) {
      minY = relY;
      bottomPoint = point;
    }
    if (relY > maxY) {
      maxY = relY;
      topPoint = point;
    }
  });

  return {
    minX,
    maxX,
    minY,
    maxY,
    leftPoint,
    rightPoint,
    topPoint,
    bottomPoint,
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
 * Find the left and right boundary lines using perpendicular projection
 */
const findLineEdgesAtPrimaryExtremes = (
  lines: any[],
  hull: { x: number; y: number }[],
  centroid: { x: number; y: number },
  avgAngle: number
): {
  leftEdge: { x: number; y: number }[];
  rightEdge: { x: number; y: number }[];
} => {
  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;

  const secondaryAxisAngle = primaryAxisAngle + Math.PI / 2; // Perpendicular to text direction

  // Step 1: Find boundary lines using perpendicular projection
  // Project each line's center onto the secondary axis (perpendicular to text direction)
  const lineProjections = lines.map((line) => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    const lineCenterY =
      (line.top_left.y +
        line.top_right.y +
        line.bottom_left.y +
        line.bottom_right.y) /
      4;

    const relX = lineCenterX - centroid.x;
    const relY = lineCenterY - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    return {
      line,
      projection: secondaryProjection,
      centerX: lineCenterX,
      centerY: lineCenterY,
    };
  });

  // Sort by projection to find extremes
  lineProjections.sort((a, b) => a.projection - b.projection);

  // Get boundary lines (use top 20% for each boundary to handle noise)
  const boundaryCount = Math.max(1, Math.ceil(lines.length * 0.2));
  const leftBoundaryLines = lineProjections
    .slice(0, boundaryCount)
    .map((p) => p.line);
  const rightBoundaryLines = lineProjections
    .slice(-boundaryCount)
    .map((p) => p.line);

  // Step 2: Get edge points from boundary lines
  let leftEdgePoints: { x: number; y: number }[] = [];
  let rightEdgePoints: { x: number; y: number }[] = [];

  // For left boundary lines, get their leftmost edges
  leftBoundaryLines.forEach((line) => {
    const leftX = Math.min(line.top_left.x, line.bottom_left.x);
    leftEdgePoints.push(
      { x: leftX, y: line.top_left.y },
      { x: leftX, y: line.bottom_left.y }
    );
  });

  // For right boundary lines, get their rightmost edges
  rightBoundaryLines.forEach((line) => {
    const rightX = Math.max(line.top_right.x, line.bottom_right.x);
    rightEdgePoints.push(
      { x: rightX, y: line.top_right.y },
      { x: rightX, y: line.bottom_right.y }
    );
  });

  return {
    leftEdge: leftEdgePoints,
    rightEdge: rightEdgePoints,
  };
};

/**
 * Enhanced version that returns both edge points and boundary angles
 */
const findBoundaryLinesWithSkew = (
  lines: any[],
  hull: { x: number; y: number }[],
  centroid: { x: number; y: number },
  avgAngle: number
): {
  leftEdgePoints: { x: number; y: number }[];
  rightEdgePoints: { x: number; y: number }[];
  leftBoundaryAngle: number;
  rightBoundaryAngle: number;
} => {
  if (lines.length === 0) {
    return {
      leftEdgePoints: [],
      rightEdgePoints: [],
      leftBoundaryAngle: avgAngle,
      rightBoundaryAngle: avgAngle,
    };
  }

  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;
  const secondaryAxisAngle = primaryAxisAngle + Math.PI / 2; // Perpendicular to text direction

  // Step 1: Find boundary lines using perpendicular projection
  const lineProjections = lines.map((line) => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    const lineCenterY =
      (line.top_left.y +
        line.top_right.y +
        line.bottom_left.y +
        line.bottom_right.y) /
      4;

    const relX = lineCenterX - centroid.x;
    const relY = lineCenterY - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    return {
      line,
      projection: secondaryProjection,
    };
  });

  // Sort by projection to find extremes
  lineProjections.sort((a, b) => a.projection - b.projection);

  // Get boundary lines (use top 20% for each boundary to handle noise)
  const boundaryCount = Math.max(1, Math.ceil(lines.length * 0.2));
  const leftBoundaryLines = lineProjections
    .slice(0, boundaryCount)
    .map((p) => p.line);
  const rightBoundaryLines = lineProjections
    .slice(-boundaryCount)
    .map((p) => p.line);

  // Step 2: Extract angles from boundary lines
  const leftBoundaryAngle =
    leftBoundaryLines.reduce((sum, line) => sum + line.angle_degrees, 0) /
    leftBoundaryLines.length;
  const rightBoundaryAngle =
    rightBoundaryLines.reduce((sum, line) => sum + line.angle_degrees, 0) /
    rightBoundaryLines.length;

  // Step 3: Get edge points from boundary lines
  let leftEdgePoints: { x: number; y: number }[] = [];
  let rightEdgePoints: { x: number; y: number }[] = [];

  // For left boundary lines, get their leftmost edges
  leftBoundaryLines.forEach((line) => {
    const leftX = Math.min(line.top_left.x, line.bottom_left.x);
    leftEdgePoints.push(
      { x: leftX, y: line.top_left.y },
      { x: leftX, y: line.bottom_left.y }
    );
  });

  // For right boundary lines, get their rightmost edges
  rightBoundaryLines.forEach((line) => {
    const rightX = Math.max(line.top_right.x, line.bottom_right.x);
    rightEdgePoints.push(
      { x: rightX, y: line.top_right.y },
      { x: rightX, y: line.bottom_right.y }
    );
  });

  return {
    leftEdgePoints,
    rightEdgePoints,
    leftBoundaryAngle,
    rightBoundaryAngle,
  };
};

/**
 * Find the top and bottom edges of lines at the secondary axis extremes
 */
const findLineEdgesAtSecondaryExtremes = (
  lines: any[],
  hull: { x: number; y: number }[],
  centroid: { x: number; y: number },
  avgAngle: number
): {
  topEdge: { x: number; y: number }[];
  bottomEdge: { x: number; y: number }[];
} => {
  const angleRad = (avgAngle * Math.PI) / 180;
  const secondaryAxisAngle = angleRad + Math.PI / 2;

  // Find secondary axis extremes (yellow circles)
  let minSecondary = Infinity,
    maxSecondary = -Infinity;
  let topExtremeX = 0,
    bottomExtremeX = 0;

  hull.forEach((point) => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    if (secondaryProjection < minSecondary) {
      minSecondary = secondaryProjection;
      bottomExtremeX = point.x;
    }
    if (secondaryProjection > maxSecondary) {
      maxSecondary = secondaryProjection;
      topExtremeX = point.x;
    }
  });

  // Find lines near the top and bottom extremes
  const tolerance = 0.1; // 10% tolerance for finding nearby lines
  const topLines = lines.filter((line) => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    return Math.abs(lineCenterX - topExtremeX) < tolerance;
  });

  const bottomLines = lines.filter((line) => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    return Math.abs(lineCenterX - bottomExtremeX) < tolerance;
  });

  // Find the actual top edge (highest Y values) and bottom edge (lowest Y values)
  let topEdgePoints: { x: number; y: number }[] = [];
  let bottomEdgePoints: { x: number; y: number }[] = [];

  if (topLines.length > 0) {
    // Get the topmost edges of lines near the top extreme
    topLines.forEach((line) => {
      const topY = Math.max(line.top_left.y, line.top_right.y);
      topEdgePoints.push(
        { x: line.top_left.x, y: topY },
        { x: line.top_right.x, y: topY }
      );
    });
  }

  if (bottomLines.length > 0) {
    // Get the bottommost edges of lines near the bottom extreme
    bottomLines.forEach((line) => {
      const bottomY = Math.min(line.bottom_left.y, line.bottom_right.y);
      bottomEdgePoints.push(
        { x: line.bottom_left.x, y: bottomY },
        { x: line.bottom_right.x, y: bottomY }
      );
    });
  }

  // Fallback to hull points if no lines found
  if (topEdgePoints.length === 0) {
    const topHullPoint = hull.find((point) => {
      const relX = point.x - centroid.x;
      const relY = point.y - centroid.y;
      const projection =
        relX * Math.cos(secondaryAxisAngle) +
        relY * Math.sin(secondaryAxisAngle);
      return Math.abs(projection - maxSecondary) < 0.001;
    });
    if (topHullPoint) topEdgePoints = [topHullPoint];
  }

  if (bottomEdgePoints.length === 0) {
    const bottomHullPoint = hull.find((point) => {
      const relX = point.x - centroid.x;
      const relY = point.y - centroid.y;
      const projection =
        relX * Math.cos(secondaryAxisAngle) +
        relY * Math.sin(secondaryAxisAngle);
      return Math.abs(projection - minSecondary) < 0.001;
    });
    if (bottomHullPoint) bottomEdgePoints = [bottomHullPoint];
  }

  return {
    topEdge: topEdgePoints,
    bottomEdge: bottomEdgePoints,
  };
};

/**
 * Compute receipt box using skewed boundaries from all four edge analyses
 */
const computeReceiptBoxFromLineEdges = (
  lines: any[],
  hull: { x: number; y: number }[],
  centroid: { x: number; y: number },
  avgAngle: number
): { x: number; y: number }[] => {
  if (lines.length === 0) return [];

  // Use the existing robust edge computation instead of our experimental approach
  const leftEdge = computeEdge(lines, "left");
  const rightEdge = computeEdge(lines, "right");

  if (!leftEdge || !rightEdge) {
    // Fallback to simple hull-based rectangle
    return [
      { x: centroid.x - 0.1, y: centroid.y + 0.1 }, // top-left
      { x: centroid.x + 0.1, y: centroid.y + 0.1 }, // top-right
      { x: centroid.x + 0.1, y: centroid.y - 0.1 }, // bottom-right
      { x: centroid.x - 0.1, y: centroid.y - 0.1 }, // bottom-left
    ];
  }

  // Find actual top and bottom boundaries using secondary axis extremes (yellow circles)
  const { topEdge, bottomEdge } = findLineEdgesAtSecondaryExtremes(
    lines,
    hull,
    centroid,
    avgAngle
  );

  // Fit lines through the top and bottom edge points (yellow circles)
  const topEdgeLine = topEdge.length >= 2 ? theilSen(topEdge) : null;
  const bottomEdgeLine = bottomEdge.length >= 2 ? theilSen(bottomEdge) : null;

  // Get left and right edge line parameters
  const leftSlope =
    (leftEdge.top.x - leftEdge.bottom.x) / (leftEdge.top.y - leftEdge.bottom.y);
  const leftIntercept = leftEdge.top.x - leftSlope * leftEdge.top.y;

  const rightSlope =
    (rightEdge.top.x - rightEdge.bottom.x) /
    (rightEdge.top.y - rightEdge.bottom.y);
  const rightIntercept = rightEdge.top.x - rightSlope * rightEdge.top.y;

  // Calculate intersections of skewed boundary lines
  let topLeft, topRight, bottomLeft, bottomRight;

  if (topEdgeLine) {
    // Top edge: x = topSlope * y + topIntercept
    // Left edge: x = leftSlope * y + leftIntercept
    // Intersection: topSlope * y + topIntercept = leftSlope * y + leftIntercept
    const topLeftY =
      (leftIntercept - topEdgeLine.intercept) / (topEdgeLine.slope - leftSlope);
    const topLeftX = leftSlope * topLeftY + leftIntercept;
    topLeft = { x: topLeftX, y: topLeftY };

    // Top edge intersect with right edge
    const topRightY =
      (rightIntercept - topEdgeLine.intercept) /
      (topEdgeLine.slope - rightSlope);
    const topRightX = rightSlope * topRightY + rightIntercept;
    topRight = { x: topRightX, y: topRightY };
  } else {
    // Fallback to horizontal line at average Y
    const avgTopY =
      topEdge.reduce((sum, p) => sum + p.y, 0) / Math.max(topEdge.length, 1);
    topLeft = { x: leftSlope * avgTopY + leftIntercept, y: avgTopY };
    topRight = { x: rightSlope * avgTopY + rightIntercept, y: avgTopY };
  }

  if (bottomEdgeLine) {
    // Bottom edge intersect with left edge
    const bottomLeftY =
      (leftIntercept - bottomEdgeLine.intercept) /
      (bottomEdgeLine.slope - leftSlope);
    const bottomLeftX = leftSlope * bottomLeftY + leftIntercept;
    bottomLeft = { x: bottomLeftX, y: bottomLeftY };

    // Bottom edge intersect with right edge
    const bottomRightY =
      (rightIntercept - bottomEdgeLine.intercept) /
      (bottomEdgeLine.slope - rightSlope);
    const bottomRightX = rightSlope * bottomRightY + rightIntercept;
    bottomRight = { x: bottomRightX, y: bottomRightY };
  } else {
    // Fallback to horizontal line at average Y
    const avgBottomY =
      bottomEdge.reduce((sum, p) => sum + p.y, 0) /
      Math.max(bottomEdge.length, 1);
    bottomLeft = { x: leftSlope * avgBottomY + leftIntercept, y: avgBottomY };
    bottomRight = {
      x: rightSlope * avgBottomY + rightIntercept,
      y: avgBottomY,
    };
  }

  return [
    topLeft, // top-left
    topRight, // top-right
    bottomRight, // bottom-right
    bottomLeft, // bottom-left
  ];
};

/**
 * Robust Theil–Sen estimator for a line x = m·y + b.
 */
const theilSen = (pts: { x: number; y: number }[]) => {
  if (pts.length < 2) return { slope: 0, intercept: pts[0] ? pts[0].y : 0 };

  // Collect all pair‑wise slopes.
  const slopes: number[] = [];
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      if (pts[i].y === pts[j].y) continue; // avoid div‑by‑zero
      slopes.push((pts[j].x - pts[i].x) / (pts[j].y - pts[i].y));
    }
  }
  if (slopes.length === 0) {
    // All points share the same y => true horizontal line
    return { slope: 0, intercept: pts[0].y };
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

// AnimatedOrientedAxes: component for visualizing oriented axes based on average line angle
interface AnimatedOrientedAxesProps {
  hull: { x: number; y: number }[];
  centroid: { x: number; y: number };
  lines: any[];
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedOrientedAxes: React.FC<AnimatedOrientedAxesProps> = ({
  hull,
  centroid,
  lines,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const [visibleElements, setVisibleElements] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      const interval = setInterval(() => {
        setVisibleElements((prev) => {
          if (prev >= 3) {
            // 0: primary axis, 1: secondary axis, 2: extent points
            clearInterval(interval);
            return prev;
          }
          return prev + 1;
        });
      }, 400); // Show each element every 400ms

      return () => clearInterval(interval);
    }, delay);

    return () => clearTimeout(timer);
  }, [delay]);

  // Reset when hull changes
  useEffect(() => {
    setVisibleElements(0);
  }, [hull, lines]);

  if (hull.length === 0 || lines.length === 0) return null;

  // Use the actual angle data from OCR API (more accurate than manual calculation)
  const avgAngle =
    lines.reduce((sum, line) => sum + line.angle_degrees, 0) / lines.length;

  const centroidX = centroid.x * svgWidth;
  const centroidY = (1 - centroid.y) * svgHeight;

  // Convert angle to radians for calculations
  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;
  const secondaryAxisAngle = angleRad + Math.PI / 2; // Perpendicular axis

  // Calculate axis endpoints (extend across the SVG canvas)
  const axisLength = Math.max(svgWidth, svgHeight);

  // Primary axis (along average line direction)
  const primaryAxis = {
    x1: centroidX - (axisLength / 2) * Math.cos(primaryAxisAngle),
    y1: centroidY - (axisLength / 2) * Math.sin(primaryAxisAngle),
    x2: centroidX + (axisLength / 2) * Math.cos(primaryAxisAngle),
    y2: centroidY + (axisLength / 2) * Math.sin(primaryAxisAngle),
  };

  // Secondary axis (perpendicular to average line direction)
  const secondaryAxis = {
    x1: centroidX - (axisLength / 2) * Math.cos(secondaryAxisAngle),
    y1: centroidY - (axisLength / 2) * Math.sin(secondaryAxisAngle),
    x2: centroidX + (axisLength / 2) * Math.cos(secondaryAxisAngle),
    y2: centroidY + (axisLength / 2) * Math.sin(secondaryAxisAngle),
  };

  // Find extent points along each axis
  let minPrimary = Infinity,
    maxPrimary = -Infinity;
  let minSecondary = Infinity,
    maxSecondary = -Infinity;
  let primaryMinPoint = hull[0],
    primaryMaxPoint = hull[0];
  let secondaryMinPoint = hull[0],
    secondaryMaxPoint = hull[0];

  // Calculate projections for all hull points
  const hullProjections = hull.map((point) => {
    const px = point.x * svgWidth;
    const py = (1 - point.y) * svgHeight;
    const relX = px - centroidX;
    const relY = py - centroidY;
    const primaryProjection =
      relX * Math.cos(primaryAxisAngle) + relY * Math.sin(primaryAxisAngle);
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    return {
      point: { x: px, y: py },
      primaryProjection,
      secondaryProjection,
      originalPoint: point,
    };
  });

  // Find the extreme projections
  hullProjections.forEach(
    ({ point, primaryProjection, secondaryProjection }) => {
      if (primaryProjection < minPrimary) {
        minPrimary = primaryProjection;
        primaryMinPoint = point;
      }
      if (primaryProjection > maxPrimary) {
        maxPrimary = primaryProjection;
        primaryMaxPoint = point;
      }
      if (secondaryProjection < minSecondary) {
        minSecondary = secondaryProjection;
        secondaryMinPoint = point;
      }
      if (secondaryProjection > maxSecondary) {
        maxSecondary = secondaryProjection;
        secondaryMaxPoint = point;
      }
    }
  );

  // Find the single closest hull point to each secondary extreme
  let topExtremeClosest = secondaryMaxPoint;
  let bottomExtremeClosest = secondaryMinPoint;
  let minTopDistance = Infinity;
  let minBottomDistance = Infinity;

  hullProjections.forEach(({ point, secondaryProjection }) => {
    // Skip if this is already the extreme point itself
    if (
      Math.abs(point.x - secondaryMaxPoint.x) < 1 &&
      Math.abs(point.y - secondaryMaxPoint.y) < 1
    )
      return;
    if (
      Math.abs(point.x - secondaryMinPoint.x) < 1 &&
      Math.abs(point.y - secondaryMinPoint.y) < 1
    )
      return;

    // Find closest to top extreme
    const topDistance = Math.abs(secondaryProjection - maxSecondary);
    if (topDistance < minTopDistance) {
      minTopDistance = topDistance;
      topExtremeClosest = point;
    }

    // Find closest to bottom extreme
    const bottomDistance = Math.abs(secondaryProjection - minSecondary);
    if (bottomDistance < minBottomDistance) {
      minBottomDistance = bottomDistance;
      bottomExtremeClosest = point;
    }
  });

  // Combine primary extremes (green) and secondary points (yellow)
  const primaryPoints = [primaryMinPoint, primaryMaxPoint];
  const secondaryPoints = [
    secondaryMinPoint, // original bottom extreme
    secondaryMaxPoint, // original top extreme
    bottomExtremeClosest, // closest to bottom extreme
    topExtremeClosest, // closest to top extreme
  ];

  // Remove duplicates from secondary points (in case closest point is same as extreme)
  const uniqueSecondaryPoints: Point[] = secondaryPoints.filter(
    (point, index, arr) =>
      arr.findIndex(
        (p) => Math.abs(p.x - point.x) < 1 && Math.abs(p.y - point.y) < 1
      ) === index
  );

  return (
    <>
      {/* Primary axis (along average line direction) */}
      {visibleElements >= 1 && (
        <line
          x1={primaryAxis.x1}
          y1={primaryAxis.y1}
          x2={primaryAxis.x2}
          y2={primaryAxis.y2}
          stroke="var(--color-green)"
          strokeWidth="3"
          strokeDasharray="8,4"
          opacity={0.8}
        />
      )}

      {/* Secondary axis (perpendicular to average line direction) */}
      {visibleElements >= 2 && (
        <line
          x1={secondaryAxis.x1}
          y1={secondaryAxis.y1}
          x2={secondaryAxis.x2}
          y2={secondaryAxis.y2}
          stroke="var(--color-yellow)"
          strokeWidth="3"
          strokeDasharray="8,4"
          opacity={0.8}
        />
      )}

      {/* Primary extent points (green) */}
      {visibleElements >= 3 &&
        primaryPoints.map((point, index) => (
          <circle
            key={`primary-${index}`}
            cx={point.x}
            cy={point.y}
            r={10}
            fill="var(--color-green)"
            stroke="white"
            strokeWidth="2"
            opacity={0.9}
          />
        ))}

      {/* Secondary edge points (yellow) - all points along top/bottom edges */}
      {visibleElements >= 3 &&
        uniqueSecondaryPoints.map((point, index) => (
          <circle
            key={`secondary-${index}`}
            cx={point.x}
            cy={point.y}
            r={10}
            fill="var(--color-yellow)"
            stroke="white"
            strokeWidth="2"
            opacity={0.9}
          />
        ))}
    </>
  );
};

// AnimatedPrimaryEdges: show line edges at primary extremes
interface AnimatedPrimaryEdgesProps {
  lines: any[];
  hull: { x: number; y: number }[];
  centroid: { x: number; y: number };
  avgAngle: number;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedPrimaryEdges: React.FC<AnimatedPrimaryEdgesProps> = ({
  lines,
  hull,
  centroid,
  avgAngle,
  svgWidth,
  svgHeight,
  delay,
}) => {
  const { leftEdgePoints, rightEdgePoints } = findBoundaryLinesWithSkew(
    lines,
    hull,
    centroid,
    avgAngle
  );

  const allEdgePoints = [...leftEdgePoints, ...rightEdgePoints];

  const edgeTransitions = useTransition(allEdgePoints, {
    keys: (point) => `edge-${point.x}-${point.y}`,
    from: { opacity: 0, scale: 0 },
    enter: (item, index) => ({
      opacity: 1,
      scale: 1,
      delay: delay + index * 100,
    }),
    config: { duration: 400 },
  });

  return (
    <g>
      {edgeTransitions((style, edgePoint) => {
        const isLeftEdge = leftEdgePoints.some(
          (p) => p.x === edgePoint.x && p.y === edgePoint.y
        );
        return (
          <animated.circle
            style={style}
            cx={edgePoint.x * svgWidth}
            cy={(1 - edgePoint.y) * svgHeight}
            r="6"
            fill={isLeftEdge ? "var(--color-blue)" : "var(--color-red)"}
            stroke="white"
            strokeWidth="1"
          />
        );
      })}
    </g>
  );
};

// AnimatedSecondaryBoundaryLines: draw lines through yellow circles (secondary extremes)
interface AnimatedSecondaryBoundaryLinesProps {
  lines: any[];
  hull: { x: number; y: number }[];
  centroid: { x: number; y: number };
  avgAngle: number;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedSecondaryBoundaryLines: React.FC<
  AnimatedSecondaryBoundaryLinesProps
> = ({ lines, hull, centroid, avgAngle, svgWidth, svgHeight, delay }) => {
  if (hull.length < 3) return null;

  // Find the yellow points on the hull (secondary axis extremes)
  const angleRad = (avgAngle * Math.PI) / 180;
  const secondaryAxisAngle = angleRad + Math.PI / 2;

  let minSecondary = Infinity,
    maxSecondary = -Infinity;
  let bottomHullPoint = hull[0],
    topHullPoint = hull[0];

  hull.forEach((point) => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    if (secondaryProjection < minSecondary) {
      minSecondary = secondaryProjection;
      bottomHullPoint = point;
    }
    if (secondaryProjection > maxSecondary) {
      maxSecondary = secondaryProjection;
      topHullPoint = point;
    }
  });

  // Find the closest neighbors to create line segments
  const findClosestNeighbor = (
    targetPoint: Point,
    targetProjection: number
  ): Point | null => {
    let closestPoint: Point | null = null;
    let minDistance = Infinity;

    hull.forEach((point) => {
      if (point === targetPoint) return;

      const relX = point.x - centroid.x;
      const relY = point.y - centroid.y;
      const projection =
        relX * Math.cos(secondaryAxisAngle) +
        relY * Math.sin(secondaryAxisAngle);
      const distance = Math.abs(projection - targetProjection);

      if (distance < minDistance) {
        minDistance = distance;
        closestPoint = point;
      }
    });

    return closestPoint;
  };

  const topNeighbor = findClosestNeighbor(topHullPoint, maxSecondary);
  const bottomNeighbor = findClosestNeighbor(bottomHullPoint, minSecondary);

  // Create line segments through yellow hull points
  const lineSegments: LineSegment[] = [];

  // Always create top boundary line (with fallback if no neighbor found)
  if (topNeighbor) {
    // Top boundary: if perfectly horizontal, draw full-width line
    const yTop = topHullPoint.y;
    const yNeighbor = topNeighbor.y;
    const topPoints = [topHullPoint, topNeighbor];
    const topLine = theilSen(topPoints);
    if (Math.abs(yTop - yNeighbor) < 1e-6) {
      // perfect horizontal: extend across canvas
      const yPixel = (1 - yTop) * svgHeight;
      lineSegments.push({
        x1: 0,
        y1: yPixel,
        x2: svgWidth,
        y2: yPixel,
        key: "top-hull-boundary",
      });
    } else {
      // angled case: draw between extended hull points
      const xStart = topLine.slope * yTop + topLine.intercept;
      const xEnd = topLine.slope * yNeighbor + topLine.intercept;
      lineSegments.push({
        x1: xStart * svgWidth,
        y1: (1 - yTop) * svgHeight,
        x2: xEnd * svgWidth,
        y2: (1 - yNeighbor) * svgHeight,
        key: "top-hull-boundary",
      });
    }
  } else {
    // Fallback: use average text angle for horizontal line through top point
    // Horizontal case as y = m·x + b
    const slope = Math.tan(angleRad);
    const intercept = topHullPoint.y - slope * topHullPoint.x;
    const x1 = 0;
    const y1 = slope * x1 + intercept;
    const x2 = 1;
    const y2 = slope * x2 + intercept;
    lineSegments.push({
      x1: x1 * svgWidth,
      y1: (1 - y1) * svgHeight,
      x2: x2 * svgWidth,
      y2: (1 - y2) * svgHeight,
      key: "top-hull-boundary",
    });
  }

  // Always create bottom boundary line (with fallback if no neighbor found)
  if (bottomNeighbor) {
    // Bottom boundary: if perfectly horizontal, draw full-width line
    const yBottomTop = bottomHullPoint.y;
    const yBottomNeighbor = bottomNeighbor.y;
    const bottomPoints = [bottomHullPoint, bottomNeighbor];
    const bottomLine = theilSen(bottomPoints);
    if (Math.abs(yBottomTop - yBottomNeighbor) < 1e-6) {
      const yPixel = (1 - yBottomTop) * svgHeight;
      lineSegments.push({
        x1: 0,
        y1: yPixel,
        x2: svgWidth,
        y2: yPixel,
        key: "bottom-hull-boundary",
      });
    } else {
      const xStart = bottomLine.slope * yBottomTop + bottomLine.intercept;
      const xEnd = bottomLine.slope * yBottomNeighbor + bottomLine.intercept;
      lineSegments.push({
        x1: xStart * svgWidth,
        y1: (1 - yBottomTop) * svgHeight,
        x2: xEnd * svgWidth,
        y2: (1 - yBottomNeighbor) * svgHeight,
        key: "bottom-hull-boundary",
      });
    }
  } else {
    // Fallback: use average text angle for horizontal line through bottom point
    // Horizontal case as y = m·x + b
    const slope = Math.tan(angleRad);
    const intercept = bottomHullPoint.y - slope * bottomHullPoint.x;
    const x1 = 0;
    const y1 = slope * x1 + intercept;
    const x2 = 1;
    const y2 = slope * x2 + intercept;
    lineSegments.push({
      x1: x1 * svgWidth,
      y1: (1 - y1) * svgHeight,
      x2: x2 * svgWidth,
      y2: (1 - y2) * svgHeight,
      key: "bottom-hull-boundary",
    });
  }

  const lineTransitions = useTransition(lineSegments, {
    keys: (line) => line.key,
    from: { opacity: 0, strokeDasharray: "10,10", strokeDashoffset: 20 },
    enter: (item, index) => ({
      opacity: 1,
      strokeDashoffset: 0,
      delay: delay + index * 200,
    }),
    config: { duration: 800 },
  });

  return (
    <g>
      {lineTransitions((style, line) => (
        <animated.line
          key={line.key}
          style={style}
          x1={line.x1}
          y1={line.y1}
          x2={line.x2}
          y2={line.y2}
          stroke="var(--color-yellow)"
          strokeWidth="5"
          strokeDasharray="10,10"
        />
      ))}
    </g>
  );
};

// AnimatedPrimaryBoundaryLines: draw left/right boundaries using perpendicular projection
interface AnimatedPrimaryBoundaryLinesProps {
  lines: any[];
  hull: { x: number; y: number }[];
  centroid: { x: number; y: number };
  avgAngle: number;
  svgWidth: number;
  svgHeight: number;
  delay: number;
}

const AnimatedPrimaryBoundaryLines: React.FC<
  AnimatedPrimaryBoundaryLinesProps
> = ({ lines, hull, centroid, avgAngle, svgWidth, svgHeight, delay }) => {
  if (lines.length < 2) return null;

  // Step 1: Find the green hull points (primary axis extremes - leftmost/rightmost)
  const angleRad = (avgAngle * Math.PI) / 180;
  const primaryAxisAngle = angleRad;

  let minPrimary = Infinity,
    maxPrimary = -Infinity;
  let leftHullPoint = hull[0],
    rightHullPoint = hull[0];

  hull.forEach((point) => {
    const relX = point.x - centroid.x;
    const relY = point.y - centroid.y;
    const primaryProjection =
      relX * Math.cos(primaryAxisAngle) + relY * Math.sin(primaryAxisAngle);

    if (primaryProjection < minPrimary) {
      minPrimary = primaryProjection;
      leftHullPoint = point;
    }
    if (primaryProjection > maxPrimary) {
      maxPrimary = primaryProjection;
      rightHullPoint = point;
    }
  });

  // Step 2: Find the leftmost and rightmost TEXT LINES for their angles
  const secondaryAxisAngle = angleRad + Math.PI / 2;
  const lineProjections = lines.map((line) => {
    const lineCenterX =
      (line.top_left.x +
        line.top_right.x +
        line.bottom_left.x +
        line.bottom_right.x) /
      4;
    const lineCenterY =
      (line.top_left.y +
        line.top_right.y +
        line.bottom_left.y +
        line.bottom_right.y) /
      4;

    const relX = lineCenterX - centroid.x;
    const relY = lineCenterY - centroid.y;
    const secondaryProjection =
      relX * Math.cos(secondaryAxisAngle) + relY * Math.sin(secondaryAxisAngle);

    return {
      line,
      projection: secondaryProjection,
    };
  });

  lineProjections.sort((a, b) => a.projection - b.projection);
  const leftmostLine = lineProjections[0].line;
  const rightmostLine = lineProjections[lineProjections.length - 1].line;

  // Step 3: Create boundary lines that pass through the GREEN hull points using boundary line angles
  const lineSegments = [];

  // Left boundary: pass through leftHullPoint with leftmost line's angle
  const leftAngleRad = (leftmostLine.angle_degrees * Math.PI) / 180;
  const leftSlope = Math.tan(leftAngleRad);
  const leftIntercept = leftHullPoint.x - leftSlope * leftHullPoint.y;

  const leftY1 = 0;
  const leftX1 = leftSlope * leftY1 + leftIntercept;
  const leftY2 = 1;
  const leftX2 = leftSlope * leftY2 + leftIntercept;

  lineSegments.push({
    x1: leftX1 * svgWidth,
    y1: (1 - leftY1) * svgHeight,
    x2: leftX2 * svgWidth,
    y2: (1 - leftY2) * svgHeight,
    key: "left-boundary",
  });

  // Right boundary: pass through rightHullPoint with rightmost line's angle
  const rightAngleRad = (rightmostLine.angle_degrees * Math.PI) / 180;
  const rightSlope = Math.tan(rightAngleRad);
  const rightIntercept = rightHullPoint.x - rightSlope * rightHullPoint.y;

  const rightY1 = 0;
  const rightX1 = rightSlope * rightY1 + rightIntercept;
  const rightY2 = 1;
  const rightX2 = rightSlope * rightY2 + rightIntercept;

  lineSegments.push({
    x1: rightX1 * svgWidth,
    y1: (1 - rightY1) * svgHeight,
    x2: rightX2 * svgWidth,
    y2: (1 - rightY2) * svgHeight,
    key: "right-boundary",
  });

  const lineTransitions = useTransition(lineSegments, {
    keys: (line) => line.key,
    from: { opacity: 0, strokeDasharray: "12,8", strokeDashoffset: 20 },
    enter: (item, index) => ({
      opacity: 1,
      strokeDashoffset: 0,
      delay: delay + index * 300,
    }),
    config: { duration: 800 },
  });

  return (
    <g>
      {lineTransitions((style, line) => (
        <animated.line
          key={line.key}
          style={style}
          x1={line.x1}
          y1={line.y1}
          x2={line.x2}
          y2={line.y2}
          stroke="var(--color-green)"
          strokeWidth="5"
          strokeDasharray="12,8"
        />
      ))}
    </g>
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

  // Use the actual angle data from OCR API (more accurate than manual calculation)
  const avgAngle =
    lines.reduce((sum, line) => sum + line.angle_degrees, 0) / lines.length;

  // Compute receipt box using line edges at secondary extremes
  const receiptCorners = computeReceiptBoxFromLineEdges(
    lines,
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
  const extentsDelay = centroidDelay + 600; // Extents after centroid
  const extentsDuration = 4 * 300 + 500; // 4 extent lines * 300ms + buffer
  const receiptDelay = extentsDelay + extentsDuration + 300; // Receipt after extents

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

              {/* Render animated oriented axes */}
              {convexHull.length > 0 && hullCentroid && (
                <AnimatedOrientedAxes
                  key={`oriented-axes-${resetKey}`}
                  hull={convexHull}
                  centroid={hullCentroid}
                  lines={lines}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay}
                />
              )}

              {/* Render line edges at primary extremes */}
              {convexHull.length > 0 && hullCentroid && lines.length > 0 && (
                <AnimatedPrimaryEdges
                  key={`primary-edges-${resetKey}`}
                  lines={lines}
                  hull={convexHull}
                  centroid={hullCentroid}
                  avgAngle={
                    lines.reduce((sum, line) => sum + line.angle_degrees, 0) /
                    lines.length
                  }
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay + 1000}
                />
              )}

              {/* Render extended yellow boundary lines */}
              {convexHull.length > 0 && hullCentroid && lines.length > 0 && (
                <AnimatedSecondaryBoundaryLines
                  key={`secondary-boundary-lines-${resetKey}`}
                  lines={lines}
                  hull={convexHull}
                  centroid={hullCentroid}
                  avgAngle={
                    lines.reduce((sum, line) => sum + line.angle_degrees, 0) /
                    lines.length
                  }
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay + 1500}
                />
              )}

              {/* Render green left/right boundary lines using perpendicular projection */}
              {convexHull.length > 0 && hullCentroid && lines.length > 0 && (
                <AnimatedPrimaryBoundaryLines
                  key={`primary-boundary-lines-${resetKey}`}
                  lines={lines}
                  hull={convexHull}
                  centroid={hullCentroid}
                  avgAngle={
                    lines.reduce((sum, line) => sum + line.angle_degrees, 0) /
                    lines.length
                  }
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={extentsDelay + 2000}
                />
              )}

              {/* Render animated receipt using proper algorithm */}
              {/* {convexHull.length > 0 && lines.length > 0 && (
                <AnimatedReceiptFromHull
                  key={`receipt-from-hull-${resetKey}`}
                  hull={convexHull}
                  lines={lines}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  delay={receiptDelay}
                />
              )} */}
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
