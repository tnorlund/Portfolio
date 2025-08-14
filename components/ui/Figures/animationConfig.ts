export interface AnimationConfig {
  totalDelayForLines: number;
  convexHullDelay: number;
  convexHullDuration: number;
  centroidDelay: number;
  extentsDelay: number;
  extentsDuration: number;
  hullEdgeAlignmentDuration: number;
  receiptDelay: number;
}

export function getAnimationConfig(
  lineCount: number,
  hullPointCount: number,
): AnimationConfig {
  const totalDelayForLines = lineCount > 0 ? (lineCount - 1) * 30 + 800 : 0;
  const convexHullDelay = totalDelayForLines + 300; // Start convex hull after lines
  const convexHullDuration = hullPointCount * 200 + 500;
  const centroidDelay = convexHullDelay + convexHullDuration + 200; // Hull centroid after convex hull
  const extentsDelay = centroidDelay + 600; // Extents after centroid
  const extentsDuration = 4 * 300 + 500; // 4 extent lines * 300ms + buffer
  const hullEdgeAlignmentDuration = 800 + 1000; // 2 steps total duration
  const receiptDelay =
    extentsDelay + extentsDuration + hullEdgeAlignmentDuration + 300; // Receipt after Hull Edge Alignment

  return {
    totalDelayForLines,
    convexHullDelay,
    convexHullDuration,
    centroidDelay,
    extentsDelay,
    extentsDuration,
    hullEdgeAlignmentDuration,
    receiptDelay,
  };
}
