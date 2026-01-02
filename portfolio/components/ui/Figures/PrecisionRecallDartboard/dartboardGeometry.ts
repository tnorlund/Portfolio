/**
 * Dartboard geometry calculations.
 *
 * Standard dartboard dimensions normalized to outer double ring = 1.0
 * Original measurements in mm:
 * - Inner bull (double bull): 6.35mm
 * - Outer bull (single bull): 15.9mm
 * - Inner triple ring: 99mm
 * - Outer triple ring: 107mm
 * - Inner double ring: 162mm
 * - Outer double ring: 170mm
 */

export const DARTBOARD_GEOMETRY = {
  // Radii normalized to outer edge = 1.0
  INNER_BULL_RADIUS: 6.35 / 170,
  OUTER_BULL_RADIUS: 15.9 / 170,
  INNER_TRIPLE_RADIUS: 99 / 170,
  OUTER_TRIPLE_RADIUS: 107 / 170,
  INNER_DOUBLE_RADIUS: 162 / 170,
  OUTER_DOUBLE_RADIUS: 1.0,

  // Number labels radius (outside the double ring)
  NUMBER_RADIUS: 1.12,
};

// Dartboard numbers in clockwise order starting from top (12 o'clock position)
export const DARTBOARD_NUMBERS = [
  20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5,
];

// Each segment spans 18 degrees (360 / 20)
export const SEGMENT_ANGLE = 18;

// Starting angle offset to position 20 at top center
// In SVG, 0 degrees is at 3 o'clock, so we offset to put 20 at top
export const START_ANGLE_OFFSET = -90 - SEGMENT_ANGLE / 2;

/**
 * Calculate the SVG path for a dartboard segment (arc wedge).
 */
export function calculateSegmentPath(
  centerX: number,
  centerY: number,
  innerRadius: number,
  outerRadius: number,
  startAngleDeg: number,
  endAngleDeg: number,
  scale: number
): string {
  const toRad = (deg: number) => (deg * Math.PI) / 180;

  const startAngle = toRad(startAngleDeg);
  const endAngle = toRad(endAngleDeg);

  // Inner arc start/end points
  const innerX1 = centerX + Math.cos(startAngle) * innerRadius * scale;
  const innerY1 = centerY + Math.sin(startAngle) * innerRadius * scale;
  const innerX2 = centerX + Math.cos(endAngle) * innerRadius * scale;
  const innerY2 = centerY + Math.sin(endAngle) * innerRadius * scale;

  // Outer arc start/end points
  const outerX1 = centerX + Math.cos(startAngle) * outerRadius * scale;
  const outerY1 = centerY + Math.sin(startAngle) * outerRadius * scale;
  const outerX2 = centerX + Math.cos(endAngle) * outerRadius * scale;
  const outerY2 = centerY + Math.sin(endAngle) * outerRadius * scale;

  const largeArcFlag = endAngleDeg - startAngleDeg > 180 ? 1 : 0;

  // Path: start at inner arc start, line to outer arc start,
  // arc to outer arc end, line to inner arc end, arc back to start
  return [
    `M ${innerX1} ${innerY1}`,
    `L ${outerX1} ${outerY1}`,
    `A ${outerRadius * scale} ${outerRadius * scale} 0 ${largeArcFlag} 1 ${outerX2} ${outerY2}`,
    `L ${innerX2} ${innerY2}`,
    `A ${innerRadius * scale} ${innerRadius * scale} 0 ${largeArcFlag} 0 ${innerX1} ${innerY1}`,
    "Z",
  ].join(" ");
}

/**
 * Get the fill color for a segment based on its index and ring type.
 * Monochromatic: singles alternate text/background, doubles/triples are inverse.
 */
export function getSegmentColor(
  segmentIndex: number,
  ringType: "single_outer" | "single_inner" | "triple" | "double"
): string {
  const isEven = segmentIndex % 2 === 0;

  if (ringType === "double" || ringType === "triple") {
    // Inverse of singles: even = background, odd = text
    return isEven ? "var(--background-color)" : "var(--text-color)";
  }

  // Singles: even = text, odd = background
  return isEven ? "var(--text-color)" : "var(--background-color)";
}

/**
 * Convert polar coordinates to cartesian for SVG.
 */
export function polarToCartesian(
  angleDeg: number,
  radius: number,
  centerX: number,
  centerY: number,
  scale: number
): { x: number; y: number } {
  const angleRad = (angleDeg * Math.PI) / 180;
  return {
    x: centerX + Math.cos(angleRad) * radius * scale,
    y: centerY + Math.sin(angleRad) * radius * scale,
  };
}
