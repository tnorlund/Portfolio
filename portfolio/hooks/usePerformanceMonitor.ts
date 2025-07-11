import { useEffect, useState, useRef, useCallback } from 'react';
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

  // Mark render start BEFORE useEffect
  if (trackRender && componentName) {
    renderStartTime.current = performance.now();
  }

  // Track component render time
  useEffect(() => {
    if (trackRender && componentName && renderStartTime.current > 0) {
      const renderDuration = performance.now() - renderStartTime.current;
      getPerformanceMonitor().trackComponentRender(componentName, renderDuration);
      renderCount.current += 1;
    }
  }, [trackRender, componentName]); // Add dependencies to fix missing dependency array

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