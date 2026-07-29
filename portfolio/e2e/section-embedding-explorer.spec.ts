import { expect, test } from "@playwright/test";

const PAGE = process.env.BASE_URL ? "/receipt" : "/receipt.html";

test("section embedding explainer survives every act and remains usable on mobile", async ({
  page,
}) => {
  const errors: string[] = [];
  const noise =
    /Failed to fetch|net::ERR_|Load failed|CORS policy|api\.tylernorlund\.com|Failed to load resource/;
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && !noise.test(message.text())) {
      errors.push(`console: ${message.text().slice(0, 200)}`);
    }
  });

  await page.goto(PAGE, { waitUntil: "networkidle" });

  const figure = page.getByTestId("section-embedding-explorer");
  for (let i = 0; i < 50 && (await figure.count()) === 0; i += 1) {
    await page.mouse.wheel(0, 700);
    await page.waitForTimeout(150);
  }
  await expect(figure).toBeVisible({ timeout: 15_000 });
  await figure.scrollIntoViewIfNeeded();

  const stage = page.getByTestId("section-explorer-stage");
  const boxBefore = await stage.boundingBox();
  const dots = page.locator('[data-testid^="section-act-dot-"]');
  await expect(dots).toHaveCount(5);
  for (let index = 0; index < 5; index += 1) {
    await dots.nth(index).click();
    await page.waitForTimeout(420);
    const box = await stage.boundingBox();
    expect(Math.round(box?.height ?? 0)).toBe(Math.round(boxBefore?.height ?? 0));
  }

  await dots.nth(2).click();
  const visa = page.getByRole("button", { name: "VISA •••• 1234" });
  await visa.click();
  await expect(visa).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("85%")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await figure.scrollIntoViewIfNeeded();
  const viewportOverflow = await page.evaluate(() =>
    Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
  );
  expect(viewportOverflow).toBe(0);
  await expect(visa).toBeVisible();

  expect(errors, errors.join("\n")).toHaveLength(0);
});

test("reduced motion exposes a resolved static stack", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto(PAGE, { waitUntil: "networkidle" });
  const figure = page.getByTestId("section-embedding-explorer");
  for (let i = 0; i < 50 && (await figure.count()) === 0; i += 1) {
    await page.mouse.wheel(0, 700);
    await page.waitForTimeout(150);
  }
  await expect(figure).toHaveAttribute("data-mode", "static");
  await expect(page.getByTestId("section-act-result")).toBeAttached();
  await context.close();
});

