import { expect, Page, test } from "@playwright/test";

const PAGE = process.env.BASE_URL ? "/receipt" : "/receipt.html";

const ACTS = ["ocr", "baseline", "neighbors", "corrected", "final"] as const;

async function findFigure(page: Page) {
  const figure = page.getByTestId("section-embedding-explorer");
  for (let i = 0; i < 50 && (await figure.count()) === 0; i += 1) {
    await page.mouse.wheel(0, 700);
    await page.waitForTimeout(150);
  }
  await expect(figure).toBeVisible({ timeout: 15_000 });
  await figure.scrollIntoViewIfNeeded();
  return figure;
}

test("every resolved section-assignment step remains stable and keyboard accessible", async ({
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
  const figure = await findFigure(page);
  const stage = page.getByTestId("section-explorer-stage");
  const boxBefore = await stage.boundingBox();
  const dots = page.locator('[data-testid^="section-act-dot-"]');
  await expect(dots).toHaveCount(5);

  const expectedAssignments = [
    [null, null],
    ["TRANSACTION_INFO", "TRANSACTION_INFO"],
    ["TRANSACTION_INFO", "TRANSACTION_INFO"],
    ["SUMMARY", "PAYMENT"],
    ["SUMMARY", "PAYMENT"],
  ] as const;

  for (let index = 0; index < ACTS.length; index += 1) {
    await dots.nth(index).click();
    await page.waitForTimeout(420);
    await expect(page.getByTestId(`section-act-${ACTS[index]}`)).toBeVisible();
    await expect(dots.nth(index)).toHaveAttribute("aria-pressed", "true");

    const [subtotal, paymentTime] = expectedAssignments[index];
    const subtotalRow = page.getByTestId("section-row-row-11");
    const paymentTimeRow = page.getByTestId("section-row-row-34");
    if (subtotal) {
      await expect(subtotalRow).toHaveAttribute("data-section", subtotal);
      await expect(paymentTimeRow).toHaveAttribute("data-section", paymentTime);
    } else {
      await expect(subtotalRow).not.toHaveAttribute("data-section");
      await expect(paymentTimeRow).not.toHaveAttribute("data-section");
    }

    const box = await stage.boundingBox();
    expect(Math.round(box?.height ?? 0)).toBe(Math.round(boxBefore?.height ?? 0));
  }

  await expect(page.getByTestId("section-current-receipt")).toHaveAttribute(
    "data-source-id",
    "d47b0f01 · R1",
  );
  const currentImage = page.getByTestId("section-current-image");
  await expect(currentImage).toHaveAttribute(
    "src",
    /assets\/d47b0f01-859d-499b-a9b0-4feb312b4d27\/1\.webp/,
  );
  await expect
    .poll(() => currentImage.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBe(425);

  const imageBox = await currentImage.boundingBox();
  const subtotalBox = await page.getByTestId("section-row-row-11").boundingBox();
  expect((subtotalBox?.x ?? 0) - (imageBox?.x ?? 0)).toBeCloseTo(
    (imageBox?.width ?? 0) * 0.0845921503955299,
    0,
  );
  expect((subtotalBox?.y ?? 0) - (imageBox?.y ?? 0)).toBeCloseTo(
    (imageBox?.height ?? 0) * (1 - 0.49709302306590664),
    0,
  );
  await dots.nth(4).click();
  await expect(page.locator('[data-testid="section-act-final"] [data-corrected="true"]')).toHaveCount(6);
  await expect(page.locator('[data-testid="section-act-final"] [data-unresolved="true"]')).toHaveCount(1);
  await expect(page.getByTestId("section-row-row-31")).toHaveAttribute(
    "data-section",
    "PAYMENT",
  );
  await expect(
    page.getByTestId("section-act-final").getByTestId("section-current-receipt").getByTestId("section-band"),
  ).toHaveCount(4);

  await dots.nth(0).focus();
  await dots.nth(0).press("End");
  await expect(dots.nth(4)).toBeFocused();
  await expect(dots.nth(4)).toHaveAttribute("aria-pressed", "true");
  await dots.nth(4).press("ArrowLeft");
  await expect(dots.nth(3)).toBeFocused();
  await expect(dots.nth(3)).toHaveAttribute("aria-pressed", "true");

  await figure.scrollIntoViewIfNeeded();
  expect(errors, errors.join("\n")).toHaveLength(0);
});

test("mobile keeps the current receipt readable and shows fewer background receipts", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(PAGE, { waitUntil: "networkidle" });
  const figure = await findFigure(page);
  await page.getByTestId("section-act-dot-2").click();
  await page.waitForTimeout(420);

  const viewportOverflow = await page.evaluate(() =>
    Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
  );
  expect(viewportOverflow).toBe(0);

  const stageMetrics = await page.getByTestId("section-explorer-stage").evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
    clientHeight: node.clientHeight,
    scrollHeight: node.scrollHeight,
  }));
  expect(stageMetrics.scrollWidth).toBe(stageMetrics.clientWidth);
  expect(stageMetrics.scrollHeight).toBe(stageMetrics.clientHeight);

  const currentReceipt = page.getByTestId("section-current-receipt");
  await expect(currentReceipt).toBeVisible();
  const currentBox = await currentReceipt.boundingBox();
  expect(currentBox?.width ?? 0).toBeGreaterThanOrEqual(200);
  const image = page.getByTestId("section-current-image");
  await expect
    .poll(() => image.evaluate((node: HTMLImageElement) => node.naturalHeight))
    .toBe(884);

  await expect(page.locator('[data-testid^="section-reference-receipt-"]:visible')).toHaveCount(2);
  const neighborAct = page.getByTestId("section-act-neighbors");
  await expect(neighborAct.getByText(/OpenAI created the row embeddings/)).toBeVisible();
  await expect(neighborAct.getByText(/2-D map is schematic/)).toBeVisible();
  await figure.scrollIntoViewIfNeeded();
});

test("reduced motion exposes every resolved state without animation", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto(PAGE, { waitUntil: "networkidle" });
  const figure = await findFigure(page);
  await expect(figure).toHaveAttribute("data-mode", "static");
  for (const act of ACTS) {
    await expect(page.getByTestId(`section-act-${act}`)).toBeAttached();
  }
  const animationName = await page
    .getByTestId("section-act-neighbors")
    .locator("path")
    .first()
    .evaluate((node) => getComputedStyle(node).animationName);
  expect(animationName).toBe("none");
  await context.close();
});
