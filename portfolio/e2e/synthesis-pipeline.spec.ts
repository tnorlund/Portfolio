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
  expect(n).toBeGreaterThanOrEqual(5);

  const round = (b: { width: number; height: number } | null) =>
    b ? { w: Math.round(b.width), h: Math.round(b.height) } : null;
  const box0 = round(await stage.boundingBox());
  // Sample a spread of acts (character, atlas, assemble, finale) and assert the
  // frame is byte-identical each time.
  for (const i of [1, 2, 3, n - 1]) {
    await dots.nth(i).click();
    await page.waitForTimeout(500);
    expect(round(await stage.boundingBox()), `stage box at act ${i}`).toEqual(
      box0,
    );
  }

  // Atlas glyphs must render as TYPE ON THE PAGE: currentColor through an alpha
  // mask, with the -webkit-mask set inline (Safari). Verify in the real browser
  // (jsdom can't) — the painted glyph color equals the page text color. The
  // atlas is the font act (index 2).
  await dots.nth(2).click();
  await page.waitForTimeout(500);
  const glyph = await page.evaluate(() => {
    const cell = document.querySelector('[data-testid="font-cell"]');
    const g = cell?.firstElementChild as HTMLElement | null;
    const container = document.getElementById("synthesis-pipeline");
    if (!g || !container) return null;
    const gs = getComputedStyle(g);
    return {
      background: gs.backgroundColor,
      pageColor: getComputedStyle(container).color,
      maskImage: gs.getPropertyValue("mask-image"),
      webkitMaskImage: gs.getPropertyValue("-webkit-mask-image"),
    };
  });
  expect(glyph, "atlas glyph tile present").not.toBeNull();
  // background-color: currentColor resolves to the page text color.
  expect(glyph!.background).toBe(glyph!.pageColor);
  // the glyph png masks currentColor via both the standard and -webkit-
  // mask-image (Safari path resolves too).
  expect(glyph!.maskImage).toMatch(/font_grid/);
  expect(glyph!.webkitMaskImage).toMatch(/font_grid/);

  // click through every act dot; none may crash the page
  for (let i = 0; i < n; i++) {
    await dots.nth(i).click();
    await page.waitForTimeout(250);
  }

  // finale: one merchant card per merchant, each with a currentColor logo mark
  await dots.nth(n - 1).click();
  await expect(page.getByTestId("finale-card").first()).toBeVisible({
    timeout: 10_000,
  });
  expect(await page.getByTestId("finale-card").count()).toBe(8);
  for (const name of [
    "Sprouts",
    "Costco",
    "Vons",
    "Trader Joe's",
    "CVS",
    "Target",
    "In-N-Out",
    "Wild Fork",
  ]) {
    await expect(
      page.getByRole("img", { name: new RegExp(`${name} logo`, "i") }),
    ).toBeAttached();
  }

  // The finale auto-pans through the pairs on its own, and any interaction
  // hands over to manual scroll. Force the row to overflow (narrow viewport)
  // then re-enter the finale so the pan starts fresh.
  await page.setViewportSize({ width: 560, height: 900 });
  await dots.nth(0).click();
  await page.waitForTimeout(300);
  await dots.nth(n - 1).click();
  const row = page.getByTestId("act-finale");
  await page.waitForTimeout(2000); // past the pan start delay
  const s1 = await row.evaluate((el) => el.scrollLeft);
  await page.waitForTimeout(1800);
  const s2 = await row.evaluate((el) => el.scrollLeft);
  // scrollLeft advances without any interaction...
  expect(s2, "auto-pan advances scrollLeft").toBeGreaterThan(s1 + 5);
  expect(await row.getAttribute("data-autopan")).not.toBeNull();
  // ...and a wheel interaction cancels it (hands over to manual scroll).
  await row.dispatchEvent("wheel", { deltaY: 20, deltaX: 0 });
  await page.waitForTimeout(200);
  expect(
    await row.getAttribute("data-autopan"),
    "a wheel interaction stops the auto-pan",
  ).toBeNull();

  expect(errors, errors.join("\n")).toHaveLength(0);
});
