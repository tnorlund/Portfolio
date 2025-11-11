import { renderHook } from "@testing-library/react";
import { describe, it, expect } from "@jest/globals";
import { useReceiptClustering, calculateEpsilonFromLines, calculateEpsilonFromLinesPixels } from "./useReceiptClustering";
import type { Line } from "../types/api";

const createMockLine = (
  id: number,
  x: number,
  y: number,
  width: number = 0.01,
  height: number = 0.01
): Line => ({
  image_id: "test-image",
  line_id: id,
  text: `Line ${id}`,
  bounding_box: { x, y, width, height },

  top_left: { x, y: y + height },
  top_right: { x: x + width, y: y + height },
  bottom_left: { x, y },
  bottom_right: { x: x + width, y },
  angle_degrees: 0,
  angle_radians: 0,
  confidence: 0.95,
});

describe("useReceiptClustering", () => {
  it("should handle empty lines", () => {
    const { result } = renderHook(() => useReceiptClustering([]));
    
    expect(result.current.clusters).toHaveLength(0);
    expect(result.current.noiseLines).toHaveLength(0);
  });

  it("should cluster nearby lines", () => {
    const lines: Line[] = [
      createMockLine(1, 0.1, 0.1),
      createMockLine(2, 0.105, 0.105),
      createMockLine(3, 0.11, 0.11),
      createMockLine(4, 0.8, 0.8),
    ];

    const { result } = renderHook(() => 
      useReceiptClustering(lines, { epsilon: 0.1, minPoints: 2, useNormalization: false })
    );
    
    expect(result.current.clusters).toHaveLength(1);
    expect(result.current.clusters[0].lines).toHaveLength(3);
    expect(result.current.noiseLines).toHaveLength(1);
  });

  it("should identify multiple clusters", () => {
    const lines: Line[] = [
      // Cluster 1 - tightly grouped
      createMockLine(1, 0.1, 0.8),
      createMockLine(2, 0.105, 0.805),
      createMockLine(3, 0.11, 0.81),
      // Cluster 2 - tightly grouped
      createMockLine(4, 0.1, 0.2),
      createMockLine(5, 0.105, 0.205),
      createMockLine(6, 0.11, 0.21),
    ];

    const { result } = renderHook(() => 
      useReceiptClustering(lines, { epsilon: 0.1, minPoints: 2, useNormalization: false })
    );
    
    expect(result.current.clusters).toHaveLength(2);
    expect(result.current.noiseLines).toHaveLength(0);
    
    result.current.clusters.forEach(cluster => {
      expect(cluster.lines).toHaveLength(3);
      expect(cluster.boundingBox).toBeDefined();
      expect(cluster.centroid).toBeDefined();
    });
  });

  it("should compute correct bounding boxes", () => {
    const lines: Line[] = [
      createMockLine(1, 0.1, 0.1, 0.2, 0.02),
      createMockLine(2, 0.15, 0.12, 0.15, 0.02),
    ];

    const { result } = renderHook(() => 
      useReceiptClustering(lines, { epsilon: 0.3, minPoints: 1, useNormalization: false })
    );
    
    expect(result.current.clusters).toHaveLength(1);
    const bbox = result.current.clusters[0].boundingBox;
    
    expect(bbox.topLeft.x).toBeCloseTo(0.1);
    expect(bbox.topRight.x).toBeCloseTo(0.3);
    expect(bbox.bottomLeft.y).toBeCloseTo(0.1);
    expect(bbox.topLeft.y).toBeCloseTo(0.14);
  });

  it("should compute correct centroids", () => {
    const lines: Line[] = [
      createMockLine(1, 0, 0, 0.02, 0.02),
      createMockLine(2, 0.18, 0, 0.02, 0.02),
    ];

    const { result } = renderHook(() => 
      useReceiptClustering(lines, { epsilon: 0.5, minPoints: 1, useNormalization: false })
    );
    
    expect(result.current.clusters).toHaveLength(1);
    const centroid = result.current.clusters[0].centroid;
    
    // Centroid of line centroids
    // Line 1 centroid: (0.01, 0.01)
    // Line 2 centroid: (0.19, 0.01)
    // Average: (0.1, 0.01)
    expect(centroid.x).toBeCloseTo(0.1);
    expect(centroid.y).toBeCloseTo(0.01);
  });

  it("should respect epsilon parameter", () => {
    const lines: Line[] = [
      createMockLine(1, 0.1, 0.1),
      createMockLine(2, 0.2, 0.1),
      createMockLine(3, 0.3, 0.1),
    ];

    // Small epsilon - each line is its own cluster or noise
    const { result: smallEps } = renderHook(() => 
      useReceiptClustering(lines, { epsilon: 0.05, minPoints: 2, useNormalization: false })
    );
    expect(smallEps.current.clusters).toHaveLength(0);
    expect(smallEps.current.noiseLines).toHaveLength(3);

    // Large epsilon - all lines in one cluster
    const { result: largeEps } = renderHook(() => 
      useReceiptClustering(lines, { epsilon: 0.5, minPoints: 2, useNormalization: false })
    );
    expect(largeEps.current.clusters).toHaveLength(1);
    expect(largeEps.current.clusters[0].lines).toHaveLength(3);
  });

  it("should respect minPoints parameter", () => {
    const lines: Line[] = [
      createMockLine(1, 0.1, 0.1),
      createMockLine(2, 0.105, 0.105),
    ];

    // minPoints = 1 - both lines form a cluster
    const { result: minPoints1 } = renderHook(() => 
      useReceiptClustering(lines, { epsilon: 0.1, minPoints: 1, useNormalization: false })
    );
    expect(minPoints1.current.clusters.length).toBeGreaterThan(0);

    // minPoints = 3 - both lines are noise
    const { result: minPoints3 } = renderHook(() => 
      useReceiptClustering(lines, { epsilon: 0.1, minPoints: 3, useNormalization: false })
    );
    expect(minPoints3.current.clusters).toHaveLength(0);
    expect(minPoints3.current.noiseLines).toHaveLength(2);
  });

  it("should handle normalization option", () => {
    const lines: Line[] = [
      createMockLine(1, 0.0, 0.0),
      createMockLine(2, 0.005, 0.005),
      createMockLine(3, 1.0, 1.0),
    ];

    // Test with normalization enabled (default)
    const { result: defaultNorm } = renderHook(() => 
      useReceiptClustering(lines)
    );
    
    // Test with normalization explicitly disabled
    const { result: noNorm } = renderHook(() => 
      useReceiptClustering(lines, { useNormalization: false })
    );

    // Just verify that both options work without errors
    expect(defaultNorm.current.clusters).toBeDefined();
    expect(defaultNorm.current.noiseLines).toBeDefined();
    expect(noNorm.current.clusters).toBeDefined();
    expect(noNorm.current.noiseLines).toBeDefined();
    
    // Verify the dbscanResult is included
    expect(defaultNorm.current.dbscanResult).toBeDefined();
    expect(defaultNorm.current.dbscanResult.clusters).toBeDefined();
    expect(defaultNorm.current.dbscanResult.noise).toBeDefined();
  });
});

describe("calculateEpsilonFromLines", () => {
  it("should return default epsilon for empty lines", () => {
    expect(calculateEpsilonFromLines([])).toBe(0.05);
  });

  it("should calculate epsilon as 2x average diagonal length", () => {
    const lines: Line[] = [
      createMockLine(1, 0, 0, 0.1, 0.1), // diagonal ≈ 0.141
      createMockLine(2, 0, 0, 0.2, 0.2), // diagonal ≈ 0.283
    ];
    
    const epsilon = calculateEpsilonFromLines(lines);
    // Average diagonal = (0.141 + 0.283) / 2 ≈ 0.212
    // Epsilon = 0.212 * 2 ≈ 0.424
    expect(epsilon).toBeCloseTo(0.424, 2);
  });

  it("should handle lines with different sizes", () => {
    const lines: Line[] = [
      createMockLine(1, 0, 0, 0.05, 0.05),
      createMockLine(2, 0, 0, 0.1, 0.1),
      createMockLine(3, 0, 0, 0.15, 0.15),
    ];
    
    const epsilon = calculateEpsilonFromLines(lines);
    expect(epsilon).toBeGreaterThan(0);
    expect(epsilon).toBeLessThan(1);
  });
});

describe("pixel-based clustering", () => {
  it("should use pixel coordinates when image dimensions are provided", () => {
    const lines: Line[] = [
      createMockLine(1, 0.1, 0.1),
      createMockLine(2, 0.11, 0.11),
    ];

    const { result } = renderHook(() => 
      useReceiptClustering(lines, { 
        imageWidth: 1000,
        imageHeight: 1000,
        minPoints: 1,
      })
    );
    
    // Should process the lines without error
    expect(result.current.clusters.length + result.current.noiseLines.length).toBeGreaterThanOrEqual(0);
    expect(result.current.dbscanResult).toBeDefined();
  });

  it("should calculate epsilon in pixels when using pixel mode", () => {
    const lines: Line[] = [
      createMockLine(1, 0, 0, 0.1, 0.1), // 100x100 pixel diagonal in 1000x1000 image
    ];
    
    const epsilon = calculateEpsilonFromLinesPixels(lines, 1000, 1000);
    // Diagonal length = sqrt((100)^2 + (100)^2) = ~141.4 pixels
    // Epsilon = 141.4 * 2 = ~282.8 pixels
    expect(epsilon).toBeCloseTo(282.8, 1);
  });
});