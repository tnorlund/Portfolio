import type { LabelNode } from "./types";

// Physics constants
const ATTRACTION_STRENGTH = 0.02;
const REPULSION_STRENGTH = 1.5;
const DAMPING = 0.85;
const VELOCITY_THRESHOLD = 0.1;
const PADDING = 12;
const MAX_ITERATIONS = 500;

// Radius and font sizing (exported for use in component)
export const MIN_RADIUS = 15;
export const MAX_RADIUS = 80;
export const MIN_FONT_INSIDE = 16;  // Minimum font to fit inside circle (matches body font-size)
export const MAX_FONT_INSIDE = 48;  // Maximum font for aesthetics
export const EXTERNAL_FONT_SIZE = 16; // External floating labels (matches body font-size)
export const REFERENCE_FONT_SIZE = 20; // Font size used for measurement

// Padding ratio for fit check: 1.0 = text can fill entire inscribed rectangle
export const TEXT_PADDING_RATIO = 0.80;

/**
 * Seeded pseudo-random number generator for reproducible positions.
 */
function seededRandom(seed: number): () => number {
  let s = seed;
  return function () {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

/**
 * Apply repulsion force between two nodes.
 */
function applyRepulsion(a: LabelNode, b: LabelNode): void {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  const dist = Math.sqrt(dx * dx + dy * dy) || 1;
  const minDist = a.radius + b.radius + PADDING;

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
 * Apply boundary force to keep node within bounds.
 * Also hard-clamp positions to prevent overflow.
 */
function applyBoundaryForce(
  node: LabelNode,
  width: number,
  height: number
): void {
  const margin = 40;  // Larger margin for leader lines
  const r = node.radius;

  // Soft boundary forces
  if (node.x - r < margin) {
    node.vx += (margin - (node.x - r)) * 0.2;
  }
  if (node.x + r > width - margin) {
    node.vx -= (node.x + r - (width - margin)) * 0.2;
  }
  if (node.y - r < margin) {
    node.vy += (margin - (node.y - r)) * 0.2;
  }
  if (node.y + r > height - margin) {
    node.vy -= (node.y + r - (height - margin)) * 0.2;
  }

  // Hard clamp to prevent overflow - leave room for leader lines and text
  const minMargin = 80; // Space for leader line + text
  node.x = Math.max(r + minMargin, Math.min(width - r - minMargin, node.x));
  node.y = Math.max(r + minMargin, Math.min(height - r - minMargin, node.y));
}

/**
 * Run one step of the physics simulation.
 * Returns true if simulation should continue.
 */
function simulateStep(
  nodes: LabelNode[],
  centerX: number,
  centerY: number,
  width: number,
  height: number,
  externalLabels?: Set<string>
): boolean {
  // Apply forces
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];

    // Attraction to center (or repulsion for external labels)
    const dx = centerX - node.x;
    const dy = centerY - node.y;

    // External labels get a gentle outward nudge instead of inward pull
    const isExternal = externalLabels?.has(node.label);
    const multiplier = isExternal ? -0.3 : 1;

    node.vx += dx * ATTRACTION_STRENGTH * multiplier;
    node.vy += dy * ATTRACTION_STRENGTH * multiplier;

    // Repulsion from other nodes
    for (let j = i + 1; j < nodes.length; j++) {
      applyRepulsion(node, nodes[j]);
    }

    // Boundary forces
    applyBoundaryForce(node, width, height);
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

  return totalVelocity > VELOCITY_THRESHOLD;
}

/**
 * Initialize nodes with start positions and calculate final settled positions.
 * Runs physics simulation synchronously to completion.
 */
export function initializeAndSolve(
  labels: Array<{ label: string; displayLines: string[]; validCount: number }>,
  width: number,
  height: number,
  seed: number = 42
): { startNodes: LabelNode[]; endNodes: LabelNode[] } {
  // Guard against empty labels array (Math.min/max on empty spread returns Infinity/-Infinity)
  if (labels.length === 0) {
    return { startNodes: [], endNodes: [] };
  }

  const random = seededRandom(seed);
  const centerX = width / 2;
  const centerY = height / 2;

  // Find min/max counts for radius scaling
  const counts = labels.map((l) => l.validCount);
  const minCount = Math.min(...counts);
  const maxCount = Math.max(...counts);
  const countRange = maxCount - minCount || 1;

  // Create nodes with start positions (scattered from edges)
  const startNodes: LabelNode[] = labels.map((item) => {
    // Calculate radius based on relative count (area proportional)
    const normalizedCount = (item.validCount - minCount) / countRange;
    const radius = MIN_RADIUS + Math.sqrt(normalizedCount) * (MAX_RADIUS - MIN_RADIUS);

    // Use reference font size for measurement - actual size calculated after measuring
    const fontSize = REFERENCE_FONT_SIZE;

    // textFitsInside will be determined by measurement in the component
    const textFitsInside: boolean | null = null;

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

    return {
      label: item.label,
      displayLines: item.displayLines,
      validCount: item.validCount,
      radius,
      fontSize, // Use REFERENCE_FONT_SIZE; actual size calculated after measurement
      textFitsInside,
      x,
      y,
      vx: 0,
      vy: 0,
    };
  });

  // Create a copy for physics simulation
  const endNodes: LabelNode[] = startNodes.map((n) => ({
    ...n,
    // Start simulation from center area with small random offset
    x: centerX + (random() - 0.5) * 100,
    y: centerY + (random() - 0.5) * 100,
    vx: 0,
    vy: 0,
  }));

  // Run physics to completion synchronously
  let iterations = 0;
  while (iterations < MAX_ITERATIONS) {
    const shouldContinue = simulateStep(endNodes, centerX, centerY, width, height);
    iterations++;
    if (!shouldContinue) break;
  }

  // Development-mode warning if layout didn't converge
  if (process.env.NODE_ENV === "development" && iterations >= MAX_ITERATIONS) {
    console.warn(
      `LabelWordCloud: Physics simulation reached max iterations (${MAX_ITERATIONS}) without convergence`
    );
  }

  // Zero out velocities in final state
  for (const node of endNodes) {
    node.vx = 0;
    node.vy = 0;
  }

  return { startNodes, endNodes };
}

/**
 * Re-run physics simulation to push external-label circles to the edges.
 * This is called after text measurement determines which labels are external.
 */
export function optimizeForExternalLabels(
  nodes: LabelNode[],
  externalLabels: Set<string>,
  width: number,
  height: number
): LabelNode[] {
  if (externalLabels.size === 0) return nodes;

  const centerX = width / 2;
  const centerY = height / 2;

  // Create a working copy with reset velocities
  const optimizedNodes: LabelNode[] = nodes.map((n) => ({
    ...n,
    vx: 0,
    vy: 0,
  }));

  // Run physics with external labels being pushed outward
  let iterations = 0;
  const maxIterations = 150; // Gentle adjustment, not aggressive push
  while (iterations < maxIterations) {
    const shouldContinue = simulateStep(
      optimizedNodes,
      centerX,
      centerY,
      width,
      height,
      externalLabels
    );
    iterations++;
    if (!shouldContinue) break;
  }

  // Zero out velocities
  for (const node of optimizedNodes) {
    node.vx = 0;
    node.vy = 0;
  }

  return optimizedNodes;
}
