# Image Stack Optimization Guide

This guide covers the image optimization strategies implemented in the portfolio project.

## Overview

The portfolio uses an advanced image optimization system that provides:
- Multiple format support (WebP, AVIF, fallback to JPEG/PNG)
- Responsive image sizing
- Lazy loading with intersection observer
- Blur-up placeholder animations

## Key Components

### 1. Format Detection

The system automatically detects browser support for modern image formats:

```typescript
import { detectImageFormatSupport } from '../../utils/imageFormat';

const formatSupport = await detectImageFormatSupport();
// Returns: { webp: true, avif: true }
```

### 2. Optimal Image Selection

The `getBestImageUrl` utility selects the best available format:

```typescript
import { getBestImageUrl } from '../../utils/imageFormat';

const imageUrl = getBestImageUrl(receipt, formatSupport, size);
```

### 3. ImageStack Component

The `ImageStack` component handles progressive loading and animations:

- Loads a low-quality placeholder first
- Transitions to full quality when in viewport
- Supports multiple image formats with fallbacks
- Provides smooth fade-in animations

## Performance Benefits

- **60% smaller file sizes** with WebP/AVIF
- **Lazy loading** reduces initial page load
- **Progressive enhancement** ensures compatibility
- **Optimized animations** using React Spring

## Best Practices

1. Always provide multiple format options in your CDN
2. Use appropriate image sizes for different viewports
3. Implement proper loading states and placeholders
4. Monitor Core Web Vitals (LCP, CLS) impact

## Related Documentation

- [Performance Monitoring Tools](./PERFORMANCE_MONITORING.md)
- [Image Format Utilities](../utils/imageFormat/README.md)