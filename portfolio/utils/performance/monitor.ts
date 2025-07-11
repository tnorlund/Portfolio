/**
 * Performance monitoring utilities for development
 */

export interface PerformanceMetrics {
  // Core Web Vitals
  lcp?: number; // Largest Contentful Paint
  fid?: number; // First Input Delay
  cls?: number; // Cumulative Layout Shift
  fcp?: number; // First Contentful Paint
  ttfb?: number; // Time to First Byte
  
  // Custom metrics
  jsHeapSize?: number;
  jsHeapSizeLimit?: number;
  usedJSHeapSize?: number;
  componentRenderTime?: Record<string, number[]>;
  apiCallDuration?: Record<string, number[]>;
  imageLoadTime?: Record<string, number>;
}

class PerformanceMonitor {
  private metrics: PerformanceMetrics = {};
  private observers: Map<string, PerformanceObserver> = new Map();
  private listeners: Set<(metrics: PerformanceMetrics) => void> = new Set();
  private memoryTrackingInterval: NodeJS.Timeout | null = null;

  constructor() {
    if (typeof window !== 'undefined') {
      this.initializeObservers();
      this.trackMemoryUsage();
    }
  }

  private initializeObservers() {
    // LCP Observer
    try {
      const lcpObserver = new PerformanceObserver((entryList) => {
        const entries = entryList.getEntries();
        const lastEntry = entries[entries.length - 1] as any;
        this.metrics.lcp = lastEntry.renderTime || lastEntry.loadTime;
        this.notifyListeners();
      });
      lcpObserver.observe({ type: 'largest-contentful-paint', buffered: true });
      this.observers.set('lcp', lcpObserver);
    } catch (e) {
      console.warn('LCP observer not supported');
    }

    // FID Observer
    try {
      const fidObserver = new PerformanceObserver((entryList) => {
        const entries = entryList.getEntries();
        entries.forEach((entry: any) => {
          if (entry.name === 'first-input') {
            this.metrics.fid = entry.processingStart - entry.startTime;
            this.notifyListeners();
          }
        });
      });
      fidObserver.observe({ type: 'first-input', buffered: true });
      this.observers.set('fid', fidObserver);
    } catch (e) {
      console.warn('FID observer not supported');
    }

    // CLS Observer
    try {
      let clsValue = 0;
      let clsEntries: any[] = [];
      const clsObserver = new PerformanceObserver((entryList) => {
        const entries = entryList.getEntries() as any[];
        entries.forEach(entry => {
          if (!entry.hadRecentInput) {
            clsEntries.push(entry);
            clsValue += entry.value;
          }
        });
        this.metrics.cls = clsValue;
        this.notifyListeners();
      });
      clsObserver.observe({ type: 'layout-shift', buffered: true });
      this.observers.set('cls', clsObserver);
    } catch (e) {
      console.warn('CLS observer not supported');
    }

    // Paint Timing
    try {
      const paintObserver = new PerformanceObserver((entryList) => {
        const entries = entryList.getEntries();
        entries.forEach((entry) => {
          if (entry.name === 'first-contentful-paint') {
            this.metrics.fcp = entry.startTime;
            this.notifyListeners();
          }
        });
      });
      paintObserver.observe({ type: 'paint', buffered: true });
      this.observers.set('paint', paintObserver);
    } catch (e) {
      console.warn('Paint observer not supported');
    }

    // Navigation Timing
    if (performance.getEntriesByType) {
      const navigationEntries = performance.getEntriesByType('navigation') as PerformanceNavigationTiming[];
      if (navigationEntries.length > 0) {
        const navEntry = navigationEntries[0];
        // Fix TTFB calculation - responseStart is relative to navigationStart, not requestStart
        this.metrics.ttfb = navEntry.responseStart - navEntry.fetchStart;
      }
    }
  }

  private trackMemoryUsage() {
    if ('memory' in performance) {
      this.memoryTrackingInterval = setInterval(() => {
        const memory = (performance as any).memory;
        this.metrics.jsHeapSize = memory.totalJSHeapSize;
        this.metrics.jsHeapSizeLimit = memory.jsHeapSizeLimit;
        this.metrics.usedJSHeapSize = memory.usedJSHeapSize;
        this.notifyListeners();
      }, 1000);
    }
  }

  public trackComponentRender(componentName: string, duration: number) {
    if (!this.metrics.componentRenderTime) {
      this.metrics.componentRenderTime = {};
    }
    if (!this.metrics.componentRenderTime[componentName]) {
      this.metrics.componentRenderTime[componentName] = [];
    }
    this.metrics.componentRenderTime[componentName].push(duration);
    this.notifyListeners();
  }

  public trackAPICall(endpoint: string, duration: number) {
    if (!this.metrics.apiCallDuration) {
      this.metrics.apiCallDuration = {};
    }
    if (!this.metrics.apiCallDuration[endpoint]) {
      this.metrics.apiCallDuration[endpoint] = [];
    }
    this.metrics.apiCallDuration[endpoint].push(duration);
    this.notifyListeners();
  }

  public trackImageLoad(imageSrc: string, duration: number) {
    if (!this.metrics.imageLoadTime) {
      this.metrics.imageLoadTime = {};
    }
    this.metrics.imageLoadTime[imageSrc] = duration;
    this.notifyListeners();
  }

  public getMetrics(): PerformanceMetrics {
    return { ...this.metrics };
  }

  public subscribe(callback: (metrics: PerformanceMetrics) => void) {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  private notifyListeners() {
    this.listeners.forEach(listener => listener(this.getMetrics()));
  }

  public reset() {
    this.metrics = {};
    this.notifyListeners();
  }

  public destroy() {
    this.observers.forEach(observer => observer.disconnect());
    this.observers.clear();
    this.listeners.clear();
    
    // Clear memory tracking interval to prevent memory leak
    if (this.memoryTrackingInterval) {
      clearInterval(this.memoryTrackingInterval);
      this.memoryTrackingInterval = null;
    }
  }
}

// Singleton instance
let performanceMonitor: PerformanceMonitor | null = null;

export function getPerformanceMonitor(): PerformanceMonitor {
  if (!performanceMonitor && typeof window !== 'undefined') {
    performanceMonitor = new PerformanceMonitor();
  }
  return performanceMonitor!;
}

// Performance measurement utilities
export function measureAsync<T>(
  name: string,
  fn: () => Promise<T>
): Promise<T> {
  const start = performance.now();
  return fn().finally(() => {
    const duration = performance.now() - start;
    console.log(`[Performance] ${name}: ${duration.toFixed(2)}ms`);
  });
}

export function measureSync<T>(
  name: string,
  fn: () => T
): T {
  const start = performance.now();
  try {
    return fn();
  } finally {
    const duration = performance.now() - start;
    console.log(`[Performance] ${name}: ${duration.toFixed(2)}ms`);
  }
}

// Web Vitals helpers
export function getCLS(): number | undefined {
  return getPerformanceMonitor().getMetrics().cls;
}

export function getLCP(): number | undefined {
  return getPerformanceMonitor().getMetrics().lcp;
}

export function getFID(): number | undefined {
  return getPerformanceMonitor().getMetrics().fid;
}

export function getFCP(): number | undefined {
  return getPerformanceMonitor().getMetrics().fcp;
}

export function getTTFB(): number | undefined {
  return getPerformanceMonitor().getMetrics().ttfb;
}