import { useRef, useCallback } from "react";
import type { LabelNode } from "./types";

// Physics constants
const ATTRACTION_STRENGTH = 0.02;
const REPULSION_STRENGTH = 1.5;
const DAMPING = 0.85;
const VELOCITY_THRESHOLD = 0.1;
const PADDING = 8;

/**
 * Seeded pseudo-random number generator for reproducible initial positions.
 */
function seededRandom(seed: number): () => number {
  let s = seed;
  return function () {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

/**
 * Calculate distance-based repulsion force between two nodes.
 */
function applyRepulsion(a: LabelNode, b: LabelNode): void {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  const dist = Math.sqrt(dx * dx + dy * dy) || 1;

  // Minimum distance based on combined dimensions
  const minDistX = (a.width + b.width) / 2 + PADDING;
  const minDistY = (a.height + b.height) / 2 + PADDING;
  const minDist = Math.sqrt(minDistX * minDistX + minDistY * minDistY) * 0.6;

  if (dist < minDist) {
    const force = ((minDist - dist) / dist) * REPULSION_STRENGTH;
    const fx = dx * force;
    const fy = dy * force;

    a.vx += fx;
    a.vy += fy;
    b.vx -= fx;
    b.vy -= fy;
  }
}

/**
 * Keep node within bounds with soft boundary force.
 */
function applyBoundaryForce(
  node: LabelNode,
  width: number,
  height: number
): void {
  const margin = 20;
  const halfW = node.width / 2;
  const halfH = node.height / 2;

  // Left boundary
  if (node.x - halfW < margin) {
    node.vx += (margin - (node.x - halfW)) * 0.1;
  }
  // Right boundary
  if (node.x + halfW > width - margin) {
    node.vx -= (node.x + halfW - (width - margin)) * 0.1;
  }
  // Top boundary
  if (node.y - halfH < margin) {
    node.vy += (margin - (node.y - halfH)) * 0.1;
  }
  // Bottom boundary
  if (node.y + halfH > height - margin) {
    node.vy -= (node.y + halfH - (height - margin)) * 0.1;
  }
}

/**
 * Initialize nodes with random positions scattered from the center.
 */
export function initializeNodes(
  labels: Array<{ label: string; displayName: string; validCount: number }>,
  width: number,
  height: number,
  minFontSize: number,
  maxFontSize: number,
  seed: number = 42
): LabelNode[] {
  const random = seededRandom(seed);
  const centerX = width / 2;
  const centerY = height / 2;

  // Find min/max counts for font size scaling
  const counts = labels.map((l) => l.validCount);
  const minCount = Math.min(...counts);
  const maxCount = Math.max(...counts);
  const countRange = maxCount - minCount || 1;

  return labels.map((item) => {
    // Calculate font size based on relative count
    const normalizedCount = (item.validCount - minCount) / countRange;
    const fontSize = minFontSize + normalizedCount * (maxFontSize - minFontSize);

    // Estimate text dimensions
    const textWidth = item.displayName.length * fontSize * 0.55;
    const textHeight = fontSize * 1.2;

    // Start from random positions around edges
    const edge = Math.floor(random() * 4);
    let x: number, y: number;

    switch (edge) {
      case 0: // Top
        x = random() * width;
        y = -50;
        break;
      case 1: // Right
        x = width + 50;
        y = random() * height;
        break;
      case 2: // Bottom
        x = random() * width;
        y = height + 50;
        break;
      default: // Left
        x = -50;
        y = random() * height;
        break;
    }

    // Initial velocity toward center
    const towardCenterX = (centerX - x) * 0.05;
    const towardCenterY = (centerY - y) * 0.05;

    return {
      label: item.label,
      displayName: item.displayName,
      validCount: item.validCount,
      fontSize,
      width: textWidth,
      height: textHeight,
      x,
      y,
      vx: towardCenterX + (random() - 0.5) * 2,
      vy: towardCenterY + (random() - 0.5) * 2,
    };
  });
}

/**
 * Custom hook for running physics simulation on label nodes.
 */
export function usePhysicsSimulation(
  svgWidth: number,
  svgHeight: number
): {
  simulateStep: (nodes: LabelNode[]) => boolean;
  isSettled: (nodes: LabelNode[]) => boolean;
} {
  const centerX = svgWidth / 2;
  const centerY = svgHeight / 2;
  const frameCountRef = useRef(0);

  const simulateStep = useCallback(
    (nodes: LabelNode[]): boolean => {
      frameCountRef.current++;

      // Apply forces to all nodes
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];

        // Attraction to center
        const dx = centerX - node.x;
        const dy = centerY - node.y;
        node.vx += dx * ATTRACTION_STRENGTH;
        node.vy += dy * ATTRACTION_STRENGTH;

        // Repulsion from other nodes
        for (let j = i + 1; j < nodes.length; j++) {
          applyRepulsion(node, nodes[j]);
        }

        // Boundary forces
        applyBoundaryForce(node, svgWidth, svgHeight);
      }

      // Update positions with damping
      let totalVelocity = 0;
      for (const node of nodes) {
        node.vx *= DAMPING;
        node.vy *= DAMPING;
        node.x += node.vx;
        node.y += node.vy;

        totalVelocity += Math.abs(node.vx) + Math.abs(node.vy);
      }

      // Return true if simulation should continue
      const shouldContinue =
        totalVelocity > VELOCITY_THRESHOLD && frameCountRef.current < 500;

      if (!shouldContinue) {
        frameCountRef.current = 0;
      }

      return shouldContinue;
    },
    [centerX, centerY, svgWidth, svgHeight]
  );

  const isSettled = useCallback((nodes: LabelNode[]): boolean => {
    let totalVelocity = 0;
    for (const node of nodes) {
      totalVelocity += Math.abs(node.vx) + Math.abs(node.vy);
    }
    return totalVelocity <= VELOCITY_THRESHOLD;
  }, []);

  return { simulateStep, isSettled };
}
