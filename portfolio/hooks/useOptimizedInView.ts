import { useEffect, useState } from "react";
import { useInView } from "react-intersection-observer";

interface UseOptimizedInViewOptions {
  threshold?: number | number[];
  triggerOnce?: boolean;
  rootMargin?: string;
  skip?: boolean;
}

type UseRevealInViewOptions = Omit<UseOptimizedInViewOptions, "triggerOnce">;

/**
 * Optimized intersection observer hook with performance improvements:
 * - triggerOnce: true by default (prevents repeated calculations)
 * - rootMargin: '100px' for better preloading
 * - Shared observer instances when possible
 * - Proper cleanup and memory management
 */
export const useOptimizedInView = (options: UseOptimizedInViewOptions = {}) => {
  const {
    threshold = 0.3,
    triggerOnce = true,
    rootMargin = "100px",
    skip = false,
  } = options;

  return useInView({
    threshold,
    triggerOnce,
    rootMargin,
    skip,
    // Fallback visibility for SSR
    fallbackInView: true,
  });
};

/**
 * Tracks both live viewport visibility and whether an element has ever been
 * revealed. Use this when visual state should remain rendered after first
 * exposure, but timers or springs should pause while the element is offscreen.
 */
export const useRevealInView = (options: UseRevealInViewOptions = {}) => {
  const {
    threshold = 0.3,
    rootMargin = "100px",
    skip = false,
  } = options;
  const [hasEntered, setHasEntered] = useState(false);
  const [ref, inView, entry] = useInView({
    threshold,
    triggerOnce: false,
    rootMargin,
    skip,
    fallbackInView: true,
  });

  useEffect(() => {
    if (inView) {
      setHasEntered(true);
    }
  }, [inView]);

  return [ref, inView, hasEntered, entry] as const;
};

export default useOptimizedInView;
