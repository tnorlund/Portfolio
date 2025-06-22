import type { Point } from "../../types/api";

export interface DBSCANParams {
  epsilon: number;
  minPoints: number;
  distanceFunction?: (a: Point, b: Point) => number;
}

export interface DBSCANResult {
  clusters: Point[][];
  noise: Point[];
}

export const euclideanDistance = (a: Point, b: Point): number => {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy);
};

export const dbscan = (
  points: Point[],
  params: DBSCANParams
): DBSCANResult => {
  const { epsilon, minPoints, distanceFunction = euclideanDistance } = params;
  
  if (points.length === 0) {
    return { clusters: [], noise: [] };
  }

  const visited = new Set<number>();
  const clustered = new Set<number>();
  const clusters: Point[][] = [];
  const noise: Point[] = [];

  const getNeighbors = (pointIndex: number): number[] => {
    const neighbors: number[] = [];
    const point = points[pointIndex];
    
    for (let i = 0; i < points.length; i++) {
      if (i !== pointIndex) {
        const distance = distanceFunction(point, points[i]);
        if (distance <= epsilon) {
          neighbors.push(i);
        }
      }
    }
    
    return neighbors;
  };

  const expandCluster = (
    pointIndex: number,
    neighbors: number[],
    cluster: Point[]
  ): void => {
    cluster.push(points[pointIndex]);
    clustered.add(pointIndex);

    const seeds = [...neighbors];
    let seedIndex = 0;

    while (seedIndex < seeds.length) {
      const currentPointIndex = seeds[seedIndex];
      
      if (!visited.has(currentPointIndex)) {
        visited.add(currentPointIndex);
        const currentNeighbors = getNeighbors(currentPointIndex);
        
        if (currentNeighbors.length >= minPoints) {
          for (const neighborIndex of currentNeighbors) {
            if (!seeds.includes(neighborIndex)) {
              seeds.push(neighborIndex);
            }
          }
        }
      }

      if (!clustered.has(currentPointIndex)) {
        cluster.push(points[currentPointIndex]);
        clustered.add(currentPointIndex);
      }

      seedIndex++;
    }
  };

  for (let i = 0; i < points.length; i++) {
    if (visited.has(i)) continue;
    
    visited.add(i);
    const neighbors = getNeighbors(i);
    
    if (neighbors.length < minPoints) {
      noise.push(points[i]);
    } else {
      const cluster: Point[] = [];
      expandCluster(i, neighbors, cluster);
      clusters.push(cluster);
    }
  }

  return { clusters, noise };
};

export const normalizePoints = (points: Point[]): Point[] => {
  if (points.length === 0) return [];

  const minX = Math.min(...points.map(p => p.x));
  const maxX = Math.max(...points.map(p => p.x));
  const minY = Math.min(...points.map(p => p.y));
  const maxY = Math.max(...points.map(p => p.y));

  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;

  return points.map(p => ({
    x: (p.x - minX) / rangeX,
    y: (p.y - minY) / rangeY,
  }));
};

export const denormalizePoints = (
  normalizedPoints: Point[],
  originalPoints: Point[]
): Point[] => {
  if (originalPoints.length === 0) return normalizedPoints;

  const minX = Math.min(...originalPoints.map(p => p.x));
  const maxX = Math.max(...originalPoints.map(p => p.x));
  const minY = Math.min(...originalPoints.map(p => p.y));
  const maxY = Math.max(...originalPoints.map(p => p.y));

  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;

  return normalizedPoints.map(p => ({
    x: p.x * rangeX + minX,
    y: p.y * rangeY + minY,
  }));
};