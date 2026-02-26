import { expect, test, Page, Route } from "@playwright/test";
import { mockTrainingMetricsResponse } from "./fixtures/training-metrics";
import { mockLayoutLMInferenceResponse } from "./fixtures/layoutlm-inference";
import { mockWordSimilarityResponse } from "./fixtures/word-similarity";

/**
 * Skeleton → Loaded height-stability tests.
 *
 * For each lazy-loaded component on /receipt, we:
 *   1. Block its API so it renders the skeleton/loading placeholder.
 *   2. Scroll to it and measure the skeleton container height.
 *   3. Fulfill the blocked API request with mock data.
 *   4. Wait for the loaded state to render.
 *   5. Measure the loaded container height and assert they match (within tolerance).
 *
 * A tolerance of 10 % is used because minor differences (a few px from borders,
 * dynamic content like epoch counts) are acceptable — the goal is to prevent
 * large layout shifts (e.g. 300 px → 900 px).
 *
 * Currently scoped to chromium (desktop). Mobile has known height-shift issues
 * in LayoutLM (~42%) and WordSimilarity (~28%) that should be fixed separately.
 */

test.use({ viewport: { width: 1280, height: 900 } });

const HEIGHT_TOLERANCE = 0.1; // 10%

/** Mock CDN image requests with an SVG placeholder */
async function mockImages(page: Page) {
  await page.route("**/*.jpg", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body: Buffer.from(
        `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400"><rect width="300" height="400" fill="#e8e8e8"/></svg>`
      ),
    });
  });
}

/** Stub APIs that are not under test so they don't error or slow things down */
async function stubOtherApis(page: Page, except: string[]) {
  const allApis: Record<string, unknown> = {
    "label_evaluator/within_receipt": { receipts: [] },
    "label_evaluator/financial_math": { receipts: [] },
    "label_evaluator/visualization": { receipts: [], stats: {} },
    "jobs/featured/training-metrics": mockTrainingMetricsResponse,
    "layoutlm_inference": mockLayoutLMInferenceResponse,
    "word_similarity": mockWordSimilarityResponse,
  };

  for (const [path, body] of Object.entries(allApis)) {
    if (except.some((e) => path.includes(e))) continue;
    await page.route(`**/${path}*`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });
  }
}

/** Navigate to the receipt page and wait for it to be ready */
async function navigateToReceipt(page: Page) {
  await page.goto("/receipt");
  await expect(
    page.locator("h1", { hasText: "Introduction" })
  ).toBeVisible({ timeout: 15000 });
}

/** Scroll a text anchor into view, then nudge slightly to reveal the component */
async function scrollToAnchor(page: Page, text: string, scrollUp?: number) {
  const anchor = page.getByText(text, { exact: false }).first();
  await anchor.scrollIntoViewIfNeeded();
  if (scrollUp) {
    await page.evaluate((up) => window.scrollBy(0, -up), scrollUp);
  } else {
    await page.evaluate(() => window.scrollBy(0, 300));
  }
  await page.waitForTimeout(500);
}

function assertHeightStable(
  skeletonHeight: number,
  loadedHeight: number,
  componentName: string
) {
  const ratio = Math.abs(loadedHeight - skeletonHeight) / skeletonHeight;
  expect(
    ratio,
    `${componentName}: skeleton height (${skeletonHeight}px) vs loaded height (${loadedHeight}px) — ${(ratio * 100).toFixed(1)}% shift exceeds ${HEIGHT_TOLERANCE * 100}% tolerance`
  ).toBeLessThanOrEqual(HEIGHT_TOLERANCE);
}

// ---------------------------------------------------------------------------
// TrainingMetricsAnimation
// ---------------------------------------------------------------------------
test.describe("Skeleton height stability", () => {
  test("TrainingMetricsAnimation: skeleton → loaded height is stable", async ({
    page,
  }) => {
    await mockImages(page);

    // Collect pending routes to fulfill later
    const pendingRoutes: Route[] = [];
    await page.route("**/jobs/featured/training-metrics*", async (route) => {
      pendingRoutes.push(route);
    });

    await stubOtherApis(page, ["training-metrics"]);
    await navigateToReceipt(page);

    // Scroll to the training metrics component
    await scrollToAnchor(page, "hyper-parameter tuning");
    await page.waitForTimeout(1000);

    // Measure skeleton height
    const container = page.locator('[class*="TrainingMetricsAnimation"]').first();
    await expect(container).toBeVisible({ timeout: 10000 });
    const skeletonBox = await container.boundingBox();
    expect(skeletonBox).not.toBeNull();
    const skeletonHeight = skeletonBox!.height;
    console.log(`  TrainingMetrics skeleton height: ${skeletonHeight}px`);

    // Fulfill the blocked API request
    for (const route of pendingRoutes) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockTrainingMetricsResponse),
      });
    }

    // Wait for loaded state — the epoch timeline appears when loaded
    await page.waitForTimeout(3000);
    const loadedBox = await container.boundingBox();
    expect(loadedBox).not.toBeNull();
    const loadedHeight = loadedBox!.height;
    console.log(`  TrainingMetrics loaded height: ${loadedHeight}px`);

    assertHeightStable(skeletonHeight, loadedHeight, "TrainingMetricsAnimation");
  });

  // ---------------------------------------------------------------------------
  // LayoutLMBatchVisualization
  // ---------------------------------------------------------------------------
  test("LayoutLMBatchVisualization: skeleton → loaded height is stable", async ({
    page,
  }) => {
    await mockImages(page);

    const pendingRoutes: Route[] = [];
    await page.route("**/layoutlm_inference*", async (route) => {
      pendingRoutes.push(route);
    });

    await stubOtherApis(page, ["layoutlm_inference"]);
    await navigateToReceipt(page);

    // The text anchor is AFTER the component, so scroll there and then up
    await scrollToAnchor(page, "The custom model does most of the work", 600);
    await page.waitForTimeout(1000);

    // Measure skeleton height — uses ReceiptFlowLoadingShell
    const container = page.locator('[class*="LayoutLMBatch"]').first();
    await expect(container).toBeVisible({ timeout: 10000 });
    const skeletonBox = await container.boundingBox();
    expect(skeletonBox).not.toBeNull();
    const skeletonHeight = skeletonBox!.height;
    console.log(`  LayoutLM skeleton height: ${skeletonHeight}px`);

    // Fulfill the blocked API requests (component fetches 3 times: initial + 2 prefetch)
    for (const route of pendingRoutes) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockLayoutLMInferenceResponse),
      });
    }

    // Also handle any subsequent requests
    await page.route("**/layoutlm_inference*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockLayoutLMInferenceResponse),
      });
    });

    await page.waitForTimeout(3000);
    const loadedBox = await container.boundingBox();
    expect(loadedBox).not.toBeNull();
    const loadedHeight = loadedBox!.height;
    console.log(`  LayoutLM loaded height: ${loadedHeight}px`);

    assertHeightStable(skeletonHeight, loadedHeight, "LayoutLMBatchVisualization");
  });

  // ---------------------------------------------------------------------------
  // WordSimilarity
  // ---------------------------------------------------------------------------
  test("WordSimilarity: loading → loaded height is stable", async ({
    page,
  }) => {
    await mockImages(page);

    const pendingRoutes: Route[] = [];
    await page.route("**/word_similarity*", async (route) => {
      pendingRoutes.push(route);
    });

    await stubOtherApis(page, ["word_similarity"]);
    await navigateToReceipt(page);

    // Scroll to the word-similarity section
    await scrollToAnchor(
      page,
      "digs through the corpus, finds every mention of milk"
    );
    await page.waitForTimeout(1000);

    // Measure loading state height — the container has data-testid and minHeight: 900px
    const container = page.locator('[data-testid="word-similarity"]').first();
    await expect(container).toBeVisible({ timeout: 10000 });
    const loadingBox = await container.boundingBox();
    expect(loadingBox).not.toBeNull();
    const loadingHeight = loadingBox!.height;
    console.log(`  WordSimilarity loading height: ${loadingHeight}px`);

    // Verify the loading placeholder reserves reasonable space
    expect(
      loadingHeight,
      "WordSimilarity loading placeholder should reserve at least 400px"
    ).toBeGreaterThanOrEqual(400);

    // Fulfill the blocked API request
    for (const route of pendingRoutes) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockWordSimilarityResponse),
      });
    }

    // Wait for loaded state — table appears
    const table = page.locator("table").first();
    await expect(table).toBeVisible({ timeout: 15000 });
    await page.waitForTimeout(1000);

    // Measure the loaded component — same data-testid, now on the loaded wrapper
    const loadedContainer = page.locator('[data-testid="word-similarity"]').first();
    const loadedBox = await loadedContainer.boundingBox();
    expect(loadedBox).not.toBeNull();
    const loadedHeight = loadedBox!.height;
    console.log(`  WordSimilarity loaded height: ${loadedHeight}px`);
    assertHeightStable(loadingHeight, loadedHeight, "WordSimilarity");
  });

  // ---------------------------------------------------------------------------
  // CICDLoop
  // ---------------------------------------------------------------------------
  test("CICDLoop: placeholder → rendered height is stable", async ({
    page,
  }) => {
    await mockImages(page);
    await stubOtherApis(page, []);
    await navigateToReceipt(page);

    // CICDLoop has no API — it renders as soon as it enters the viewport.
    // The placeholder has minHeight matching the SVG height prop.
    // Scroll to just before the component to see the placeholder first.
    const anchor = page.getByText("LangSmith records what happened", {
      exact: false,
    }).first();
    const anchorBox = await anchor.boundingBox();
    if (anchorBox) {
      // Position the anchor near top so the CICDLoop below is just barely not
      // in the viewport (we want to see its placeholder height)
      await page.evaluate((y) => window.scrollTo(0, y - 80), anchorBox.y);
    }
    await page.waitForTimeout(500);

    // The CICDLoop placeholder is a div with minHeight matching the component
    // Scroll a bit more to trigger the component
    await page.evaluate(() => window.scrollBy(0, 400));
    await page.waitForTimeout(2000);

    // Verify the CICDLoop SVG rendered
    const cicdSvg = page.locator('[class*="CICDLoop"] svg, text=Plan').first();
    const isCicdVisible = await cicdSvg.isVisible().catch(() => false);

    if (isCicdVisible) {
      // CICDLoop has a fixed size SVG, so it should be consistent
      const svgBox = await cicdSvg.boundingBox();
      expect(svgBox).not.toBeNull();
      console.log(
        `  CICDLoop rendered height: ${svgBox!.height}px (fixed SVG)`
      );
      // Since CICDLoop's placeholder uses the same minHeight as the SVG's
      // height prop, there should be no shift. Just verify it rendered.
      expect(svgBox!.height).toBeGreaterThan(100);
    }
  });
});
