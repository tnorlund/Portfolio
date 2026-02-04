import { expect, test, devices } from "@playwright/test";

import {
  mockQAMetadata,
  mockQAQuestions,
  MOCK_ANSWERS,
  EXAMPLE_TRACE_ANSWER,
} from "./fixtures/qa-agent-flow";

/**
 * Tests for QAAgentFlow mobile answer state bug.
 *
 * The bug: when consecutive questions go through a `null` data state,
 * questionIndex stays -1 both times so the reset effect never fires,
 * and the answer text stays stuck on the EXAMPLE_TRACE fallback
 * ("You spent $58.38 on coffee").
 *
 * Uses Pixel 5 viewport to reproduce the mobile-specific timing.
 */

test.use({ ...devices["Pixel 5"] });

test.describe("QAAgentFlow", () => {
  test.beforeEach(async ({ page }) => {
    // Mock CDN image requests (receipts, assets) with SVG placeholder
    await page.route("**/*.webp", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: Buffer.from(
          `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400" viewBox="0 0 300 400">
            <rect width="300" height="400" fill="#e8e8e8"/>
            <text x="150" y="200" text-anchor="middle" fill="#666" font-family="sans-serif" font-size="12">Mock</text>
          </svg>`
        ),
      });
    });

    await page.route("**/*.jpg", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: Buffer.from(
          `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400" viewBox="0 0 300 400">
            <rect width="300" height="400" fill="#e8e8e8"/>
          </svg>`
        ),
      });
    });

    // Mock word_similarity API to prevent unrelated console errors
    await page.route("**/word_similarity*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          query: "milk",
          results: [],
          timing: {},
          grand_total: 0,
          commentary: "",
        }),
      });
    });
  });

  test("displays real answer data instead of EXAMPLE_TRACE fallback", async ({
    page,
  }) => {
    // Mock metadata endpoint (no ?index param)
    await page.route("**/qa/visualization*", async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.has("index")) {
        const idx = parseInt(url.searchParams.get("index")!, 10);
        const q = mockQAQuestions[idx] ?? mockQAQuestions[0];
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ questions: [q] }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(mockQAMetadata),
        });
      }
    });

    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15_000 });

    // Scroll to the QA section
    const qaHeading = page.locator("h1", { hasText: "So Now What?" });
    await qaHeading.scrollIntoViewIfNeeded();

    // Wait for one of the real mock answers to appear (not the EXAMPLE_TRACE)
    const anyMockAnswer = page.locator("p").filter({
      hasText: new RegExp(
        MOCK_ANSWERS.map((a) => a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(
          "|"
        )
      ),
    });
    await expect(anyMockAnswer.first()).toBeVisible({ timeout: 30_000 });

    // The EXAMPLE_TRACE answer should NOT be visible
    await expect(
      page.getByText(EXAMPLE_TRACE_ANSWER, { exact: false })
    ).not.toBeVisible();
  });

  test("answer updates when auto-advancing between questions", async ({
    page,
  }) => {
    test.setTimeout(90_000);

    // Mock all QA API routes with immediate responses
    await page.route("**/qa/visualization*", async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.has("index")) {
        const idx = parseInt(url.searchParams.get("index")!, 10);
        const q = mockQAQuestions[idx] ?? mockQAQuestions[0];
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ questions: [q] }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(mockQAMetadata),
        });
      }
    });

    await page.goto("/receipt");
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15_000 });

    const qaHeading = page.locator("h1", { hasText: "So Now What?" });
    await qaHeading.scrollIntoViewIfNeeded();

    // Wait for the first answer to appear
    const anyMockAnswer = page.locator("p").filter({
      hasText: new RegExp(
        MOCK_ANSWERS.map((a) => a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(
          "|"
        )
      ),
    });
    await expect(anyMockAnswer.first()).toBeVisible({ timeout: 30_000 });

    // Determine which answer appeared first
    const firstAnswerText = await anyMockAnswer.first().textContent();
    const firstAnswer = MOCK_ANSWERS.find((a) => firstAnswerText?.includes(a));
    expect(firstAnswer).toBeTruthy();

    // Wait for that first answer to disappear (cycle reset)
    await expect(
      page.getByText(firstAnswer!, { exact: false })
    ).not.toBeVisible({ timeout: 30_000 });

    // Wait for a different answer to appear
    const otherAnswers = MOCK_ANSWERS.filter((a) => a !== firstAnswer);
    const otherAnswerLocator = page.locator("p").filter({
      hasText: new RegExp(
        otherAnswers
          .map((a) => a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
          .join("|")
      ),
    });
    await expect(otherAnswerLocator.first()).toBeVisible({ timeout: 60_000 });
  });

  test("answer does not stay stuck on EXAMPLE_TRACE after consecutive null data states", async ({
    page,
  }) => {
    test.setTimeout(180_000);

    // The hook shuffles indices and prefetches 3 ahead (prefetchAhead=3).
    // We respond to the first question index request with valid data so
    // the first cycle shows a real answer. All other index requests get
    // empty responses, so fetchQuestion returns null → data stays null.
    //
    // In practice, loadQuestion's fetch is initiated before prefetch's
    // fetches (same synchronous .then() block), so the first request
    // reaching our handler is the one we want. Prefetch requests for
    // other indices get empty responses → nothing gets cached for those.
    //
    // After first cycle: advance() → loadQuestion(nextIdx) → cache miss →
    // data=null → fetch returns empty → data stays null.
    // Component: questionData=undefined → questionIndex = -1.
    //
    // After EXAMPLE_TRACE cycle: advance() again → same outcome → -1.
    // Two consecutive -1 values → reset effect doesn't fire → BUG.
    let firstRespondedIndex: number | null = null;

    await page.route("**/qa/visualization*", async (route) => {
      const url = new URL(route.request().url());

      if (!url.searchParams.has("index")) {
        // Metadata: use a small total so re-shuffle happens quickly
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ metadata: { total_questions: 3 } }),
        });
        return;
      }

      const idx = parseInt(url.searchParams.get("index")!, 10);

      if (firstRespondedIndex === null) {
        // First question-index request — respond with real data
        firstRespondedIndex = idx;
        const q = {
          ...mockQAQuestions[idx % 3],
          questionIndex: idx,
        };
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ questions: [q] }),
        });
      } else if (idx === firstRespondedIndex) {
        // Re-request for the same index (e.g. after re-shuffle) — use cache
        const q = {
          ...mockQAQuestions[idx % 3],
          questionIndex: idx,
        };
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ questions: [q] }),
        });
      } else {
        // All other indices: return empty → fetchQuestion returns null →
        // data stays null → questionData is undefined → questionIndex = -1
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ questions: [] }),
        });
      }
    });

    await page.goto("/receipt");
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15_000 });

    const qaHeading = page.locator("h1", { hasText: "So Now What?" });
    await qaHeading.scrollIntoViewIfNeeded();

    // Phase 1: Wait for the real answer from mock data to appear.
    // The first question animates through its 3-step trace (~5s + 10s hold).
    const anyMockAnswer = page.locator("p").filter({
      hasText: new RegExp(
        MOCK_ANSWERS.map((a) => a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(
          "|"
        )
      ),
    });
    await expect(anyMockAnswer.first()).toBeVisible({ timeout: 30_000 });

    // Phase 2: After the ~10s hold, onCycleComplete fires. The hook calls
    // advance() → loadQuestion(nextIdx). If nextIdx was prefetched and
    // cached (returned valid data from the first batch), a cached answer
    // plays. Otherwise data=null → EXAMPLE_TRACE plays.
    // Either way, eventually we reach an uncached index where data=null.
    //
    // With total_questions:3, only the first index got valid data.
    // The other 2 indices returned empty → not cached.
    // After first cycle, advance() hits an uncached index → null data.
    await expect(
      page.getByText(EXAMPLE_TRACE_ANSWER, { exact: false })
    ).toBeVisible({ timeout: 60_000 });

    // Phase 3: Wait for the EXAMPLE_TRACE cycle to complete (~26s for
    // 8-step trace with real durationMs values) and the next onCycleComplete
    // to fire. The hook advances again — the new index is also uncached →
    // data stays null → questionIndex is -1 again (same as last time).
    //
    // THE BUG: questionIndex goes from -1 → -1, so the reset effect at
    // line 191 of QAAgentFlow.tsx never fires. The answer text stays
    // stuck showing "You spent $58.38 on coffee".
    //
    // Wait for the EXAMPLE_TRACE cycle to finish plus buffer.
    await page.waitForTimeout(35_000);

    // Assert: EXAMPLE_TRACE answer should NOT still be visible.
    // If the bug is present, this assertion FAILS (answer stays stuck).
    // If the bug is fixed, the reset fires and the answer disappears.
    await expect(
      page.getByText(EXAMPLE_TRACE_ANSWER, { exact: false })
    ).not.toBeVisible({ timeout: 15_000 });
  });

  test("no JavaScript errors during QA animation cycle", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => {
      pageErrors.push(error.message);
    });

    // Mock all QA API routes
    await page.route("**/qa/visualization*", async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.has("index")) {
        const idx = parseInt(url.searchParams.get("index")!, 10);
        const q = mockQAQuestions[idx] ?? mockQAQuestions[0];
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ questions: [q] }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(mockQAMetadata),
        });
      }
    });

    await page.goto("/receipt");
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15_000 });

    const qaHeading = page.locator("h1", { hasText: "So Now What?" });
    await qaHeading.scrollIntoViewIfNeeded();

    // Wait for the component to render and animate through one cycle
    const anyMockAnswer = page.locator("p").filter({
      hasText: new RegExp(
        MOCK_ANSWERS.map((a) => a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join(
          "|"
        )
      ),
    });
    await expect(anyMockAnswer.first()).toBeVisible({ timeout: 30_000 });

    // Verify no JavaScript errors occurred
    expect(pageErrors, "Page should not have JavaScript errors").toEqual([]);
  });
});
