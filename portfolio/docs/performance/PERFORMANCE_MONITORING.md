# Performance Monitoring Tools

This document describes the performance monitoring tools available for development in the Portfolio project.

## Overview

The performance monitoring system provides real-time insights into:
- Core Web Vitals (LCP, FID, CLS, FCP, TTFB)
- JavaScript memory usage
- Component render times
- API call durations
- Image loading performance
- Bundle sizes

## Features

### 1. Development Performance Overlay

A real-time performance overlay appears in development mode showing:
- FPS counter
- Core Web Vitals metrics
- Memory usage with visual indicator
- Component render times
- API call performance

**Location**: Bottom-right corner (configurable)
**Toggle**: Click the header to minimize/expand

### 2. Performance Monitoring Hook

Use the `usePerformanceMonitor` hook in components to track performance:

```typescript
import { usePerformanceMonitor } from '../hooks/usePerformanceMonitor';

function MyComponent() {
  const { trackAPICall, trackImageLoad } = usePerformanceMonitor({
    componentName: 'MyComponent',
    trackRender: true, // Automatically track render times
  });

  // Track API calls
  const fetchData = async () => {
    const data = await trackAPICall('/api/data', () => 
      fetch('/api/data').then(r => r.json())
    );
    return data;
  };

  // Track image loads
  const handleImageLoad = (imageSrc: string, startTime: number) => {
    trackImageLoad(imageSrc, startTime);
  };

  return <div>...</div>;
}
```

### 3. Performance Logger

The logger automatically stores performance metrics in localStorage and generates reports:

```typescript
import { getPerformanceLogger } from '../utils/performance/logger';

// Get the logger instance
const logger = getPerformanceLogger();

// Generate a markdown report
const report = logger.generateReport();

// Download the report
logger.downloadReport();

// Clear logs
logger.clear();
```

### 4. Performance Testing Utilities

Run performance benchmarks and comparisons:

```typescript
import { PerformanceBenchmark } from '../utils/performance/testing';

const benchmark = new PerformanceBenchmark();

benchmark
  .add('Array map', () => {
    const arr = Array(1000).fill(0);
    arr.map(x => x * 2);
  })
  .add('Array forEach', () => {
    const arr = Array(1000).fill(0);
    arr.forEach(x => x * 2);
  });

// Run comparison with 'Array map' as baseline
await benchmark.compare('Array map');
```

### 5. Bundle Size Analysis

Monitor and analyze bundle sizes:

```bash
# Analyze bundles with webpack-bundle-analyzer
npm run analyze

# Generate bundle size report
npm run analyze:bundle
```

The report shows:
- Page bundle sizes (original and gzipped)
- Chunk bundle sizes
- Total bundle size
- Warnings for large bundles

## API Reference

### Performance Monitor

```typescript
interface PerformanceMetrics {
  lcp?: number;          // Largest Contentful Paint
  fid?: number;          // First Input Delay
  cls?: number;          // Cumulative Layout Shift
  fcp?: number;          // First Contentful Paint
  ttfb?: number;         // Time to First Byte
  jsHeapSize?: number;
  jsHeapSizeLimit?: number;
  usedJSHeapSize?: number;
  componentRenderTime?: Record<string, number[]>;
  apiCallDuration?: Record<string, number[]>;
  imageLoadTime?: Record<string, number>;
}
```

### Core Web Vitals Thresholds

| Metric | Good | Needs Improvement | Poor |
|--------|------|-------------------|------|
| LCP | < 2.5s | < 4.0s | ≥ 4.0s |
| FID | < 100ms | < 300ms | ≥ 300ms |
| CLS | < 0.1 | < 0.25 | ≥ 0.25 |
| FCP | < 1.8s | < 3.0s | ≥ 3.0s |
| TTFB | < 800ms | < 1800ms | ≥ 1800ms |

## Best Practices

1. **Use in Development Only**: Performance monitoring adds overhead. It's automatically disabled in production.

2. **Track Critical Paths**: Focus monitoring on components and API calls that impact user experience.

3. **Regular Analysis**: Run bundle analysis after adding dependencies or major features.

4. **Set Performance Budgets**: Use warnings in bundle analysis to enforce size limits.

5. **Monitor Trends**: Use the performance logger to track metrics over time.

## Configuration

### Environment Variables

- `NODE_ENV=development`: Enables performance monitoring
- `ANALYZE=true`: Enables webpack bundle analyzer

### Overlay Position

Configure the overlay position in `PerformanceOverlay`:

```typescript
<PerformanceOverlay position="top-right" />
```

Options: `top-left`, `top-right`, `bottom-left`, `bottom-right`

## Troubleshooting

### Performance Overlay Not Showing

1. Ensure you're in development mode
2. Check that `PerformanceOverlay` is included in `_app.tsx`
3. Verify browser supports Performance Observer API

### Missing Metrics

Some metrics require user interaction:
- FID: Click or interact with the page
- CLS: Wait for layout shifts to occur

### Bundle Analysis Fails

1. Run `npm run build` first
2. Ensure `.next` directory exists
3. Check that gzip is available in your system

## Future Enhancements

- [ ] Real User Monitoring (RUM) integration
- [ ] Performance budgets with CI integration
- [ ] Historical performance tracking
- [ ] Lighthouse CI integration
- [ ] Custom performance marks and measures
- [ ] Network request waterfall visualization