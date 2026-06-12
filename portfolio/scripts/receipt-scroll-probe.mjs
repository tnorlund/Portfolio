/* global process, window, PerformanceObserver, document, NodeFilter, Node, getComputedStyle, performance, requestAnimationFrame, setTimeout, console */

import { chromium } from "playwright";

const TARGET_URL = process.env.RECEIPT_PROBE_URL ?? "http://localhost:3000/receipt";
const CPU_THROTTLE = Number(process.env.RECEIPT_PROBE_CPU_THROTTLE ?? "1");
const ROUTE_MODE = process.env.RECEIPT_PROBE_ROUTE_MODE ?? "real";

function metricMap(metrics) {
  return Object.fromEntries(metrics.metrics.map((metric) => [metric.name, metric.value]));
}

function deltaMetrics(before, after) {
  const keys = [
    "TaskDuration",
    "ScriptDuration",
    "LayoutDuration",
    "RecalcStyleDuration",
    "LayoutCount",
    "RecalcStyleCount",
    "Nodes",
    "JSHeapUsedSize",
    "JSHeapTotalSize",
    "JSEventListeners",
  ];

  return Object.fromEntries(keys.map((key) => [key, (after[key] ?? 0) - (before[key] ?? 0)]));
}

function summarizeFrames(frames) {
  const sorted = [...frames].sort((a, b) => a - b);
  const average = frames.length ? frames.reduce((sum, value) => sum + value, 0) / frames.length : 0;
  const percentile = (q) =>
    sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * q))] : 0;

  return {
    frames: frames.length,
    avgMs: average,
    p50Ms: percentile(0.5),
    p95Ms: percentile(0.95),
    maxMs: sorted.at(-1) ?? 0,
    over16ms: frames.filter((value) => value > 16.7).length,
    over33ms: frames.filter((value) => value > 33.4).length,
    over50ms: frames.filter((value) => value > 50).length,
    approxFps: average ? 1000 / average : 0,
  };
}

async function installApiMocks(page) {
  await page.route("**/api/**", async (route) => {
    const url = route.request().url();
    let body = {};

    if (url.includes("/receipts")) {
      body = { receipts: [], lastEvaluatedKey: null };
    } else if (url.includes("/label_evaluator/financial_math")) {
      body = { receipts: [] };
    } else if (url.includes("/label_evaluator/within_receipt")) {
      body = { receipts: [] };
    } else if (url.includes("/label_evaluator")) {
      body = { receipts: [] };
    } else if (url.includes("/label_validation_count")) {
      body = {};
    } else if (url.includes("/word_similarity")) {
      body = { words: [] };
    } else if (url.includes("/address_similarity")) {
      body = { pairs: [] };
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

async function main() {
  const launchArgs = ["--enable-precise-memory-info"];
  if (process.env.RECEIPT_PROBE_DISABLE_WEB_SECURITY === "1") {
    launchArgs.push("--disable-web-security");
  }

  const browser = await chromium.launch({
    headless: true,
    args: launchArgs,
  });

  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 720 },
      deviceScaleFactor: 1,
    });

    await context.addInitScript(() => {
      const state = {
        rafScheduled: 0,
        rafFired: 0,
        rafCanceled: 0,
        rafPending: 0,
        timeoutsSet: 0,
        timeoutsFired: 0,
        intervalsSet: 0,
        intervalsCleared: 0,
        activeIntervals: 0,
        longTasks: [],
      };

      window.__receiptScrollProbe = state;

      const originalRaf = window.requestAnimationFrame.bind(window);
      const originalCancelRaf = window.cancelAnimationFrame.bind(window);
      const activeRafs = new Set();

      window.requestAnimationFrame = (callback) => {
        const id = originalRaf((time) => {
          if (activeRafs.delete(id)) {
            state.rafPending = Math.max(0, state.rafPending - 1);
          }
          state.rafFired += 1;
          callback(time);
        });
        activeRafs.add(id);
        state.rafScheduled += 1;
        state.rafPending += 1;
        return id;
      };

      window.cancelAnimationFrame = (id) => {
        if (activeRafs.delete(id)) {
          state.rafPending = Math.max(0, state.rafPending - 1);
          state.rafCanceled += 1;
        }
        return originalCancelRaf(id);
      };

      const originalSetTimeout = window.setTimeout.bind(window);
      const originalSetInterval = window.setInterval.bind(window);
      const originalClearInterval = window.clearInterval.bind(window);
      const activeIntervals = new Set();

      window.setTimeout = (callback, delay, ...args) => {
        state.timeoutsSet += 1;
        return originalSetTimeout((...callbackArgs) => {
          state.timeoutsFired += 1;
          return callback(...callbackArgs);
        }, delay, ...args);
      };

      window.setInterval = (callback, delay, ...args) => {
        const id = originalSetInterval(callback, delay, ...args);
        activeIntervals.add(id);
        state.intervalsSet += 1;
        state.activeIntervals = activeIntervals.size;
        return id;
      };

      window.clearInterval = (id) => {
        if (activeIntervals.delete(id)) {
          state.intervalsCleared += 1;
          state.activeIntervals = activeIntervals.size;
        }
        return originalClearInterval(id);
      };

      if ("PerformanceObserver" in window) {
        try {
          const observer = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              state.longTasks.push({
                name: entry.name,
                startTime: entry.startTime,
                duration: entry.duration,
              });
            }
          });
          observer.observe({ entryTypes: ["longtask"] });
          Object.defineProperty(window, "__receiptScrollProbeLongTaskObserver", {
            value: observer,
            configurable: true,
          });
        } catch {
          // Long task observation is unavailable in some browsers.
        }
      }
    });

    const page = await context.newPage();
    const consoleLogs = [];
    const pageErrors = [];

    page.on("console", (message) => {
      const text = message.text();
      if (message.type() === "error" || text.includes("API Base")) {
        consoleLogs.push({ type: message.type(), text: text.slice(0, 500) });
      }
    });

    page.on("pageerror", (error) => {
      pageErrors.push(String(error).slice(0, 500));
    });

    if (ROUTE_MODE === "mock-empty") {
      await installApiMocks(page);
    }

    const client = await context.newCDPSession(page);
    await client.send("Performance.enable");
    if (CPU_THROTTLE !== 1) {
      await client.send("Emulation.setCPUThrottlingRate", { rate: CPU_THROTTLE });
    }

    const getPerfMetrics = async () => metricMap(await client.send("Performance.getMetrics"));

    const domStats = async (label) => {
      const cdp = await getPerfMetrics();
      const inPage = await page.evaluate((snapshotLabel) => {
        const elements = Array.from(document.getElementsByTagName("*"));
        let allNodes = 0;
        let textNodes = 0;
        const walker = document.createTreeWalker(document, NodeFilter.SHOW_ALL);

        while (walker.nextNode()) {
          allNodes += 1;
          if (walker.currentNode.nodeType === Node.TEXT_NODE) {
            textNodes += 1;
          }
        }

        const tagCounts = {};
        for (const element of elements) {
          const tag = element.tagName.toLowerCase();
          tagCounts[tag] = (tagCounts[tag] || 0) + 1;
        }

        const viewportElements = elements.filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && rect.bottom >= 0 && rect.top <= window.innerHeight;
        }).length;

        const animatedStyleElements = elements.filter((element) => {
          const style = getComputedStyle(element);
          return (
            style.animationName !== "none" ||
            style.transitionDuration !== "0s" ||
            style.willChange !== "auto" ||
            style.transform !== "none"
          );
        }).length;

        const cssAnimations = document.getAnimations ? document.getAnimations() : [];
        const sectionCounts = {};
        let currentSection = "before first heading";
        for (const element of elements) {
          if (element.matches("h1, h2")) {
            currentSection = `${element.tagName.toLowerCase()}: ${element.textContent?.trim() || "(untitled)"}`;
          }
          sectionCounts[currentSection] = (sectionCounts[currentSection] || 0) + 1;
        }
        const topSections = Object.entries(sectionCounts)
          .sort(([, a], [, b]) => b - a)
          .slice(0, 8)
          .map(([section, count]) => ({ section, count }));
        const probe = window.__receiptScrollProbe || {};
        const memory = performance.memory
          ? {
              usedJSHeapMB: performance.memory.usedJSHeapSize / 1048576,
              totalJSHeapMB: performance.memory.totalJSHeapSize / 1048576,
            }
          : null;

        return {
          label: snapshotLabel,
          scrollY: Math.round(window.scrollY),
          scrollHeight: document.documentElement.scrollHeight,
          viewportHeight: window.innerHeight,
          elements: elements.length,
          allNodes,
          textNodes,
          viewportElements,
          tagCounts,
          animatedStyleElements,
          cssAnimations: cssAnimations.length,
          cssRunningAnimations: cssAnimations.filter((animation) => animation.playState === "running").length,
          topSections,
          probe: {
            rafScheduled: probe.rafScheduled || 0,
            rafFired: probe.rafFired || 0,
            rafCanceled: probe.rafCanceled || 0,
            rafPending: probe.rafPending || 0,
            timeoutsSet: probe.timeoutsSet || 0,
            timeoutsFired: probe.timeoutsFired || 0,
            intervalsSet: probe.intervalsSet || 0,
            intervalsCleared: probe.intervalsCleared || 0,
            activeIntervals: probe.activeIntervals || 0,
            longTasks: (probe.longTasks || []).length,
            longTaskDurationMs: (probe.longTasks || []).reduce((sum, task) => sum + task.duration, 0),
          },
          memory,
        };
      }, label);

      return {
        ...inPage,
        cdp: {
          Nodes: cdp.Nodes,
          JSEventListeners: cdp.JSEventListeners,
          JSHeapUsedMB: (cdp.JSHeapUsedSize || 0) / 1048576,
          TaskDurationMs: (cdp.TaskDuration || 0) * 1000,
          ScriptDurationMs: (cdp.ScriptDuration || 0) * 1000,
          LayoutDurationMs: (cdp.LayoutDuration || 0) * 1000,
          RecalcStyleDurationMs: (cdp.RecalcStyleDuration || 0) * 1000,
          LayoutCount: cdp.LayoutCount,
          RecalcStyleCount: cdp.RecalcStyleCount,
        },
      };
    };

    const idleMeasure = async (label, durationMs = 2500) => {
      const beforeCdp = await getPerfMetrics();
      const beforeProbe = await page.evaluate(() => ({ ...window.__receiptScrollProbe }));
      await page.waitForTimeout(durationMs);
      const afterCdp = await getPerfMetrics();
      const afterProbe = await page.evaluate(() => ({ ...window.__receiptScrollProbe }));

      return {
        label,
        durationMs,
        rafFiredPerSec: ((afterProbe.rafFired || 0) - (beforeProbe.rafFired || 0)) / (durationMs / 1000),
        rafScheduledPerSec:
          ((afterProbe.rafScheduled || 0) - (beforeProbe.rafScheduled || 0)) / (durationMs / 1000),
        longTasks: (afterProbe.longTasks?.length || 0) - (beforeProbe.longTasks?.length || 0),
        longTaskDurationMs: (afterProbe.longTasks || [])
          .slice(beforeProbe.longTasks?.length || 0)
          .reduce((sum, task) => sum + task.duration, 0),
        cdpDelta: deltaMetrics(beforeCdp, afterCdp),
      };
    };

    const scrollMeasure = async (label, toY, durationMs = 8000) => {
      const beforeCdp = await getPerfMetrics();
      const beforeProbe = await page.evaluate(() => ({ ...window.__receiptScrollProbe }));
      const result = await page.evaluate(
        async ({ scrollLabel, targetY, scrollDurationMs }) => {
          const startY = window.scrollY;
          const distance = targetY - startY;
          const frames = [];
          const localLongTasks = [];
          let observer = null;

          if ("PerformanceObserver" in window) {
            try {
              observer = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                  localLongTasks.push(entry.duration);
                }
              });
              observer.observe({ entryTypes: ["longtask"] });
            } catch {
              // Long task observation is unavailable in some browsers.
            }
          }

          return await new Promise((resolve) => {
            const started = performance.now();
            let last = started;

            function step(now) {
              frames.push(now - last);
              last = now;
              const progress = Math.min(1, (now - started) / scrollDurationMs);
              window.scrollTo(0, startY + distance * progress);

              if (progress < 1) {
                requestAnimationFrame(step);
              } else {
                setTimeout(() => {
                  if (observer) {
                    observer.disconnect();
                  }
                  resolve({
                    label: scrollLabel,
                    fromY: Math.round(startY),
                    toY: Math.round(window.scrollY),
                    requestedToY: Math.round(targetY),
                    elapsedMs: performance.now() - started,
                    frames,
                    localLongTasks: localLongTasks.length,
                    localLongTaskDurationMs: localLongTasks.reduce((sum, duration) => sum + duration, 0),
                  });
                }, 150);
              }
            }

            requestAnimationFrame(step);
          });
        },
        { scrollLabel: label, targetY: toY, scrollDurationMs: durationMs },
      );
      const afterCdp = await getPerfMetrics();
      const afterProbe = await page.evaluate(() => ({ ...window.__receiptScrollProbe }));

      return {
        label,
        fromY: result.fromY,
        toY: result.toY,
        durationMs: result.elapsedMs,
        frameSummary: summarizeFrames(result.frames.slice(1)),
        localLongTasks: result.localLongTasks,
        localLongTaskDurationMs: result.localLongTaskDurationMs,
        rafFiredPerSec: ((afterProbe.rafFired || 0) - (beforeProbe.rafFired || 0)) / (result.elapsedMs / 1000),
        rafScheduledPerSec:
          ((afterProbe.rafScheduled || 0) - (beforeProbe.rafScheduled || 0)) / (result.elapsedMs / 1000),
        longTasks: (afterProbe.longTasks?.length || 0) - (beforeProbe.longTasks?.length || 0),
        longTaskDurationMs: (afterProbe.longTasks || [])
          .slice(beforeProbe.longTasks?.length || 0)
          .reduce((sum, task) => sum + task.duration, 0),
        cdpDelta: deltaMetrics(beforeCdp, afterCdp),
      };
    };

    await page.goto(TARGET_URL, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForSelector("h1", { timeout: 20000 });
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => undefined);
    await page.waitForTimeout(3000);

    const snapshots = [];
    const idles = [];
    const scrolls = [];

    snapshots.push(await domStats("top-after-hydration"));
    idles.push(await idleMeasure("top-idle", 2500));

    const maxY = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
    scrolls.push(await scrollMeasure("scroll-down-full-page", maxY, 10000));
    await page.waitForTimeout(1500);

    snapshots.push(await domStats("bottom-after-scroll-down"));
    idles.push(await idleMeasure("bottom-idle", 2500));

    scrolls.push(await scrollMeasure("scroll-up-full-page", 0, 10000));
    await page.waitForTimeout(1500);

    snapshots.push(await domStats("top-after-return-up"));
    idles.push(await idleMeasure("top-after-return-idle", 2500));

    await client.send("Performance.disable").catch(() => undefined);

    console.log(
      JSON.stringify(
        {
          probe: {
            cpuThrottle: CPU_THROTTLE,
            routeMode: ROUTE_MODE,
            url: TARGET_URL,
            viewport: "1280x720",
          },
          snapshots,
          idles,
          scrolls,
          consoleLogs: consoleLogs.slice(-12),
          pageErrors,
        },
        null,
        2,
      ),
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
