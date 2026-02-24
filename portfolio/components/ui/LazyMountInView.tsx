import React, { ReactNode } from "react";
import { useInView } from "react-intersection-observer";

export interface LazyMountInViewProps {
  /** Content to mount when the wrapper enters the viewport. */
  children: ReactNode;
  /**
   * Placeholder shown until the wrapper is near viewport.
   * Use a fixed-height shell to avoid layout shift.
   */
  placeholder?: ReactNode;
  /**
   * Minimum height for the placeholder container (e.g. "400px") to prevent layout shift.
   */
  minHeight?: string | number;
  /** How much of the element must be visible (0–1). Default 0.1. */
  threshold?: number;
  /** Margin around viewport (e.g. "200px") so mount happens before fully visible. */
  rootMargin?: string;
  /** If true, children stay mounted after first entry. Default true. */
  triggerOnce?: boolean;
  className?: string;
}

/**
 * Lazy-mounts children only when the wrapper is near the viewport.
 * Renders a lightweight placeholder until then to improve initial responsiveness.
 */
const LazyMountInView = ({
  children,
  placeholder = null,
  minHeight,
  threshold = 0.1,
  rootMargin = "200px",
  triggerOnce = true,
  className,
}: LazyMountInViewProps) => {
  const { ref, inView } = useInView({
    threshold,
    rootMargin,
    triggerOnce,
    skip: false,
    fallbackInView: false,
  });

  const containerStyle: React.CSSProperties = minHeight
    ? { minHeight: typeof minHeight === "number" ? `${minHeight}px` : minHeight }
    : {};

  return (
    <div ref={ref} className={className} style={containerStyle}>
      {inView ? children : placeholder}
    </div>
  );
};

export default LazyMountInView;
