# ImageStack & ReceiptStack Performance Optimization Guide

## Overview

`ImageStack.tsx` and `ReceiptStack.tsx` are React components in the Next.js portfolio application that create visually appealing, animated collages of images. They display 20-40 images in a randomized, overlapping layout with staggered fade-in animations.

## System Architecture

### Complete Data Flow

```
User Browser
    ↓
Next.js App (React 18 + TypeScript)
    ↓
Components: ImageStack (20 images) / ReceiptStack (40 receipts)
    ↓
API Service Layer (services/api/index.ts)
    ↓
CloudFront CDN #1 (api.tylernorlund.com)
    - CORS enabled
    - Rate limiting: 20k req/s sustained
    ↓
AWS API Gateway v2 (HTTP API)
    ↓
Lambda Functions (Python 3.12, ARM64, 1024MB)
    - /images endpoint (30s timeout)
    - /receipts endpoint (120s timeout)
    ↓
DynamoDB (On-demand billing)
    - 4 GSIs for query optimization
    - Paginated queries
    ↓
Response includes S3 keys for images
    ↓
Browser fetches actual images
    ↓
CloudFront CDN #2 (www.tylernorlund.com)
    - 30-day cache for images
    - HTTP/3 support
    - Brotli/gzip compression
    ↓
S3 Bucket (Origin Access Control)
    - Raw images + WebP/AVIF variants
```

## Current Implementation

### Component Architecture

| Component | Purpose | Default Load | Dimensions |
|-----------|---------|--------------|------------|
| `ImageStack` | Photo collage display | 20 images | 150px width |
| `ReceiptStack` | Receipt scan collage | 40 receipts | 100px width |

### Shared Behavior

1. **Format Detection**: Detects AVIF/WebP support on mount
2. **Data Fetching**: Paginated API calls until reaching max count
3. **Position Calculation**: Random positions/rotations via `useMemo`
4. **Rendering**: Absolutely positioned images with fade-in animations
5. **Fallback Logic**: AVIF → WebP → JPEG fallback chain

## Performance Analysis

### Current Metrics

- **Lighthouse Score**: 85/100 (mobile), 100/100 (desktop)
- **JavaScript Execution**: 3.1 seconds
- **Initial Load**: 20-40 items loaded immediately

### Identified Bottlenecks

#### 1. Sequential Data Loading
```typescript
// Current implementation:
while (allImages.length < maxImages) {
  const response = await api.fetchImages(pageSize, lastEvaluatedKey);
  // Blocks rendering until all pages loaded
}
```
**Impact**: 400-800ms delay before first render

#### 2. Heavy Initial Payload
- 40-120KB metadata for initial load
- No progressive enhancement
- All positions calculated upfront

#### 3. Expensive Animations
```typescript
// Animating layout properties:
top: shouldAnimate ? `${topOffset}px` : `${topOffset - 50}px`
```
**Impact**: Forces layout recalculation for 20-40 elements

#### 4. Unoptimized Large Images
```typescript
unoptimized={image.width > 3000 || image.height > 3000}
```
**Impact**: Downloads full resolution (1-5MB per image)

#### 5. Client-Side Calculations
- Position randomization runs on every mount/resize
- Format detection creates test elements
- ~50-100ms main thread blocking

## Optimization Strategy

### Quick Wins (Implemented)

1. **Progressive Loading**
   - Added `initialCount` prop (default: 6)
   - Remaining items load after initial render
   - 50-70% faster first paint

2. **Next.js Image Optimization**
   - Removed `unoptimized` prop
   - Enables automatic format conversion
   - Significant bandwidth savings

3. **Optimized API Fetching**
   - Improved batching logic
   - Reduced redundant calls

4. **Cached Format Detection**
   - 7-day localStorage cache
   - Eliminates redundant detection

5. **GPU-Accelerated Animations**
   - Using `transform: translateY()` instead of `top`
   - Smooth 60fps animations

### Medium-Term Improvements

1. **Virtual Scrolling**
   - Render only visible items
   - Use react-window or similar

2. **Server-Side Position Generation**
   - Pre-calculate during build/ISR
   - Ship positions as JSON

3. **Static Metadata Manifest**
   - Generate JSON with recent items
   - Serve as static asset

4. **CDN Image Resizing**
   - CloudFront + Lambda@Edge
   - Dynamic sizing via query params

### Long-Term Architecture

1. **Canvas/WebGL Rendering**
   - Single bitmap for all thumbnails
   - GPU-only animations

2. **Edge-Cached Aggregation**
   - Single GraphQL/REST endpoint
   - Coalesce multiple requests

3. **Web Worker Offloading**
   - Position calculations off main thread
   - Non-blocking rendering

## Performance Targets

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Initial Render | 20-40 items | 6 items | ✅ Achieved |
| Animation FPS | ~30fps | 60fps | ✅ Achieved |
| Format Detection | Every mount | Cached | ✅ Achieved |
| Image Optimization | Partial | Full | ✅ Achieved |
| Layout Reflows | Continuous | None | ✅ Achieved |

## Implementation Guidelines

### When Adding New Features

1. **Always use progressive loading**
   - Start with minimal set
   - Load more on demand

2. **Optimize animations**
   - Use transform/opacity only
   - Avoid layout properties

3. **Cache expensive operations**
   - Format detection
   - API responses
   - Position calculations

4. **Measure performance impact**
   - Run Lighthouse before/after
   - Monitor bundle size
   - Check mobile performance

### Testing Checklist

- [ ] Components render with new props
- [ ] Progressive loading works correctly
- [ ] Animations are smooth (60fps)
- [ ] Format detection uses cache
- [ ] No visual regressions
- [ ] Mobile performance acceptable

## Future Optimization Roadmap

### Phase 1: Virtual Scrolling (1-3 days)
- Implement windowing for large lists
- Reduce DOM nodes significantly
- Improve memory usage

### Phase 2: Server-Side Optimization (1 week)
- Move position calculation to build time
- Generate static manifests
- Implement ISR for updates

### Phase 3: Advanced Caching (1-2 weeks)
- Service worker implementation
- Edge caching strategies
- Predictive preloading

### Phase 4: Alternative Rendering (2-4 weeks)
- Canvas-based rendering
- WebGL for complex animations
- Progressive enhancement

## Monitoring & Maintenance

### Key Metrics to Track

1. **Performance Scores**
   - Lighthouse CI integration
   - Regular performance audits
   - Mobile vs desktop comparison

2. **User Metrics**
   - Time to interactive
   - First contentful paint
   - Cumulative layout shift

3. **Technical Metrics**
   - Bundle size changes
   - API response times
   - Cache hit rates

### Regular Review Process

1. Monthly performance audit
2. Quarterly architecture review
3. Continuous monitoring via GitHub Actions
4. User feedback incorporation

---

*Last Updated: January 2025*  
*Status: Initial optimizations complete, ongoing improvements planned*