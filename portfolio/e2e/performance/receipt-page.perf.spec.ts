import { test, expect } from '@playwright/test';

// Helper to measure component render performance
async function measureComponentPerformance(page: any, componentSelector: string) {
  return await page.evaluate((selector: string) => {
    return new Promise((resolve) => {
      const observer = new PerformanceObserver((list) => {
        const entries = list.getEntries();
        const componentEntry = entries.find(entry => 
          entry.name.includes('component') || entry.name.includes(selector)
        );
        if (componentEntry) {
          resolve({
            duration: Math.round(componentEntry.duration),
            startTime: Math.round(componentEntry.startTime),
          });
        }
      });
      
      observer.observe({ entryTypes: ['measure', 'navigation'] });
      
      // Fallback after 5 seconds
      setTimeout(() => resolve({ duration: 0, startTime: 0 }), 5000);
    });
  }, componentSelector);
}

test.describe('Receipt Page Performance', () => {
  test.beforeEach(async ({ page }) => {
    // Enable performance observer
    await page.addInitScript(() => {
      (window as any).__performanceMarks = [];
      const originalMark = performance.mark.bind(performance);
      performance.mark = function(markName: string, markOptions?: PerformanceMarkOptions) {
        (window as any).__performanceMarks.push({ type: 'mark', args: [markName, markOptions], time: performance.now() });
        return originalMark(markName, markOptions);
      };
    });
  });

  test('should load receipt images progressively', async ({ page }) => {
    const imageLoadTimes: { src: string; loadTime: number }[] = [];
    
    // Track image loads
    page.on('response', async (response) => {
      if (response.request().resourceType() === 'image' && response.status() === 200) {
        const timing = await response.request().timing();
        if (timing) {
          imageLoadTimes.push({
            src: response.url(),
            loadTime: timing.responseEnd - timing.requestStart,
          });
        }
      }
    });
    
    await page.goto('/receipt');
    
    // Wait for initial images to load
    await page.waitForSelector('img', { state: 'visible' });
    await page.waitForTimeout(2000);
    
    // Check progressive loading - first 6 images should load quickly
    const firstBatchImages = imageLoadTimes.slice(0, 6);
    const laterImages = imageLoadTimes.slice(6);
    
    if (firstBatchImages.length > 0) {
      const avgFirstBatch = firstBatchImages.reduce((sum, img) => sum + img.loadTime, 0) / firstBatchImages.length;
      console.log(`First batch (${firstBatchImages.length} images) avg load time: ${avgFirstBatch}ms`);
      
      // First batch should load quickly
      expect(avgFirstBatch).toBeLessThan(300);
    }
    
    // Verify progressive loading pattern
    console.log(`Total images loaded: ${imageLoadTimes.length}`);
    expect(imageLoadTimes.length).toBeGreaterThanOrEqual(6);
  });

  test('should handle scroll performance efficiently', async ({ page }) => {
    await page.goto('/receipt');
    await page.waitForLoadState('networkidle');
    
    // Measure scroll performance
    const scrollMetrics = await page.evaluate(async () => {
      const metrics = {
        scrollEvents: 0,
        layoutShifts: 0,
        maxScrollJank: 0,
      };
      
      // Track layout shifts during scroll
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.entryType === 'layout-shift' && !(entry as any).hadRecentInput) {
            metrics.layoutShifts += (entry as any).value;
          }
        }
      });
      observer.observe({ entryTypes: ['layout-shift'] });
      
      // Perform smooth scroll
      const startTime = performance.now();
      let lastTime = startTime;
      
      for (let i = 0; i < 10; i++) {
        window.scrollBy({ top: 200, behavior: 'smooth' });
        await new Promise(resolve => setTimeout(resolve, 100));
        
        const currentTime = performance.now();
        const jank = currentTime - lastTime - 100; // Expected 100ms delay
        metrics.maxScrollJank = Math.max(metrics.maxScrollJank, jank);
        lastTime = currentTime;
        metrics.scrollEvents++;
      }
      
      observer.disconnect();
      return metrics;
    });
    
    console.log('Scroll performance metrics:', scrollMetrics);
    
    // Layout shifts during scroll should be minimal
    expect(scrollMetrics.layoutShifts).toBeLessThan(0.05);
    
    // Scroll jank should be minimal (under 50ms)
    expect(scrollMetrics.maxScrollJank).toBeLessThan(50);
  });

  test('should optimize format detection caching', async ({ page }) => {
    // First visit - format detection runs
    await page.goto('/receipt');
    
    const firstVisitMetrics = await page.evaluate(() => {
      const formatDetectionTime = performance.getEntriesByName('format-detection')[0];
      const hasCache = localStorage.getItem('imageFormatSupport') !== null;
      return {
        detectionTime: formatDetectionTime ? formatDetectionTime.duration : 0,
        hasCache,
      };
    });
    
    // Reload page
    await page.reload();
    
    const secondVisitMetrics = await page.evaluate(() => {
      const formatDetectionTime = performance.getEntriesByName('format-detection')[0];
      const hasCache = localStorage.getItem('imageFormatSupport') !== null;
      const cacheData = localStorage.getItem('imageFormatSupport');
      return {
        detectionTime: formatDetectionTime ? formatDetectionTime.duration : 0,
        hasCache,
        cacheValid: cacheData ? JSON.parse(cacheData).expiry > Date.now() : false,
      };
    });
    
    console.log('Format detection metrics:', { firstVisitMetrics, secondVisitMetrics });
    
    // Cache should be present after first visit
    expect(secondVisitMetrics.hasCache).toBe(true);
    expect(secondVisitMetrics.cacheValid).toBe(true);
  });

  test('should handle API call performance', async ({ page }) => {
    const apiCalls: { url: string; duration: number }[] = [];
    
    // Intercept API calls
    page.on('response', async (response) => {
      if (response.url().includes('api.tylernorlund.com')) {
        const timing = await response.request().timing();
        if (timing) {
          apiCalls.push({
            url: response.url(),
            duration: timing.responseEnd - timing.requestStart,
          });
        }
      }
    });
    
    await page.goto('/receipt');
    await page.waitForLoadState('networkidle');
    
    console.log('API calls:', apiCalls);
    
    // All API calls should complete within reasonable time
    apiCalls.forEach(call => {
      expect(call.duration).toBeLessThan(2000); // 2 seconds max
    });
    
    // Average API response time
    if (apiCalls.length > 0) {
      const avgApiTime = apiCalls.reduce((sum, call) => sum + call.duration, 0) / apiCalls.length;
      console.log(`Average API response time: ${avgApiTime}ms`);
      expect(avgApiTime).toBeLessThan(1000); // 1 second average
    }
  });

  test('should maintain 60fps during animations', async ({ page }) => {
    await page.goto('/receipt');
    await page.waitForSelector('[style*="transform"]', { state: 'visible' });
    
    // Measure animation performance
    const animationMetrics = await page.evaluate(async () => {
      const metrics = {
        fps: [] as number[],
        droppedFrames: 0,
        animationDuration: 0,
      };
      
      let frameCount = 0;
      let lastTime = performance.now();
      const startTime = lastTime;
      
      // Monitor frames for 2 seconds during animations
      return new Promise((resolve) => {
        const measureFrame = () => {
          const currentTime = performance.now();
          const deltaTime = currentTime - lastTime;
          
          if (deltaTime > 0) {
            const currentFPS = 1000 / deltaTime;
            metrics.fps.push(currentFPS);
            
            // Count dropped frames (less than 50fps)
            if (currentFPS < 50) {
              metrics.droppedFrames++;
            }
          }
          
          lastTime = currentTime;
          frameCount++;
          
          if (currentTime - startTime < 2000) {
            requestAnimationFrame(measureFrame);
          } else {
            metrics.animationDuration = currentTime - startTime;
            resolve(metrics);
          }
        };
        
        requestAnimationFrame(measureFrame);
      });
    }) as any;
    
    const avgFPS = animationMetrics.fps.reduce((a: number, b: number) => a + b, 0) / animationMetrics.fps.length;
    const droppedFrameRate = animationMetrics.droppedFrames / animationMetrics.fps.length;
    
    console.log(`Animation performance: ${avgFPS.toFixed(1)} FPS average`);
    console.log(`Dropped frames: ${(droppedFrameRate * 100).toFixed(1)}%`);
    
    // Should maintain close to 60fps
    expect(avgFPS).toBeGreaterThan(50);
    
    // Less than 10% dropped frames
    expect(droppedFrameRate).toBeLessThan(0.1);
  });
});