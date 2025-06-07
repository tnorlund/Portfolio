import { useInView } from "react-intersection-observer";

interface UseOptimizedInViewOptions {
  threshold?: number | number[];
  triggerOnce?: boolean;
  rootMargin?: string;
  skip?: boolean;
}

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

export default useOptimizedInView;
