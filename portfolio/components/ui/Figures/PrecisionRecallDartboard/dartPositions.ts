import type { DartPosition, DartboardScenario } from "./types";

/**
 * Seeded pseudo-random number generator for reproducible dart positions.
 */
function seededRandom(seed: number): () => number {
  let s = seed;
  return function () {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

/**
 * Generate dart positions clustered around a center point using
 * a Gaussian-like distribution.
 */
function generateClusteredDarts(
  count: number,
  centerX: number,
  centerY: number,
  spreadRadius: number,
  seed: number
): DartPosition[] {
  const random = seededRandom(seed);
  const darts: DartPosition[] = [];

  for (let i = 0; i < count; i++) {
    // Box-Muller approximation for Gaussian distribution
    const u1 = Math.max(0.001, random());
    const u2 = random();
    const r = spreadRadius * Math.sqrt(-2 * Math.log(u1)) * 0.5;
    const theta = 2 * Math.PI * u2;

    darts.push({
      x: Math.max(0.05, Math.min(0.95, centerX + r * Math.cos(theta))),
      y: Math.max(0.05, Math.min(0.95, centerY + r * Math.sin(theta))),
      angle: random() * 20 - 10, // Slight random rotation
    });
  }

  return darts;
}

/**
 * Generate dart positions scattered uniformly across the board.
 */
function generateScatteredDarts(
  count: number,
  maxRadius: number,
  seed: number
): DartPosition[] {
  const random = seededRandom(seed);
  const darts: DartPosition[] = [];

  for (let i = 0; i < count; i++) {
    // Uniform distribution in a circle
    const r = maxRadius * Math.sqrt(random());
    const theta = 2 * Math.PI * random();

    darts.push({
      x: 0.5 + r * Math.cos(theta),
      y: 0.5 + r * Math.sin(theta),
      angle: random() * 40 - 20, // More random rotation for scattered look
    });
  }

  return darts;
}

/**
 * Pre-defined scenarios for the 2x2 grid.
 * Grid layout:
 *   [0] Low P, Low R      [1] Low P, High R
 *   [2] High P, Low R     [3] High P, High R
 */
export const DARTBOARD_SCENARIOS: DartboardScenario[] = [
  {
    id: "low-precision-low-recall",
    title: "Low Precision, Low Recall",
    precision: "low",
    recall: "low",
    description: "Few darts, spread out (limited and inconsistent)",
    darts: generateScatteredDarts(
      4, // Few darts (low recall)
      0.4, // Wide spread
      789 // Seed
    ),
  },
  {
    id: "low-precision-high-recall",
    title: "Low Precision, High Recall",
    precision: "low",
    recall: "high",
    description:
      "Many darts, spread across the target (comprehensive but inconsistent)",
    darts: generateScatteredDarts(
      14, // Many darts (high recall)
      0.38, // Spread across board
      456 // Seed
    ),
  },
  {
    id: "high-precision-low-recall",
    title: "High Precision, Low Recall",
    precision: "high",
    recall: "low",
    description: "Few darts, tightly clustered (accurate but limited coverage)",
    darts: generateClusteredDarts(
      4, // Few darts (low recall)
      0.38, // Offset from center
      0.42, // Offset from center
      0.04, // Very tight cluster (high precision)
      123 // Seed
    ),
  },
  {
    id: "high-precision-high-recall",
    title: "High Precision, High Recall",
    precision: "high",
    recall: "high",
    description:
      "Many darts, all clustered in the bullseye (accurate and comprehensive)",
    darts: generateClusteredDarts(
      12, // Many darts (high recall)
      0.5, // Center X (bullseye)
      0.5, // Center Y (bullseye)
      0.06, // Tight cluster (high precision)
      42 // Seed
    ),
  },
];
