import { expect, test, Page, Route } from "@playwright/test";
import { mockTrainingMetricsResponse } from "./fixtures/training-metrics";
import { mockLayoutLMInferenceResponse } from "./fixtures/layoutlm-inference";
import { mockWordSimilarityResponse } from "./fixtures/word-similarity";

/**
 * Skeleton → Loaded height-stability tests.
 *
 * For each lazy-loaded component on /receipt, we:
 *   1. Block its API so it renders the skeleton/loading placeholder.
 *   2. Scroll to it and measure the page-level figure boundary.
 *   3. Fulfill the blocked API request with mock data.
 *   4. Wait for the loaded state to render.
 *   5. Measure the settled boundary and assert that its full box is unchanged.
 *
 * A one-pixel tolerance only covers subpixel rounding. The outer boundary is
 * the layout contract, so dynamic differences inside a figure must not move the
 * surrounding article on either desktop or mobile.
 */

const BOX_TOLERANCE_PX = 1;

type LayoutBox = { x: number; y: number; width: number; height: number };

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
  const introduction = page
    .getByRole("heading", { name: "Introduction", exact: true })
    .filter({ visible: true });
  await expect(introduction).toHaveCount(1, { timeout: 15000 });
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

function assertBoxStable(
  loadingBox: LayoutBox,
  loadedBox: LayoutBox,
  componentName: string
) {
  for (const key of ["x", "y", "width", "height"] as const) {
    const delta = Math.abs(loadedBox[key] - loadingBox[key]);
    expect(
      delta,
      `${componentName}: ${key} shifted by ${delta.toFixed(2)}px (loading ${loadingBox[key].toFixed(2)}px → loaded ${loadedBox[key].toFixed(2)}px)`
    ).toBeLessThanOrEqual(BOX_TOLERANCE_PX);
  }
}

function figureBoundary(page: Page, name: string) {
  return page
    .locator(`[data-figure-boundary="${name}"]`)
    .filter({ visible: true });
}

async function measureDocumentBox(
  page: Page,
  locator: ReturnType<Page["locator"]>
): Promise<LayoutBox | null> {
  const box = await locator.boundingBox();
  if (!box) return null;
  const scroll = await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
  return { ...box, x: box.x + scroll.x, y: box.y + scroll.y };
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
    const boundary = figureBoundary(page, "training-metrics");
    const skeletonBox = await measureDocumentBox(page, boundary);
    expect(skeletonBox).not.toBeNull();
    console.log(`  TrainingMetrics loading boundary: ${JSON.stringify(skeletonBox)}`);

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
    const loadedBox = await measureDocumentBox(page, boundary);
    expect(loadedBox).not.toBeNull();
    console.log(`  TrainingMetrics loaded boundary: ${JSON.stringify(loadedBox)}`);

    assertBoxStable(skeletonBox!, loadedBox!, "TrainingMetricsAnimation");
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
    const boundary = figureBoundary(page, "layoutlm-inference");
    const skeletonBox = await measureDocumentBox(page, boundary);
    expect(skeletonBox).not.toBeNull();
    console.log(`  LayoutLM loading boundary: ${JSON.stringify(skeletonBox)}`);

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
    const loadedBox = await measureDocumentBox(page, boundary);
    expect(loadedBox).not.toBeNull();
    console.log(`  LayoutLM loaded boundary: ${JSON.stringify(loadedBox)}`);

    assertBoxStable(skeletonBox!, loadedBox!, "LayoutLMBatchVisualization");
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
    const boundary = figureBoundary(page, "word-similarity");
    const loadingBox = await measureDocumentBox(page, boundary);
    expect(loadingBox).not.toBeNull();
    console.log(`  WordSimilarity loading boundary: ${JSON.stringify(loadingBox)}`);

    const evidenceSuffix = (page.viewportSize()?.width ?? 1280) <= 768
      ? "mobile"
      : "desktop";
    if (process.env.CAPTURE_SKELETON_EVIDENCE === "1") {
      const performanceToggle = page.getByText("⚡ Performance", { exact: true });
      if (await performanceToggle.count() === 1) {
        await performanceToggle.click();
      }
      await boundary.screenshot({
        path: `/tmp/portfolio-word-similarity-skeleton-${evidenceSuffix}.png`,
      });
    }

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
    const loadedBox = await measureDocumentBox(page, boundary);
    expect(loadedBox).not.toBeNull();
    console.log(`  WordSimilarity loaded boundary: ${JSON.stringify(loadedBox)}`);
    if (process.env.CAPTURE_SKELETON_EVIDENCE === "1") {
      await boundary.screenshot({
        path: `/tmp/portfolio-word-similarity-loaded-${evidenceSuffix}.png`,
      });
    }
    assertBoxStable(loadingBox!, loadedBox!, "WordSimilarity");
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
    // Measure the reserved boundary before bringing it into view.
    const boundary = figureBoundary(page, "cicd-loop");
    const placeholderBox = await measureDocumentBox(page, boundary);
    expect(placeholderBox).not.toBeNull();

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

    const renderedBox = await measureDocumentBox(page, boundary);
    expect(renderedBox).not.toBeNull();
    assertBoxStable(placeholderBox!, renderedBox!, "CICDLoop");
  });
});
