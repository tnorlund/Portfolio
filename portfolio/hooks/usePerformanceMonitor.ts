import { useEffect, useLayoutEffect, useState, useRef, useCallback } from 'react';
import { 
  getPerformanceMonitor, 
  PerformanceMetrics,
  measureSync 
} from '../utils/performance/monitor';

interface UsePerformanceMonitorOptions {
  componentName?: string;
  trackRender?: boolean;
}

export function usePerformanceMonitor(options: UsePerformanceMonitorOptions = {}) {
  const { componentName, trackRender = true } = options;
  const [metrics, setMetrics] = useState<PerformanceMetrics>({});
  const renderStartTime = useRef<number>(0);
  const renderCount = useRef<number>(0);

  useEffect(() => {
    const monitor = getPerformanceMonitor();
    const unsubscribe = monitor.subscribe(setMetrics);

    return () => {
      unsubscribe();
    };
  }, []);

  // Set initial render start time first (runs on mount)
  useLayoutEffect(() => {
    if (trackRender && componentName) {
      renderStartTime.current = performance.now();
    }
  }, [trackRender, componentName]); // Dependencies ensure proper initialization

  // Track component render time using useLayoutEffect for accurate timing
  useLayoutEffect(() => {
    if (trackRender && componentName && renderStartTime.current > 0) {
      // Mark render end - useLayoutEffect runs synchronously after DOM mutations
      const renderEndTime = performance.now();
      const renderDuration = renderEndTime - renderStartTime.current;
      
      getPerformanceMonitor().trackComponentRender(componentName, renderDuration);
      renderCount.current += 1;
      
      // Set start time for next render
      renderStartTime.current = renderEndTime;
    }
  }); // No dependencies - runs after every render to measure timing

  const trackAPICall = useCallback(async <T,>(
    endpoint: string,
    apiCall: () => Promise<T>
  ): Promise<T> => {
    const start = performance.now();
    try {
      const result = await apiCall();
      const duration = performance.now() - start;
      getPerformanceMonitor().trackAPICall(endpoint, duration);
      return result;
    } catch (error) {
      const duration = performance.now() - start;
      getPerformanceMonitor().trackAPICall(endpoint, duration);
      throw error;
    }
  }, []);

  const trackImageLoad = useCallback((imageSrc: string, startTime: number) => {
    const duration = performance.now() - startTime;
    getPerformanceMonitor().trackImageLoad(imageSrc, duration);
  }, []);

  const measureFunction = useCallback(<T,>(
    name: string,
    fn: () => T
  ): T => {
    return measureSync(name, fn);
  }, []);

  return {
    metrics,
    trackAPICall,
    trackImageLoad,
    measureFunction,
    renderCount: renderCount.current,
  };
}