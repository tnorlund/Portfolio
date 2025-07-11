import React, { useEffect } from 'react';
import { getPerformanceMonitor } from '../../utils/performance/monitor';
import { getPerformanceLogger } from '../../utils/performance/logger';

interface PerformanceProviderProps {
  children: React.ReactNode;
  enabled?: boolean;
}

export const PerformanceProvider: React.FC<PerformanceProviderProps> = ({
  children,
  enabled = process.env.NODE_ENV === 'development',
}) => {
  useEffect(() => {
    if (!enabled) return;

    const monitor = getPerformanceMonitor();
    const logger = getPerformanceLogger();

    // Subscribe to metrics and log them (only if logger is available)
    const unsubscribe = monitor.subscribe((metrics) => {
      if (logger) {
        logger.log(metrics);
      }
    });

    // Log navigation timing on initial load
    if (performance.timing) {
      const navigationTime = performance.timing.loadEventEnd - performance.timing.navigationStart;
      console.log(`[Performance] Page load time: ${navigationTime}ms`);
    }

    // Set up visibility change tracking
    const handleVisibilityChange = () => {
      if (document.hidden) {
        console.log('[Performance] Page hidden - pausing monitoring');
      } else {
        console.log('[Performance] Page visible - resuming monitoring');
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    // Cleanup
    return () => {
      unsubscribe();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [enabled]);

  return <>{children}</>;
};