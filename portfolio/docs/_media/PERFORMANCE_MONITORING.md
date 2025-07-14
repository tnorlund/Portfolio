# Performance Monitoring Tools

This document describes the performance monitoring utilities available in the portfolio project.

## Overview

The portfolio includes comprehensive performance monitoring tools to track and optimize application performance.

## Available Tools

### 1. Performance Monitor

The performance monitor tracks Core Web Vitals and custom metrics:

```typescript
import { getPerformanceMonitor } from '../../utils/performance/monitor';

const monitor = getPerformanceMonitor();

// Track Core Web Vitals
monitor.getLCP(metric => console.log('LCP:', metric));
monitor.getFID(metric => console.log('FID:', metric));
monitor.getCLS(metric => console.log('CLS:', metric));
```

### 2. Performance Logger

Log performance events and metrics:

```typescript
import { getPerformanceLogger } from '../../utils/performance/logger';

const logger = getPerformanceLogger();
logger.log('api-call', {
  duration: 250,
  endpoint: '/api/receipts',
  status: 'success'
});
```

### 3. API Performance Wrapper

Automatically track API call performance:

```typescript
import { withPerformanceTracking } from '../../utils/performance/api-wrapper';

const trackedFetch = withPerformanceTracking(fetch, 'receipts-api');
```

### 4. Performance Testing

Run performance benchmarks:

```typescript
import { runPerformanceTest } from '../../utils/performance/testing';

const results = await runPerformanceTest({
  name: 'Receipt Rendering',
  fn: () => renderReceipt(data),
  iterations: 100
});
```

## Monitoring Strategy

1. **Real User Monitoring (RUM)**
   - Track actual user experiences
   - Monitor Core Web Vitals
   - Identify performance bottlenecks

2. **Synthetic Testing**
   - Regular performance benchmarks
   - Regression detection
   - CI/CD integration

3. **Custom Metrics**
   - API response times
   - Component render performance
   - Resource loading times

## Performance Budgets

We enforce the following performance budgets:

- **LCP**: < 2.5s
- **FID**: < 100ms
- **CLS**: < 0.1
- **Total Bundle Size**: < 250KB (gzipped)

## Integration with CI/CD

Performance tests run automatically in CI:

```bash
npm run test:perf
npm run lighthouse
```

## Related Documentation

- [Image Stack Optimization](./image-stack-optimization.md)
- [Performance Monitor API](../utils/performance/monitor/README.md)
- [Performance Logger API](../utils/performance/logger/README.md)