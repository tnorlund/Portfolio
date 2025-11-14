import { useMemo } from "react";
import type { Line, Point } from "../types/api";
import { dbscan, normalizePoints, denormalizePoints, type DBSCANResult } from "../utils/geometry";

export interface ReceiptCluster {
  lines: Line[];
  boundingBox: {
    topLeft: Point;
    topRight: Point;
    bottomLeft: Point;
    bottomRight: Point;
  };
  centroid: Point;
}

export interface UseReceiptClusteringOptions {
  epsilon?: number;
  minPoints?: number;
  useNormalization?: boolean;
  imageWidth?: number;
  imageHeight?: number;
}

export interface UseReceiptClusteringResult {
  clusters: ReceiptCluster[];
  noiseLines: Line[];
  dbscanResult: DBSCANResult;
}

const getLineCentroid = (line: Line): Point => {
  return {
    x: (line.top_left.x + line.top_right.x + line.bottom_left.x + line.bottom_right.x) / 4,
    y: (line.top_left.y + line.top_right.y + line.bottom_left.y + line.bottom_right.y) / 4,
  };
};

const getLineCentroidPixels = (line: Line, imageWidth: number, imageHeight: number): Point => {
  const centroid = getLineCentroid(line);
  return {
    x: centroid.x * imageWidth,
    y: centroid.y * imageHeight,
  };
};

const getLineDiagonalLength = (line: Line): number => {
  const dx = line.top_right.x - line.bottom_left.x;
  const dy = line.top_right.y - line.bottom_left.y;
  return Math.sqrt(dx * dx + dy * dy);
};

const getLineDiagonalLengthPixels = (line: Line, imageWidth: number, imageHeight: number): number => {
  const dx = (line.top_right.x - line.bottom_left.x) * imageWidth;
  const dy = (line.top_right.y - line.bottom_left.y) * imageHeight;
  return Math.sqrt(dx * dx + dy * dy);
};

export const calculateEpsilonFromLines = (lines: Line[]): number => {
  if (lines.length === 0) return 0.05; // default
  
  const diagonalLengths = lines.map(getLineDiagonalLength);
  const avgDiagonal = diagonalLengths.reduce((sum, len) => sum + len, 0) / diagonalLengths.length;
  
  return avgDiagonal * 2;
};

export const calculateEpsilonFromLinesPixels = (lines: Line[], imageWidth: number, imageHeight: number): number => {
  if (lines.length === 0) return 50; // default in pixels
  
  const diagonalLengths = lines.map(line => getLineDiagonalLengthPixels(line, imageWidth, imageHeight));
  const avgDiagonal = diagonalLengths.reduce((sum, len) => sum + len, 0) / diagonalLengths.length;
  
  return avgDiagonal * 2;
};

const computeClusterBoundingBox = (lines: Line[]) => {
  if (lines.length === 0) {
    return {
      topLeft: { x: 0, y: 0 },
      topRight: { x: 0, y: 0 },
      bottomLeft: { x: 0, y: 0 },
      bottomRight: { x: 0, y: 0 },
    };
  }

  const allPoints: Point[] = [];
  lines.forEach(line => {
    allPoints.push(line.top_left, line.top_right, line.bottom_left, line.bottom_right);
  });

  const minX = Math.min(...allPoints.map(p => p.x));
  const maxX = Math.max(...allPoints.map(p => p.x));
  const minY = Math.min(...allPoints.map(p => p.y));
  const maxY = Math.max(...allPoints.map(p => p.y));

  return {
    topLeft: { x: minX, y: maxY },
    topRight: { x: maxX, y: maxY },
    bottomLeft: { x: minX, y: minY },
    bottomRight: { x: maxX, y: minY },
  };
};

const computeClusterCentroid = (lines: Line[]): Point => {
  if (lines.length === 0) return { x: 0, y: 0 };

  const centroids = lines.map(getLineCentroid);
  const sumX = centroids.reduce((sum, c) => sum + c.x, 0);
  const sumY = centroids.reduce((sum, c) => sum + c.y, 0);

  return {
    x: sumX / centroids.length,
    y: sumY / centroids.length,
  };
};

export const useReceiptClustering = (
  lines: Line[],
  options: UseReceiptClusteringOptions = {}
): UseReceiptClusteringResult => {
  const {
    imageWidth,
    imageHeight,
    minPoints = 3,
    useNormalization = true,
  } = options;
  
  // Use pixel-based clustering if image dimensions are provided
  const usePixelClustering = imageWidth !== undefined && imageHeight !== undefined;
  
  // Calculate default epsilon based on clustering mode
  const calculatedEpsilon = usePixelClustering && imageWidth && imageHeight
    ? calculateEpsilonFromLinesPixels(lines, imageWidth, imageHeight)
    : calculateEpsilonFromLines(lines);
    
  const { epsilon = calculatedEpsilon } = options;

  return useMemo(() => {
    if (lines.length === 0) {
      return {
        clusters: [],
        noiseLines: [],
        dbscanResult: { clusters: [], noise: [] },
      };
    }

    let pointsForClustering: Point[];
    
    if (usePixelClustering && imageWidth && imageHeight) {
      // Use pixel coordinates for clustering (matches Python implementation)
      pointsForClustering = lines.map(line => getLineCentroidPixels(line, imageWidth, imageHeight));
    } else {
      // Use normalized coordinates
      const lineCentroids = lines.map(getLineCentroid);
      pointsForClustering = useNormalization ? normalizePoints(lineCentroids) : lineCentroids;
    }

    const dbscanResult = dbscan(pointsForClustering, { epsilon, minPoints });

    let clusteredPoints = dbscanResult.clusters;
    let noisePoints = dbscanResult.noise;

    // Create mapping based on the clustering mode
    const pointToLineMap = new Map<string, Line>();
    
    if (usePixelClustering && imageWidth && imageHeight) {
      // For pixel clustering, map using pixel coordinates
      lines.forEach((line, index) => {
        const centroid = getLineCentroidPixels(line, imageWidth, imageHeight);
        const key = `${centroid.x.toFixed(2)},${centroid.y.toFixed(2)}`;
        pointToLineMap.set(key, line);
      });
    } else {
      // For normalized clustering
      const lineCentroids = lines.map(getLineCentroid);
      
      if (useNormalization && !usePixelClustering) {
        // Denormalize the clustered points back to original scale
        clusteredPoints = clusteredPoints.map(cluster => 
          denormalizePoints(cluster, lineCentroids)
        );
        noisePoints = denormalizePoints(noisePoints, lineCentroids);
      }
      
      lineCentroids.forEach((centroid, index) => {
        const key = `${centroid.x.toFixed(6)},${centroid.y.toFixed(6)}`;
        pointToLineMap.set(key, lines[index]);
      });
    }

    const clusters: ReceiptCluster[] = clusteredPoints.map(clusterPoints => {
      const clusterLines: Line[] = [];
      
      clusterPoints.forEach(point => {
        const precision = usePixelClustering ? 2 : 6;
        const key = `${point.x.toFixed(precision)},${point.y.toFixed(precision)}`;
        const line = pointToLineMap.get(key);
        if (line) {
          clusterLines.push(line);
        }
      });

      return {
        lines: clusterLines,
        boundingBox: computeClusterBoundingBox(clusterLines),
        centroid: computeClusterCentroid(clusterLines),
      };
    });

    const noiseLines: Line[] = [];
    noisePoints.forEach(point => {
      const precision = usePixelClustering ? 2 : 6;
      const key = `${point.x.toFixed(precision)},${point.y.toFixed(precision)}`;
      const line = pointToLineMap.get(key);
      if (line) {
        noiseLines.push(line);
      }
    });

    return {
      clusters,
      noiseLines,
      dbscanResult: {
        clusters: clusteredPoints,
        noise: noisePoints,
      },
    };
  }, [lines, epsilon, minPoints, useNormalization, imageWidth, imageHeight, usePixelClustering]);
};

export default useReceiptClustering;