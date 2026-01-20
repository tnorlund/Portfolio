import { expect, test } from "@playwright/test";

import {
  mockWordSimilarityResponse,
  mockWordSimilarityResponseWithoutCommentary,
  mockWordSimilarityHighSpending,
} from "./fixtures/word-similarity";

/**
 * Tests for WordSimilarity component to ensure:
 * - Component renders with data from API
 * - Grand total is displayed correctly in the table footer
 * - Commentary is rendered when present
 * - Timing breakdown is displayed
 * - Component handles missing commentary gracefully
 */

test.describe("WordSimilarity", () => {
  test.beforeEach(async ({ page }) => {
    // Mock CDN image requests with a placeholder
    await page.route("**/*.jpg", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: Buffer.from(
          `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400" viewBox="0 0 300 400">
            <rect width="300" height="400" fill="#e8e8e8"/>
            <text x="150" y="200" text-anchor="middle" fill="#666" font-family="sans-serif" font-size="12">Mock Receipt</text>
          </svg>`
        ),
      });
    });

  });

  test("renders commentary when present in API response", async ({ page }) => {
    // Mock the word similarity API
    await page.route("**/word_similarity**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockWordSimilarityResponse),
      });
    });

    // Navigate to receipt page
    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15000 });

    // Scroll down to the WordSimilarity component area
    // The component is after the "Asking About the $$$ Spent on Milk on Milk?" section
    const milkHeading = page.locator("h1", { hasText: "Asking About the $$$ Spent on Milk" });
    await milkHeading.scrollIntoViewIfNeeded();

    // Wait for the WordSimilarity component to load
    // The summary table should be visible
    const summaryTable = page.locator("table");
    await expect(summaryTable.first()).toBeVisible({ timeout: 15000 });

    // Check that the commentary is displayed
    const commentary = page.locator("p", {
      hasText: "significantly more than I expected",
    });
    await expect(commentary).toBeVisible({ timeout: 10000 });

    // Verify the commentary contains the dynamic amount
    await expect(commentary).toContainText("$21.47");
    await expect(commentary).toContainText("21 dollars a year");
  });

  test("displays grand total in table footer", async ({ page }) => {
    // Mock the word similarity API
    await page.route("**/word_similarity*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockWordSimilarityResponse),
      });
    });

    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15000 });

    // Scroll to the milk section
    const milkHeading = page.locator("h1", { hasText: "Asking About the $$$ Spent on Milk" });
    await milkHeading.scrollIntoViewIfNeeded();

    // Wait for the table to load
    const table = page.locator("table").first();
    await expect(table).toBeVisible({ timeout: 15000 });

    // Check that the table footer shows the total
    const tableFooter = table.locator("tfoot");
    await expect(tableFooter).toBeVisible();
    await expect(tableFooter).toContainText("Total");
    // The grand total should be the sum: 15.98 + 5.49 = 21.47
    await expect(tableFooter).toContainText("$21.47");
  });

  test("displays timing breakdown with Chroma Cloud indicator", async ({
    page,
  }) => {
    // Mock the word similarity API
    await page.route("**/word_similarity*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockWordSimilarityResponse),
      });
    });

    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15000 });

    // Scroll to the milk section
    const milkHeading = page.locator("h1", { hasText: "Asking About the $$$ Spent on Milk" });
    await milkHeading.scrollIntoViewIfNeeded();

    // Wait for timing breakdown to be visible
    // The timing breakdown shows "Document Retrieval" as the header
    const timingSection = page.locator("text=Document Retrieval");
    await expect(timingSection).toBeVisible({ timeout: 15000 });

    // Check for Chroma-related timing labels
    await expect(page.locator("text=Chroma Init")).toBeVisible();
    await expect(page.locator("text=Chroma Fetch")).toBeVisible();
  });

  test("handles missing commentary gracefully", async ({ page }) => {
    // Mock the word similarity API with response that has no commentary
    await page.route("**/word_similarity*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockWordSimilarityResponseWithoutCommentary),
      });
    });

    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15000 });

    // Scroll to the milk section
    const milkHeading = page.locator("h1", { hasText: "Asking About the $$$ Spent on Milk" });
    await milkHeading.scrollIntoViewIfNeeded();

    // Wait for the table to load (component has rendered)
    const table = page.locator("table").first();
    await expect(table).toBeVisible({ timeout: 15000 });

    // The commentary should NOT be visible when not in the response
    const commentary = page.locator("p", {
      hasText: "significantly more than I expected",
    });
    await expect(commentary).not.toBeVisible();

    // But the table should still show the total
    const tableFooter = table.locator("tfoot");
    await expect(tableFooter).toContainText("$21.47");
  });

  test("renders correct commentary for high spending amount", async ({
    page,
  }) => {
    // Mock the word similarity API with high spending response
    await page.route("**/word_similarity*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockWordSimilarityHighSpending),
      });
    });

    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15000 });

    // Scroll to the milk section
    const milkHeading = page.locator("h1", { hasText: "Asking About the $$$ Spent on Milk" });
    await milkHeading.scrollIntoViewIfNeeded();

    // Wait for the table to load
    const table = page.locator("table").first();
    await expect(table).toBeVisible({ timeout: 15000 });

    // Check that the commentary shows the higher amount
    const commentary = page.locator("p", {
      hasText: "significantly more than I expected",
    });
    await expect(commentary).toBeVisible({ timeout: 10000 });

    // Verify the commentary contains the high spending amount
    await expect(commentary).toContainText("$849.50");
    await expect(commentary).toContainText("800 dollars a year");
  });

  test("no JavaScript errors when loading WordSimilarity", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => {
      pageErrors.push(error.message);
    });

    // Mock the word similarity API
    await page.route("**/word_similarity*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockWordSimilarityResponse),
      });
    });

    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15000 });

    // Scroll to the milk section to trigger component loading
    const milkHeading = page.locator("h1", { hasText: "Asking About the $$$ Spent on Milk" });
    await milkHeading.scrollIntoViewIfNeeded();

    // Wait for the component to fully load
    const table = page.locator("table").first();
    await expect(table).toBeVisible({ timeout: 15000 });

    // Verify no JavaScript errors occurred
    expect(
      pageErrors,
      "Page should not have JavaScript errors"
    ).toEqual([]);
  });
});

test.describe("WordSimilarity API error handling", () => {
  test("displays error message when API fails", async ({ page }) => {
    // Mock the word similarity API to return an error
    await page.route("**/word_similarity*", async (route) => {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: "Internal server error" }),
      });
    });

    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15000 });

    // Scroll to the milk section
    const milkHeading = page.locator("h1", { hasText: "Asking About the $$$ Spent on Milk" });
    await milkHeading.scrollIntoViewIfNeeded();

    // In dev mode, Next.js shows an error overlay with the error message
    // In production, the component shows its own error message
    // Check for either the error overlay (dev) or component error message (prod)
    const errorOverlay = page.locator("text=Network response was not ok (status: 500)");
    const componentError = page.locator("text=No word similarity data available");

    // Either should be visible depending on environment
    await expect(errorOverlay.or(componentError)).toBeVisible({ timeout: 15000 });
  });

  test("displays loading state initially", async ({ page }) => {
    // Delay the API response to observe loading state
    await page.route("**/word_similarity*", async (route) => {
      // Delay response by 2 seconds
      await new Promise((resolve) => setTimeout(resolve, 2000));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockWordSimilarityResponse),
      });
    });

    await page.goto("/receipt");

    // Wait for page to be ready
    await expect(
      page.locator("h1", { hasText: "Introduction" })
    ).toBeVisible({ timeout: 15000 });

    // Scroll to the milk section
    const milkHeading = page.locator("h1", { hasText: "Asking About the $$$ Spent on Milk" });
    await milkHeading.scrollIntoViewIfNeeded();

    // Check for loading state - component shows "Loading..." text
    const loadingText = page.locator("text=Loading");
    await expect(loadingText.first()).toBeVisible({ timeout: 5000 });

    // Wait for content to load
    const table = page.locator("table").first();
    await expect(table).toBeVisible({ timeout: 15000 });
  });
});
