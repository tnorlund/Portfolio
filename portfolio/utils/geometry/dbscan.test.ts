import { describe, it, expect } from "@jest/globals";
import { dbscan, euclideanDistance, normalizePoints, denormalizePoints } from "./dbscan";
import type { Point } from "../../types/api";

describe("DBSCAN", () => {
  describe("euclideanDistance", () => {
    it("should calculate distance between two points", () => {
      const a: Point = { x: 0, y: 0 };
      const b: Point = { x: 3, y: 4 };
      expect(euclideanDistance(a, b)).toBe(5);
    });

    it("should return 0 for same point", () => {
      const a: Point = { x: 5, y: 5 };
      expect(euclideanDistance(a, a)).toBe(0);
    });
  });

  describe("normalizePoints", () => {
    it("should normalize points to [0, 1] range", () => {
      const points: Point[] = [
        { x: 0, y: 0 },
        { x: 10, y: 20 },
        { x: 5, y: 10 },
      ];
      const normalized = normalizePoints(points);
      
      expect(normalized[0]).toEqual({ x: 0, y: 0 });
      expect(normalized[1]).toEqual({ x: 1, y: 1 });
      expect(normalized[2]).toEqual({ x: 0.5, y: 0.5 });
    });

    it("should handle empty array", () => {
      expect(normalizePoints([])).toEqual([]);
    });

    it("should handle single point", () => {
      const points: Point[] = [{ x: 5, y: 10 }];
      const normalized = normalizePoints(points);
      expect(normalized[0]).toEqual({ x: 0, y: 0 });
    });
  });

  describe("denormalizePoints", () => {
    it("should restore original scale", () => {
      const original: Point[] = [
        { x: 0, y: 0 },
        { x: 10, y: 20 },
        { x: 5, y: 10 },
      ];
      const normalized: Point[] = [
        { x: 0, y: 0 },
        { x: 1, y: 1 },
        { x: 0.5, y: 0.5 },
      ];
      
      const denormalized = denormalizePoints(normalized, original);
      
      expect(denormalized[0]).toEqual({ x: 0, y: 0 });
      expect(denormalized[1]).toEqual({ x: 10, y: 20 });
      expect(denormalized[2]).toEqual({ x: 5, y: 10 });
    });
  });

  describe("dbscan clustering", () => {
    it("should identify a single cluster", () => {
      const points: Point[] = [
        { x: 0, y: 0 },
        { x: 0.1, y: 0.1 },
        { x: 0.2, y: 0 },
        { x: 0.1, y: 0.2 },
      ];
      
      const result = dbscan(points, { epsilon: 0.3, minPoints: 2 });
      
      expect(result.clusters).toHaveLength(1);
      expect(result.clusters[0]).toHaveLength(4);
      expect(result.noise).toHaveLength(0);
    });

    it("should identify multiple clusters", () => {
      const points: Point[] = [
        // Cluster 1
        { x: 0, y: 0 },
        { x: 0.1, y: 0.1 },
        { x: 0.2, y: 0 },
        // Cluster 2
        { x: 5, y: 5 },
        { x: 5.1, y: 5.1 },
        { x: 5.2, y: 5 },
      ];
      
      const result = dbscan(points, { epsilon: 0.3, minPoints: 2 });
      
      expect(result.clusters).toHaveLength(2);
      expect(result.clusters[0]).toHaveLength(3);
      expect(result.clusters[1]).toHaveLength(3);
      expect(result.noise).toHaveLength(0);
    });

    it("should identify noise points", () => {
      const points: Point[] = [
        // Cluster
        { x: 0, y: 0 },
        { x: 0.1, y: 0.1 },
        { x: 0.2, y: 0 },
        { x: 0.1, y: 0 },
        // Noise
        { x: 10, y: 10 },
      ];
      
      const result = dbscan(points, { epsilon: 0.3, minPoints: 3 });
      
      expect(result.clusters).toHaveLength(1);
      expect(result.clusters[0]).toHaveLength(4);
      expect(result.noise).toHaveLength(1);
      expect(result.noise[0]).toEqual({ x: 10, y: 10 });
    });

    it("should handle empty input", () => {
      const result = dbscan([], { epsilon: 0.5, minPoints: 3 });
      
      expect(result.clusters).toHaveLength(0);
      expect(result.noise).toHaveLength(0);
    });

    it("should handle single point as noise", () => {
      const points: Point[] = [{ x: 0, y: 0 }];
      const result = dbscan(points, { epsilon: 0.5, minPoints: 2 });
      
      expect(result.clusters).toHaveLength(0);
      expect(result.noise).toHaveLength(1);
    });

    it("should use custom distance function", () => {
      const manhattanDistance = (a: Point, b: Point): number => {
        return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
      };

      const points: Point[] = [
        { x: 0, y: 0 },
        { x: 0.5, y: 0.5 },
        { x: 1, y: 0 },
      ];
      
      const result = dbscan(points, { 
        epsilon: 1.2, 
        minPoints: 2,
        distanceFunction: manhattanDistance
      });
      
      expect(result.clusters).toHaveLength(1);
      expect(result.clusters[0]).toHaveLength(3);
    });

    it("should correctly cluster receipt-like data", () => {
      const points: Point[] = [
        // Top receipt lines
        { x: 0.1, y: 0.9 },
        { x: 0.15, y: 0.85 },
        { x: 0.12, y: 0.87 },
        // Middle receipt lines
        { x: 0.11, y: 0.5 },
        { x: 0.14, y: 0.48 },
        { x: 0.13, y: 0.52 },
        // Bottom receipt lines
        { x: 0.1, y: 0.1 },
        { x: 0.15, y: 0.12 },
        // Noise
        { x: 0.9, y: 0.9 },
      ];
      
      const result = dbscan(points, { epsilon: 0.1, minPoints: 2 });
      
      expect(result.clusters.length).toBeGreaterThanOrEqual(2);
      expect(result.noise.length).toBeGreaterThanOrEqual(1);
    });
  });
});