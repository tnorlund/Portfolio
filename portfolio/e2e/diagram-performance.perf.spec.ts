import { test, expect, type Page } from "@playwright/test";

/**
 * Performance tests for diagram components on the /receipt page.
 *
 * These tests measure:
 * - Frame rate (FPS) during animation
 * - Script execution time
 * - Layout/paint time
 * - Total blocking time
 *
 * Run with: npx playwright test --project=performance
 */

interface PerformanceMetrics {
  averageFps: number;
  minFps: number;
  maxFps: number;
  scriptingTime: number;
  renderingTime: number;
  paintingTime: number;
  totalTime: number;
  longTaskCount: number;
  frameCount: number;
}

interface FrameData {
  timestamp: number;
  duration: number;
}

/**
 * Measures performance metrics while diagrams are animating
 */
async function measureDiagramPerformance(
  page: Page,
  durationMs: number = 5000
): Promise<PerformanceMetrics> {
  // Start collecting performance metrics via CDP
  const client = await page.context().newCDPSession(page);

  await client.send("Performance.enable");
  await client.send("Profiler.enable");

  // Collect frame timing data
  const frames: FrameData[] = [];
  let longTaskCount = 0;

  // Use PerformanceObserver to track frames and long tasks
  await page.evaluate(() => {
    (window as any).__perfFrames = [];
    (window as any).__longTasks = 0;

    // Track animation frames
    let lastTime = performance.now();
    const trackFrame = (time: number) => {
      const delta = time - lastTime;
      (window as any).__perfFrames.push({ timestamp: time, duration: delta });
      lastTime = time;
      if ((window as any).__trackingPerf) {
        requestAnimationFrame(trackFrame);
      }
    };

    (window as any).__trackingPerf = true;
    requestAnimationFrame(trackFrame);

    // Track long tasks
    if ("PerformanceObserver" in window) {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.duration > 50) {
            (window as any).__longTasks++;
          }
        }
      });
      try {
        observer.observe({ entryTypes: ["longtask"] });
        (window as any).__longTaskObserver = observer;
      } catch (e) {
        // longtask not supported in all browsers
      }
    }
  });

  // Wait for the measurement period
  await page.waitForTimeout(durationMs);

  // Stop tracking and collect results
  const results = await page.evaluate(() => {
    (window as any).__trackingPerf = false;
    if ((window as any).__longTaskObserver) {
      (window as any).__longTaskObserver.disconnect();
    }
    return {
      frames: (window as any).__perfFrames as FrameData[],
      longTasks: (window as any).__longTasks as number,
    };
  });

  // Get performance metrics from CDP
  const perfMetrics = await client.send("Performance.getMetrics");

  // Calculate FPS from frame data
  const frameDeltas = results.frames
    .slice(1) // Skip first frame (no delta)
    .map((f) => f.duration)
    .filter((d) => d > 0 && d < 1000); // Filter out anomalies

  const fpsValues = frameDeltas.map((delta) => 1000 / delta);

  const averageFps = fpsValues.length > 0
    ? fpsValues.reduce((a, b) => a + b, 0) / fpsValues.length
    : 0;
  const minFps = fpsValues.length > 0 ? Math.min(...fpsValues) : 0;
  const maxFps = fpsValues.length > 0 ? Math.max(...fpsValues) : 0;

  // Extract relevant metrics
  const metricsMap = new Map(
    perfMetrics.metrics.map((m) => [m.name, m.value])
  );

  const scriptingTime = metricsMap.get("ScriptDuration") ?? 0;
  const renderingTime = metricsMap.get("LayoutDuration") ?? 0;
  const paintingTime = metricsMap.get("PaintDuration") ?? metricsMap.get("RecalcStyleDuration") ?? 0;
  const totalTime = metricsMap.get("TaskDuration") ?? 0;

  await client.send("Performance.disable");
  await client.send("Profiler.disable");

  return {
    averageFps,
    minFps,
    maxFps,
    scriptingTime: scriptingTime * 1000, // Convert to ms
    renderingTime: renderingTime * 1000,
    paintingTime: paintingTime * 1000,
    totalTime: totalTime * 1000,
    longTaskCount: results.longTasks,
    frameCount: results.frames.length,
  };
}

/**
 * Scrolls to make diagram components visible in the viewport.
 * Selects the middle diagram to maximize visibility of surrounding diagrams.
 * @returns true if diagrams were found and scrolled to, false otherwise
 */
async function scrollToDiagrams(page: Page): Promise<boolean> {
  // Find diagram SVGs and scroll to the middle one
  const found = await page.evaluate(() => {
    // Find SVGs that are part of the diagrams
    const svgs = document.querySelectorAll("svg");
    const diagramSvgs = Array.from(svgs).filter((svg) => {
      const viewBox = svg.getAttribute("viewBox");
      return viewBox && (
        viewBox.includes("0 0 300 300") || // UploadDiagram & StreamBitsRouting
        viewBox.includes("0 0 300 400") || // CodeBuildDiagram vertical
        viewBox.includes("0 0 400 200")    // CodeBuildDiagram horizontal
      );
    });

    if (diagramSvgs.length === 0) {
      return false;
    }

    // Scroll to the middle diagram to maximize visibility of all diagrams
    const middleIndex = Math.floor(diagramSvgs.length / 2);
    diagramSvgs[middleIndex].scrollIntoView({ block: "center", behavior: "instant" });
    return true;
  });

  // Give time for scroll to complete
  await page.waitForTimeout(100);

  return found;
}

/**
 * Writes performance results to a JSON file for comparison
 */
function formatMetrics(metrics: PerformanceMetrics, label: string): string {
  return `
${label}:
  Average FPS: ${metrics.averageFps.toFixed(1)}
  Min FPS: ${metrics.minFps.toFixed(1)}
  Max FPS: ${metrics.maxFps.toFixed(1)}
  Frame Count: ${metrics.frameCount}
  Script Time: ${metrics.scriptingTime.toFixed(2)}ms
  Render Time: ${metrics.renderingTime.toFixed(2)}ms
  Paint Time: ${metrics.paintingTime.toFixed(2)}ms
  Total Time: ${metrics.totalTime.toFixed(2)}ms
  Long Tasks: ${metrics.longTaskCount}
`.trim();
}

test.describe("Diagram Performance Tests", () => {
  test.beforeEach(async ({ page }) => {
    // Mock any API calls to prevent network delays
    await page.route("**/api/**", async (route) => {
      await route.fulfill({ status: 200, json: {} });
    });
  });

  test("measure baseline performance with all diagrams visible", async ({ page }) => {
    // Navigate to the receipt page
    await page.goto("/receipt");

    // Wait for the page to be fully loaded (use first h1 to avoid strict mode violation)
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 15000 });

    // Wait a bit for initial hydration to complete
    await page.waitForTimeout(1000);

    // Scroll to make diagrams visible
    const foundDiagrams = await scrollToDiagrams(page);
    expect(foundDiagrams).toBe(true);

    // Wait for diagrams to start animating
    await page.waitForTimeout(500);

    // Measure performance for 5 seconds (covers multiple animation cycles)
    const metrics = await measureDiagramPerformance(page, 5000);

    console.log("\n" + formatMetrics(metrics, "DIAGRAM PERFORMANCE BASELINE"));

    // Store metrics for later comparison
    const metricsJson = JSON.stringify({
      timestamp: new Date().toISOString(),
      type: "baseline",
      metrics,
    }, null, 2);

    // Write to test artifacts
    await page.evaluate((data) => {
      console.log("PERF_METRICS:" + data);
    }, metricsJson);

    // Performance assertions (baseline expectations)
    // These may fail initially and will be updated after optimization
    expect(metrics.averageFps).toBeGreaterThan(20); // Minimum acceptable FPS
    expect(metrics.longTaskCount).toBeLessThan(50); // Maximum long tasks
  });

  test("measure performance while scrolling through diagrams", async ({ page }) => {
    await page.goto("/receipt");
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 15000 });
    await page.waitForTimeout(1000);

    // Start measuring before scroll
    const client = await page.context().newCDPSession(page);
    await client.send("Performance.enable");

    // Scroll through the page to trigger all diagrams
    await page.evaluate(async () => {
      const scrollStep = window.innerHeight;
      const maxScroll = document.documentElement.scrollHeight;

      for (let pos = 0; pos < maxScroll; pos += scrollStep) {
        window.scrollTo(0, pos);
        await new Promise((r) => setTimeout(r, 100));
      }
    });

    // Measure performance at the diagram section
    const foundDiagrams = await scrollToDiagrams(page);
    expect(foundDiagrams).toBe(true);
    const metrics = await measureDiagramPerformance(page, 3000);

    console.log("\n" + formatMetrics(metrics, "SCROLL + ANIMATE PERFORMANCE"));

    await client.send("Performance.disable");

    // Assertions
    expect(metrics.averageFps).toBeGreaterThan(15);
  });

  test("measure individual diagram performance - CodeBuildDiagram", async ({ page }) => {
    await page.goto("/receipt");
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 15000 });
    await page.waitForTimeout(1000);

    // Scroll specifically to CodeBuildDiagram area
    await page.evaluate(() => {
      // Find CodeBuildDiagram by looking for its unique SVG structure
      const svgs = document.querySelectorAll("svg");
      for (const svg of Array.from(svgs)) {
        const viewBox = svg.getAttribute("viewBox");
        if (viewBox === "0 0 300 400" || viewBox === "0 0 400 200") {
          svg.scrollIntoView({ block: "center", behavior: "instant" });
          break;
        }
      }
    });

    await page.waitForTimeout(500);
    const metrics = await measureDiagramPerformance(page, 3000);

    console.log("\n" + formatMetrics(metrics, "CODEBUILD DIAGRAM ONLY"));

    expect(metrics.averageFps).toBeGreaterThan(25);
  });

  test("measure individual diagram performance - StreamBitsRoutingDiagram", async ({ page }) => {
    await page.goto("/receipt");
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 15000 });
    await page.waitForTimeout(1000);

    // StreamBitsRoutingDiagram has viewBox="0 0 300 300"
    await page.evaluate(() => {
      const svgs = document.querySelectorAll('svg[viewBox="0 0 300 300"]');
      // First one might be UploadDiagram, find the one with robot paths
      for (const svg of Array.from(svgs)) {
        if (svg.querySelector("#MiddleRobot") || svg.querySelector("#DynamoStream")) {
          svg.scrollIntoView({ block: "center", behavior: "instant" });
          break;
        }
      }
    });

    await page.waitForTimeout(500);
    const metrics = await measureDiagramPerformance(page, 3000);

    console.log("\n" + formatMetrics(metrics, "STREAMBITS ROUTING DIAGRAM ONLY"));

    expect(metrics.averageFps).toBeGreaterThan(25);
  });

  test("measure individual diagram performance - UploadDiagram", async ({ page }) => {
    await page.goto("/receipt");
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 15000 });
    await page.waitForTimeout(1000);

    // UploadDiagram has viewBox="0 0 300 300" and PyTorch group
    await page.evaluate(() => {
      const svgs = document.querySelectorAll('svg[viewBox="0 0 300 300"]');
      for (const svg of Array.from(svgs)) {
        if (svg.querySelector("#PyTorch")) {
          svg.scrollIntoView({ block: "center", behavior: "instant" });
          break;
        }
      }
    });

    await page.waitForTimeout(500);
    const metrics = await measureDiagramPerformance(page, 3000);

    console.log("\n" + formatMetrics(metrics, "UPLOAD DIAGRAM ONLY"));

    expect(metrics.averageFps).toBeGreaterThan(25);
  });

  test("memory usage during diagram animation", async ({ page }) => {
    await page.goto("/receipt");
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 15000 });

    // Get initial memory
    const initialMemory = await page.evaluate(() => {
      if ("memory" in performance) {
        return (performance as any).memory.usedJSHeapSize;
      }
      return 0;
    });

    const foundDiagrams = await scrollToDiagrams(page);
    expect(foundDiagrams).toBe(true);

    // Wait for several animation cycles
    await page.waitForTimeout(5000);

    // Get final memory
    const finalMemory = await page.evaluate(() => {
      if ("memory" in performance) {
        return (performance as any).memory.usedJSHeapSize;
      }
      return 0;
    });

    const memoryGrowth = finalMemory - initialMemory;
    const memoryGrowthMB = memoryGrowth / (1024 * 1024);

    console.log(`\nMEMORY USAGE:`);
    console.log(`  Initial: ${(initialMemory / (1024 * 1024)).toFixed(2)} MB`);
    console.log(`  Final: ${(finalMemory / (1024 * 1024)).toFixed(2)} MB`);
    console.log(`  Growth: ${memoryGrowthMB.toFixed(2)} MB`);

    // Memory should not grow excessively during animation
    // (indicates memory leak if it does)
    if (initialMemory > 0) {
      expect(memoryGrowthMB).toBeLessThan(50); // Max 50MB growth
    }
  });
});
