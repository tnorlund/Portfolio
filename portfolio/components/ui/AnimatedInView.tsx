import useOptimizedInView from "../../hooks/useOptimizedInView";
import { useSpring, animated } from "@react-spring/web";
import { ReactNode, useEffect, useState } from "react";

interface AnimatedInViewProps {
  children: ReactNode;
  /**
   * Optional React node to replace the children once the
   * component comes into view.
   */
  replacement?: ReactNode;
  /**
   * Delay in milliseconds before rendering the replacement once the
   * wrapper is visible. Defaults to `0`, meaning immediate replacement.
   */
  replaceAfterMs?: number;
  className?: string;
}

/**
 * Wraps content in a div that fades in when it enters the viewport.
 * Optionally replaces the children with another component when visible.
 */
const AnimatedInView = ({
  children,
  replacement,
  replaceAfterMs = 0,
  className,
}: AnimatedInViewProps) => {
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });
  const [style, api] = useSpring(() => ({
    opacity: 0,
    config: { tension: 120, friction: 14 },
  }));
  const [replaced, setReplaced] = useState(false);

  // Reset state when component goes out of view
  useEffect(() => {
    if (!inView) {
      // Reset to initial state when out of view
      setReplaced(false);
      api.set({ opacity: 0 });
    }
  }, [inView, api]);

  // Initial fade in when component comes into view
  useEffect(() => {
    if (inView) {
      api.start({ opacity: 1 });
    }
  }, [inView, api]);

  // Start replacement sequence with smooth transition
  useEffect(() => {
    if (inView && replacement && !replaced) {
      const timer = setTimeout(() => {
        // Fade out current content
        api.start({
          opacity: 0,
          config: { duration: 300 },
          onRest: () => {
            // Once fade out is complete, switch content
            setReplaced(true);
            // Give the new content a moment to render, then fade in
            setTimeout(() => {
              api.start({
                opacity: 1,
                config: { duration: 300 },
              });
            }, 50);
          },
        });
      }, replaceAfterMs);
      return () => clearTimeout(timer);
    }
  }, [inView, replacement, replaceAfterMs, replaced, api]);

  const content = replaced && replacement ? replacement : children;

  return (
    <animated.div ref={ref} style={style} className={className}>
      {content}
    </animated.div>
  );
};

export default AnimatedInView;
