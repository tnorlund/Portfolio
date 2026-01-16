import { test, expect } from '@playwright/test';

/**
 * Tests to catch hydration failures across browsers (especially Safari/WebKit).
 *
 * These tests detect issues like:
 * - Empty __next div after JavaScript runs
 * - JavaScript errors during hydration (e.g., "Can't find variable: _self___NEXT_DATA___autoExport")
 * - React hydration mismatches
 * - Components not becoming interactive after hydration
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

    // Wait for specific content to be visible (more reliable than networkidle)
    const header = page.locator('header h1');
    await expect(header).toBeVisible({ timeout: 10000 });

    // Check that __next is NOT empty (the bug we just fixed)
    const nextDiv = page.locator('#__next');
    await expect(nextDiv).not.toBeEmpty();

    // Check that actual content is visible
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

    // Wait for specific content instead of networkidle
    const header = page.locator('header h1');
    await expect(header).toBeVisible({ timeout: 10000 });

    // Check content rendered
    const nextDiv = page.locator('#__next');
    await expect(nextDiv).not.toBeEmpty();

    expect(pageErrors, 'Page should not have JavaScript errors').toEqual([]);
  });

  test('receipt page renders content after hydration', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    await page.goto('/receipt');

    // Wait for the main heading to be visible (indicates page has rendered)
    // The receipt page has an h1 "Introduction" as its first heading
    const introHeading = page.locator('h1', { hasText: 'Introduction' });
    await expect(introHeading).toBeVisible({ timeout: 15000 });

    // Check content rendered
    const nextDiv = page.locator('#__next');
    await expect(nextDiv).not.toBeEmpty();

    // Check page-specific content - header should also be visible
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
    await expect(page.locator('header h1')).toBeVisible({ timeout: 10000 });

    // Navigate to resume
    await page.click('button:has-text("Résumé")');
    await expect(page).toHaveURL(/\/resume/);
    await expect(page.locator('#__next')).not.toBeEmpty();
    // Wait for resume-specific content (not shared header)
    await expect(page.locator('.resume-box').first()).toBeVisible();

    // Navigate back to home
    await page.click('header h1 a');
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator('#__next')).not.toBeEmpty();
    // Wait for homepage-specific content (navigation buttons)
    await expect(page.locator('button:has-text("Receipt")')).toBeVisible();

    // Navigate to receipt
    await page.click('button:has-text("Receipt")');
    await expect(page).toHaveURL(/\/receipt/);
    await expect(page.locator('#__next')).not.toBeEmpty();
    // Wait for receipt-specific content
    await expect(page.locator('h1', { hasText: 'Introduction' })).toBeVisible({ timeout: 15000 });

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

    // Wait for content instead of networkidle
    await expect(page.locator('header h1')).toBeVisible({ timeout: 10000 });

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

test.describe('Interactivity', () => {
  test('buttons respond to clicks after hydration', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    await page.goto('/');
    await expect(page.locator('header h1')).toBeVisible({ timeout: 10000 });

    // Click the Receipt button and verify navigation occurs
    const receiptButton = page.locator('button', { hasText: 'Receipt' });
    await expect(receiptButton).toBeVisible();
    await receiptButton.click();

    // Should navigate to /receipt
    await expect(page).toHaveURL(/\/receipt/);
    await expect(page.locator('h1', { hasText: 'Introduction' })).toBeVisible({ timeout: 15000 });

    expect(pageErrors, 'Button click should not cause JavaScript errors').toEqual([]);
  });

  test('resume button navigates correctly', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(error.message);
    });

    await page.goto('/');
    await expect(page.locator('header h1')).toBeVisible({ timeout: 10000 });

    // Click the Resume button and verify navigation
    const resumeButton = page.locator('button', { hasText: 'Résumé' });
    await expect(resumeButton).toBeVisible();
    await resumeButton.click();

    // Should navigate to /resume
    await expect(page).toHaveURL(/\/resume/);
    await expect(page.locator('header h1')).toBeVisible();

    expect(pageErrors, 'Button click should not cause JavaScript errors').toEqual([]);
  });
});
