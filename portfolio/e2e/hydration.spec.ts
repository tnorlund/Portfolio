import { test, expect } from '@playwright/test';

/**
 * Tests to catch hydration failures across browsers (especially Safari/WebKit).
 *
 * These tests detect issues like:
 * - Empty __next div after JavaScript runs
 * - JavaScript errors during hydration (e.g., "Can't find variable: _self___NEXT_DATA___autoExport")
 * - React hydration mismatches
 */

test.describe('Hydration', () => {
  test('homepage renders content after hydration', async ({ page }) => {
    // Collect console errors
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      }
    });

    // Collect page errors (uncaught exceptions)
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    await page.goto('/');

    // Wait for hydration to complete (React should be interactive)
    await page.waitForLoadState('networkidle');

    // Check that __next is NOT empty (the bug we just fixed)
    const nextDiv = page.locator('#__next');
    await expect(nextDiv).not.toBeEmpty();

    // Check that actual content is visible
    const header = page.locator('header h1');
    await expect(header).toBeVisible();
    await expect(header).toContainText('Tyler Norlund');

    // Check that buttons are visible and interactive
    const resumeButton = page.locator('button', { hasText: 'Résumé' });
    await expect(resumeButton).toBeVisible();

    const receiptButton = page.locator('button', { hasText: 'Receipt' });
    await expect(receiptButton).toBeVisible();

    // Fail if there were any JavaScript errors
    expect(pageErrors, 'Page should not have JavaScript errors').toEqual([]);

    // Filter out expected/benign console errors and fail on unexpected ones
    const criticalErrors = consoleErrors.filter(
      (err) =>
        !err.includes('favicon') &&
        !err.includes('analytics') &&
        !err.includes('Failed to load resource') // 404s for optional resources
    );
    expect(criticalErrors, 'Page should not have critical console errors').toEqual([]);
  });

  test('resume page renders content after hydration', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    await page.goto('/resume');
    await page.waitForLoadState('networkidle');

    // Check content rendered
    const nextDiv = page.locator('#__next');
    await expect(nextDiv).not.toBeEmpty();

    // Check page-specific content
    await expect(page.locator('header h1')).toBeVisible();

    expect(pageErrors, 'Page should not have JavaScript errors').toEqual([]);
  });

  test('receipt page renders content after hydration', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    await page.goto('/receipt');
    await page.waitForLoadState('networkidle');

    // Check content rendered
    const nextDiv = page.locator('#__next');
    await expect(nextDiv).not.toBeEmpty();

    // Check page-specific content
    await expect(page.locator('header h1')).toBeVisible();

    expect(pageErrors, 'Page should not have JavaScript errors').toEqual([]);
  });

  test('navigation works without hydration errors', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    // Start at homepage
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Navigate to resume
    await page.click('button:has-text("Résumé")');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#__next')).not.toBeEmpty();

    // Navigate back to home
    await page.click('header h1 a');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#__next')).not.toBeEmpty();

    // Navigate to receipt
    await page.click('button:has-text("Receipt")');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#__next')).not.toBeEmpty();

    expect(pageErrors, 'Navigation should not cause JavaScript errors').toEqual([]);
  });
});

test.describe('Browser-specific hydration', () => {
  // This test is especially important for Safari/WebKit
  test('no SWC transpilation errors', async ({ page, browserName }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Check for the specific error we encountered
    const hasSwcError = pageErrors.some(
      (err) =>
        err.includes("Can't find variable") ||
        err.includes('is not defined') ||
        err.includes('ReferenceError')
    );

    expect(
      hasSwcError,
      `${browserName} should not have SWC transpilation errors. Errors: ${pageErrors.join(', ')}`
    ).toBe(false);

    // Ensure content is visible
    await expect(page.locator('#__next')).not.toBeEmpty();
    await expect(page.locator('header h1')).toBeVisible();
  });
});
