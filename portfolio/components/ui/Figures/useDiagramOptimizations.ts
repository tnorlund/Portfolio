import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * Hook to detect if an element is visible in the viewport.
 * Uses IntersectionObserver for efficient visibility detection.
 *
 * @param threshold - How much of the element must be visible (0-1). Default 0.1 (10%)
 * @param rootMargin - Margin around the viewport. Positive values trigger earlier.
 */
export function useIsVisible(
  threshold: number = 0.1,
  rootMargin: string = "100px"
): [React.RefObject<HTMLDivElement | null>, boolean] {
  const containerRef = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    // Check if IntersectionObserver is available (not in SSR)
    if (typeof IntersectionObserver === "undefined") {
      // Fallback: assume visible
      setIsVisible(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsVisible(entry.isIntersecting);
      },
      {
        threshold,
        rootMargin, // Start animating slightly before element is in view
      }
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [threshold, rootMargin]);

  return [containerRef, isVisible];
}

/**
 * Cache for SVG path lengths to avoid expensive getTotalLength() calls.
 * Maps path element to its total length.
 */
const pathLengthCache = new WeakMap<SVGPathElement, number>();

/**
 * Gets the total length of an SVG path, with caching.
 * Avoids repeated calls to the expensive getTotalLength() method.
 */
export function getCachedPathLength(path: SVGPathElement): number {
  let length = pathLengthCache.get(path);
  if (length === undefined) {
    length = path.getTotalLength();
    pathLengthCache.set(path, length);
  }
  return length;
}

/**
 * Gets a point on an SVG path at a given percentage (0-100).
 * Uses cached path length for better performance.
 *
 * @param ref - React ref to the SVG path element
 * @param pct - Percentage along the path (0-100)
 */
export function pointAtCached(
  ref: React.RefObject<SVGPathElement | null>,
  pct: number
): { x: number; y: number } {
  const el = ref.current;
  if (!el) return { x: 0, y: 0 };
  const len = getCachedPathLength(el);
  return el.getPointAtLength(((pct % 100) / 100) * len);
}

/**
 * Pre-computes points along a path for smoother animation.
 * This reduces the number of getPointAtLength calls during animation.
 *
 * @param ref - React ref to the SVG path element
 * @param steps - Number of points to pre-compute (default 101 for 0-100%)
 */
export function usePrecomputedPath(
  ref: React.RefObject<SVGPathElement | null>,
  steps: number = 101
): (pct: number) => { x: number; y: number } {
  const pointsRef = useRef<Array<{ x: number; y: number }> | null>(null);
  const pathRef = useRef<SVGPathElement | null>(null);

  // Compute points when the path element is available
  const computePoints = useCallback(() => {
    const el = ref.current;
    if (!el || el === pathRef.current) return;

    pathRef.current = el;
    const len = getCachedPathLength(el);
    const points: Array<{ x: number; y: number }> = [];

    for (let i = 0; i < steps; i++) {
      const pct = i / (steps - 1);
      const point = el.getPointAtLength(pct * len);
      points.push({ x: point.x, y: point.y });
    }

    pointsRef.current = points;
  }, [ref, steps]);

  // Interpolate between pre-computed points
  const getPoint = useCallback(
    (pct: number): { x: number; y: number } => {
      // Compute points lazily on first access
      if (!pointsRef.current) {
        computePoints();
      }

      const points = pointsRef.current;
      if (!points || points.length === 0) {
        return { x: 0, y: 0 };
      }

      // Clamp percentage to 0-100
      const clampedPct = Math.max(0, Math.min(100, pct % 100));

      // Find the exact or interpolated point
      const index = (clampedPct / 100) * (points.length - 1);
      const lowerIndex = Math.floor(index);
      const upperIndex = Math.ceil(index);

      if (lowerIndex === upperIndex || upperIndex >= points.length) {
        return points[lowerIndex];
      }

      // Linear interpolation between two points
      const t = index - lowerIndex;
      const lower = points[lowerIndex];
      const upper = points[upperIndex];

      return {
        x: lower.x + (upper.x - lower.x) * t,
        y: lower.y + (upper.y - lower.y) * t,
      };
    },
    [computePoints]
  );

  return getPoint;
}

/**
 * Optimized spring config for diagram animations.
 * Uses higher precision values to reduce frame updates while maintaining smoothness.
 */
export const OPTIMIZED_SPRING_CONFIG = {
  // Duration in ms - keep original timing
  precision: 0.1, // Higher than 1 = fewer updates (was 1)
  easing: (t: number) => t, // Linear easing
};

/**
 * Pre-computed FADE values for opacity animation.
 * Avoids computing 1 - Math.abs((p % 100) - 50) / 50 on every frame.
 */
const FADE_LUT: number[] = [];
for (let i = 0; i <= 100; i++) {
  FADE_LUT[i] = 1 - Math.abs((i % 100) - 50) / 50;
}

/**
 * Optimized fade function using lookup table.
 * Returns opacity value (0-1) that fades in and out as percentage goes 0->50->100.
 */
export function fadeLUT(pct: number): number {
  const index = Math.round(pct % 100);
  return FADE_LUT[Math.max(0, Math.min(100, index))];
}

/**
 * The original fade function for comparison.
 */
export function fadeOriginal(pct: number): number {
  return 1 - Math.abs((pct % 100) - 50) / 50;
}

/**
 * Hook that provides animation control based on visibility and pause state.
 *
 * @param isVisible - Whether the diagram is currently visible
 * @param isPaused - Optional external pause control
 */
export function useAnimationControl(
  isVisible: boolean,
  isPaused: boolean = false
): {
  shouldAnimate: boolean;
  springConfig: { immediate: boolean };
} {
  const shouldAnimate = isVisible && !isPaused;

  return {
    shouldAnimate,
    springConfig: {
      // When not animating, skip spring calculations entirely
      immediate: !shouldAnimate,
    },
  };
}

/**
 * Hook to manage viewport-aware animation with reduced rendering when not visible.
 * Combines visibility detection with animation control.
 */
export function useViewportAnimation(
  isPaused: boolean = false
): {
  containerRef: React.RefObject<HTMLDivElement | null>;
  isVisible: boolean;
  shouldAnimate: boolean;
  springPause: boolean;
} {
  const [containerRef, isVisible] = useIsVisible();
  const shouldAnimate = isVisible && !isPaused;

  return {
    containerRef,
    isVisible,
    shouldAnimate,
    // react-spring's pause prop - true means paused
    springPause: !shouldAnimate,
  };
}
