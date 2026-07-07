/**
 * Real-browser verification for the SynthesisPipeline figure — exists because
 * jsdom passed while real browsers broke (sticky bug, toggle crash, NaN
 * rects). Run against the static build (default) or dev:
 *   npx playwright test e2e/ --config e2e/playwright.config.ts
 *   BASE_URL=https://dev.tylernorlund.com npx playwright test e2e/ --config e2e/playwright.config.ts
 */
import { test, expect } from "@playwright/test";

// static python server has no /receipt -> receipt.html rewrite; CloudFront does
const PAGE = process.env.BASE_URL ? "/receipt" : "/receipt.html";

test("figure loads, autoplays, and survives the full act cycle", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  // Other figures on the page fetch api.tylernorlund.com, which is
  // unreachable in local runs -- their fetch noise is not our failure.
  const NOISE =
    /Failed to fetch|net::ERR_|Load failed|CORS policy|api\.tylernorlund\.com|Failed to load resource/;
  page.on("console", (m) => {
    if (m.type() === "error" && !NOISE.test(m.text())) {
      errors.push(`console: ${m.text().slice(0, 200)}`);
    }
  });

  await page.goto(PAGE, { waitUntil: "networkidle" });

  // The figure LAZY-MOUNTS (FigureBoundary placeholder until near viewport),
  // so scroll the page progressively until it exists — scrollIntoViewIfNeeded
  // can't target a node that hasn't mounted.
  // The act label is now a visually-hidden (sr-only) aria-live element — the
  // figure has no visible chrome — so assert it is ATTACHED, not visible.
  const headline = page.getByTestId("act-headline");
  for (let i = 0; i < 40 && (await headline.count()) === 0; i++) {
    await page.mouse.wheel(0, 700);
    await page.waitForTimeout(200);
  }
  await headline.scrollIntoViewIfNeeded();
  await expect(headline).toBeAttached({ timeout: 15_000 });

  // autoplay must advance acts without interaction
  const first = await headline.textContent();
  await expect
    .poll(async () => headline.textContent(), { timeout: 20_000 })
    .not.toBe(first);

  // The stage box must NOT bounce as acts swap: fixed height + width across
  // every act so the shared hero element travels in a stable coordinate frame.
  const stage = page.getByTestId("pipeline-stage");
  const dots = page.locator('[data-testid^="act-dot"]');
  const n = await dots.count();
  expect(n).toBeGreaterThanOrEqual(8);

  const round = (b: { width: number; height: number } | null) =>
    b ? { w: Math.round(b.width), h: Math.round(b.height) } : null;
  const box0 = round(await stage.boundingBox());
  // Sample a spread of acts (the wild ones + the finale) and assert the frame
  // is byte-identical each time.
  for (const i of [3, 4, 5, 7, n - 1]) {
    await dots.nth(i).click();
    await page.waitForTimeout(500);
    expect(round(await stage.boundingBox()), `stage box at act ${i}`).toEqual(
      box0,
    );
  }

  // click through every act dot; none may crash the page
  for (let i = 0; i < n; i++) {
    await dots.nth(i).click();
    await page.waitForTimeout(250);
  }

  // finale: three merchant cards
  await dots.nth(n - 1).click();
  await expect(page.getByTestId("finale-card").first()).toBeVisible({
    timeout: 10_000,
  });
  expect(await page.getByTestId("finale-card").count()).toBe(3);

  expect(errors, errors.join("\n")).toHaveLength(0);
});
