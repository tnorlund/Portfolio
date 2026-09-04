// Temporary helper: copy to portfolio/screenshot.mjs, run from portfolio/,
// delete before committing. Usage: node screenshot.mjs <before|after>
import { chromium } from "playwright-core";

const BASE = "http://localhost:3000/receipt"; // page under test
const prefix = process.argv[2] || "screenshot";
const outDir = "/path/to/temp/screenshots";

const browser = await chromium.launch();

async function hideDevOverlay(page) {
  await page.evaluate(() => {
    document.querySelectorAll("nextjs-portal").forEach((el) => el.remove());
    for (const el of document.querySelectorAll("*")) {
      const style = window.getComputedStyle(el);
      if (style.position === "fixed" && parseInt(style.zIndex) > 1000) {
        el.style.display = "none";
      }
    }
  });
}

async function shot(name, width, height) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: "networkidle" });
  await hideDevOverlay(page);

  const heading = page.locator("text=Some Heading").first();
  if (await heading.isVisible()) await heading.scrollIntoViewIfNeeded();
  await page.waitForTimeout(3000);
  await hideDevOverlay(page);

  const container = page.locator('[class*="ComponentName"]').first();
  if (await container.isVisible()) {
    await container.screenshot({ path: `${outDir}/${prefix}-${name}.png` });
  } else {
    await page.screenshot({ path: `${outDir}/${prefix}-${name}.png` });
  }
  await ctx.close();
}

await shot("desktop", 1280, 900);
await shot("mobile", 375, 812);
await browser.close();
