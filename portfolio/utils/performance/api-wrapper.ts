import { getPerformanceMonitor } from './monitor';

type APIFunction<T extends any[], R> = (...args: T) => Promise<R>;

/**
 * Wraps an API function to automatically track its performance
 */
export function withPerformanceTracking<T extends any[], R>(
  fn: APIFunction<T, R>,
  endpoint: string
): APIFunction<T, R> {
  return async (...args: T): Promise<R> => {
    const start = performance.now();
    const monitor = getPerformanceMonitor();
    
    try {
      const result = await fn(...args);
      const duration = performance.now() - start;
      monitor?.trackAPICall(endpoint, duration);
      
      if (process.env.NODE_ENV === 'development') {
        console.log(`[API Performance] ${endpoint}: ${duration.toFixed(2)}ms`);
      }
      
      return result;
    } catch (error) {
      const duration = performance.now() - start;
      monitor?.trackAPICall(endpoint, duration);
      
      if (process.env.NODE_ENV === 'development') {
        console.error(`[API Performance] ${endpoint}: ${duration.toFixed(2)}ms (failed)`);
      }
      
      throw error;
    }
  };
}

/**
 * Wraps all methods of an API object to track performance
 */
export function withPerformanceTrackingForAPI<T extends Record<string, any>>(
  api: T,
  prefix = ''
): T {
  const wrappedAPI = {} as T;
  
  Object.entries(api).forEach(([key, value]) => {
    if (typeof value === 'function') {
      const endpoint = prefix ? `${prefix}.${key}` : key;
      (wrappedAPI as any)[key] = withPerformanceTracking(value, endpoint);
    } else {
      (wrappedAPI as any)[key] = value;
    }
  });
  
  return wrappedAPI;
}