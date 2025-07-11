import { test, expect } from '@playwright/test';

// Helper to get performance metrics
async function getMetrics(page: any) {
  return await page.evaluate(() => {
    const getMetric = (name: string) => {
      const entry = performance.getEntriesByName(name)[0] || 
                    performance.getEntriesByType(name)[0];
      return entry ? Math.round(entry.startTime || (entry as any).renderTime || (entry as any).loadTime) : null;
    };

    // Get CLS
    let clsValue = 0;
    const clsEntries = performance.getEntriesByType('layout-shift') as any[];
    clsEntries.forEach(entry => {
      if (!entry.hadRecentInput) {
        clsValue += entry.value;
      }
    });

    return {
      // Navigation timing
      domContentLoaded: Math.round(performance.timing.domContentLoadedEventEnd - performance.timing.navigationStart),
      loadComplete: Math.round(performance.timing.loadEventEnd - performance.timing.navigationStart),
      
      // Core Web Vitals
      fcp: getMetric('first-contentful-paint'),
      lcp: performance.getEntriesByType('largest-contentful-paint').length > 0 
        ? Math.round((performance.getEntriesByType('largest-contentful-paint').pop() as any).renderTime || 
                     (performance.getEntriesByType('largest-contentful-paint').pop() as any).loadTime)
        : null,
      cls: Math.round(clsValue * 1000) / 1000,
      ttfb: Math.round(performance.timing.responseStart - performance.timing.requestStart),
      
      // Memory (if available)
      memory: (performance as any).memory ? {
        usedJSHeapSize: Math.round((performance as any).memory.usedJSHeapSize / 1048576),
        totalJSHeapSize: Math.round((performance as any).memory.totalJSHeapSize / 1048576),
        jsHeapSizeLimit: Math.round((performance as any).memory.jsHeapSizeLimit / 1048576),
      } : null,
    };
  });
}

test.describe('Homepage Performance', () => {
  test('should meet Core Web Vitals thresholds', async ({ page }) => {
    // Start collecting performance entries
    await page.goto('/', { waitUntil: 'networkidle' });
    
    // Wait a bit for all metrics to be collected
    await page.waitForTimeout(2000);
    
    const metrics = await getMetrics(page);
    console.log('Performance Metrics:', metrics);
    
    // Assert Core Web Vitals are within acceptable ranges
    // FCP (First Contentful Paint) - Good: <1.8s, Needs improvement: <3s
    expect(metrics.fcp).toBeLessThan(1800);
    
    // LCP (Largest Contentful Paint) - Good: <2.5s, Needs improvement: <4s
    if (metrics.lcp) {
      expect(metrics.lcp).toBeLessThan(2500);
    }
    
    // CLS (Cumulative Layout Shift) - Good: <0.1, Needs improvement: <0.25
    expect(metrics.cls).toBeLessThan(0.1);
    
    // TTFB (Time to First Byte) - Good: <800ms, Needs improvement: <1800ms
    expect(metrics.ttfb).toBeLessThan(800);
    
    // Total page load time should be reasonable
    expect(metrics.loadComplete).toBeLessThan(5000);
  });

  test('should not have memory leaks on navigation', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    
    // Get initial memory
    const initialMetrics = await getMetrics(page);
    if (!initialMetrics.memory) {
      test.skip();
      return;
    }
    
    const initialMemory = initialMetrics.memory.usedJSHeapSize;
    
    // Navigate around the site
    for (let i = 0; i < 5; i++) {
      await page.goto('/');
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(500);
    }
    
    // Force garbage collection if possible
    await page.evaluate(() => {
      if ((window as any).gc) {
        (window as any).gc();
      }
    });
    
    await page.waitForTimeout(1000);
    
    // Check final memory
    const finalMetrics = await getMetrics(page);
    const finalMemory = finalMetrics.memory!.usedJSHeapSize;
    
    // Memory should not grow more than 50% after multiple navigations
    const memoryGrowth = (finalMemory - initialMemory) / initialMemory;
    expect(memoryGrowth).toBeLessThan(0.5);
  });

  test('should load images efficiently', async ({ page }) => {
    const imageLoadTimes: number[] = [];
    
    // Track image load times
    page.on('response', async (response) => {
      if (response.request().resourceType() === 'image') {
        const timing = await response.request().timing();
        if (timing) {
          imageLoadTimes.push(timing.responseEnd);
        }
      }
    });
    
    await page.goto('/', { waitUntil: 'networkidle' });
    
    // Check that images load reasonably fast
    const avgImageLoadTime = imageLoadTimes.length > 0 
      ? imageLoadTimes.reduce((a, b) => a + b, 0) / imageLoadTimes.length 
      : 0;
    
    console.log(`Loaded ${imageLoadTimes.length} images, avg time: ${avgImageLoadTime}ms`);
    
    // Average image load time should be under 500ms
    expect(avgImageLoadTime).toBeLessThan(500);
  });

  test('should have efficient JavaScript execution', async ({ page }) => {
    await page.goto('/');
    
    // Measure JavaScript execution time
    const jsMetrics = await page.evaluate(() => {
      const entries = performance.getEntriesByType('measure');
      const scriptEntries = performance.getEntriesByType('resource')
        .filter((entry: any) => entry.name.includes('.js'));
      
      let totalScriptTime = 0;
      scriptEntries.forEach((entry: any) => {
        totalScriptTime += entry.duration || 0;
      });
      
      return {
        scriptCount: scriptEntries.length,
        totalScriptTime: Math.round(totalScriptTime),
      };
    });
    
    console.log('JavaScript metrics:', jsMetrics);
    
    // Total script execution should be under 3 seconds
    expect(jsMetrics.totalScriptTime).toBeLessThan(3000);
  });

  test('should handle slow network gracefully', async ({ page, browser }) => {
    // Create a new context with slow network
    const context = await browser.newContext({
      // Simulate slow 3G
      offline: false,
    });
    
    const slowPage = await context.newPage();
    
    // Emulate slow network
    await slowPage.route('**/*', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 100)); // Add 100ms delay
      await route.continue();
    });
    
    const startTime = Date.now();
    await slowPage.goto('/', { waitUntil: 'domcontentloaded' });
    const loadTime = Date.now() - startTime;
    
    // Even on slow network, initial content should load within 10 seconds
    expect(loadTime).toBeLessThan(10000);
    
    // Check that content is visible
    await expect(slowPage.locator('h1')).toBeVisible();
    
    await context.close();
  });
});