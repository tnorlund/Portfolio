import { test, expect } from '@playwright/test';

/**
 * Tests for ReceiptBoundingBoxGrid component to ensure:
 * - Loading and loaded states have the same dimensions
 * - No layout shift occurs when data loads
 * - Both bounding box figures maintain consistent 3:4 aspect ratio
 */

test.describe('ReceiptBoundingBoxGrid', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to receipt page
    await page.goto('/receipt');
    
    // Wait for page to be ready
    await expect(page.locator('h1', { hasText: 'Introduction' })).toBeVisible({ timeout: 15000 });
  });

  test('loading and loaded states have same dimensions', async ({ page }) => {
    // Find the grid container
    const grid = page.locator('[data-testid="receipt-bounding-box-grid"]');
    await expect(grid).toBeVisible();

    // Scroll to the grid to ensure it's in view (components use intersection observer)
    await grid.scrollIntoViewIfNeeded();
    await page.waitForTimeout(500); // Wait for intersection observer to trigger

    // Find the two bounding box containers (bottom row of grid)
    const scanCell = page.locator('[data-testid="scan-receipt-cell"]');
    const photoCell = page.locator('[data-testid="photo-receipt-cell"]');
    
    // Wait for cells to be visible (they might be hidden until intersection observer triggers)
    await expect(scanCell).toBeVisible({ timeout: 10000 });
    await expect(photoCell).toBeVisible({ timeout: 10000 });

    // Wait a bit for initial render
    await page.waitForTimeout(500);

    // Get dimensions of containers during loading state
    // Structure: gridCell > div (ReceiptBoundingBoxFrame outer) > div (aspect ratio container)
    const scanContainerLoading = scanCell.locator('div').first().locator('div').first();
    const photoContainerLoading = photoCell.locator('div').first().locator('div').first();

    // Check if loading state is visible (might be very brief)
    const scanLoadingVisible = await scanContainerLoading.locator('text=Loading').isVisible().catch(() => false);
    const photoLoadingVisible = await photoContainerLoading.locator('text=Loading').isVisible().catch(() => false);

    if (scanLoadingVisible || photoLoadingVisible) {
      // Get dimensions during loading
      const scanLoadingBox = await scanContainerLoading.boundingBox();
      const photoLoadingBox = await photoContainerLoading.boundingBox();

      if (scanLoadingBox && photoLoadingBox) {
        console.log('Loading state dimensions:', {
          scan: { width: scanLoadingBox.width, height: scanLoadingBox.height },
          photo: { width: photoLoadingBox.width, height: photoLoadingBox.height },
        });

        // Verify they have the same width (should be in a 2-column grid)
        expect(scanLoadingBox.width).toBeCloseTo(photoLoadingBox.width, 1);
      }
    }

    // Wait for content to load - look for SVG elements within the cells
    await scanCell.locator('svg').first().waitFor({ timeout: 15000, state: 'attached' });
    await photoCell.locator('svg').first().waitFor({ timeout: 15000, state: 'attached' });

    // Get dimensions after loading - get the frame containers (divs with aspect ratio)
    // Structure: gridCell > div (ReceiptBoundingBoxFrame) > div (aspect ratio container) > svg
    const scanFrame = scanCell.locator('div').first().locator('div').first();
    const photoFrame = photoCell.locator('div').first().locator('div').first();
    
    await expect(scanFrame).toBeAttached({ timeout: 10000 });
    await expect(photoFrame).toBeAttached({ timeout: 10000 });

    const scanLoadedBox = await scanFrame.boundingBox();
    const photoLoadedBox = await photoFrame.boundingBox();

    expect(scanLoadedBox).not.toBeNull();
    expect(photoLoadedBox).not.toBeNull();

    if (scanLoadedBox && photoLoadedBox) {
      console.log('Loaded state dimensions:', {
        scan: { width: scanLoadedBox.width, height: scanLoadedBox.height },
        photo: { width: photoLoadedBox.width, height: photoLoadedBox.height },
      });

      // Verify both have the same dimensions (should be in a 2-column grid)
      expect(scanLoadedBox.width).toBeCloseTo(photoLoadedBox.width, 1);
      expect(scanLoadedBox.height).toBeCloseTo(photoLoadedBox.height, 1);

      // Verify aspect ratio is approximately 3:4 (0.75)
      const scanAspectRatio = scanLoadedBox.width / scanLoadedBox.height;
      const photoAspectRatio = photoLoadedBox.width / photoLoadedBox.height;

      console.log('Aspect ratios:', {
        scan: scanAspectRatio,
        photo: photoAspectRatio,
        expected: 0.75,
      });

      expect(scanAspectRatio).toBeCloseTo(0.75, 1);
      expect(photoAspectRatio).toBeCloseTo(0.75, 1);

      // If we captured loading dimensions, verify they match loaded dimensions
      if (scanLoadingVisible || photoLoadingVisible) {
        const scanContainerLoadingBox = await scanContainerLoading.boundingBox();
        if (scanContainerLoadingBox) {
          expect(scanLoadedBox.width).toBeCloseTo(scanContainerLoadingBox.width, 1);
          expect(scanLoadedBox.height).toBeCloseTo(scanContainerLoadingBox.height, 1);
        }
      }
    }
  });

  test('no layout shift when data loads', async ({ page }) => {
    // Find the grid
    const grid = page.locator('[data-testid="receipt-bounding-box-grid"]');
    await expect(grid).toBeVisible();
    
    // Scroll to the grid to ensure it's in view
    await grid.scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);

    // Get initial grid position and size
    const initialGridBox = await grid.boundingBox();
    expect(initialGridBox).not.toBeNull();

    // Wait for content to load
    await page.waitForSelector('svg', { timeout: 10000 });

    // Get grid position and size after loading
    const finalGridBox = await grid.boundingBox();
    expect(finalGridBox).not.toBeNull();

    if (initialGridBox && finalGridBox) {
      // Verify grid didn't shift position
      expect(finalGridBox.x).toBeCloseTo(initialGridBox.x, 0);
      expect(finalGridBox.y).toBeCloseTo(initialGridBox.y, 0);
      
      // Verify grid size didn't change
      expect(finalGridBox.width).toBeCloseTo(initialGridBox.width, 1);
      expect(finalGridBox.height).toBeCloseTo(initialGridBox.height, 1);
    }
  });

  test('both bounding boxes maintain 3:4 aspect ratio', async ({ page }) => {
    // Scroll to the grid first
    const grid = page.locator('[data-testid="receipt-bounding-box-grid"]');
    await grid.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1000); // Wait for intersection observer
    
    const scanCell = page.locator('[data-testid="scan-receipt-cell"]');
    const photoCell = page.locator('[data-testid="photo-receipt-cell"]');
    
    // Wait for SVG content to load
    await scanCell.locator('svg').first().waitFor({ timeout: 15000, state: 'attached' });
    await photoCell.locator('svg').first().waitFor({ timeout: 15000, state: 'attached' });

    // Get the frame containers - structure: gridCell > div (ReceiptBoundingBoxFrame outer) > div (aspect ratio container)
    const scanFrame = scanCell.locator('div').first().locator('div').first();
    const photoFrame = photoCell.locator('div').first().locator('div').first();

    // Wait a moment for frames to render
    await page.waitForTimeout(500);

    // Wait for frames to be visible and have dimensions
    await expect(scanFrame).toBeVisible({ timeout: 10000 });
    await expect(photoFrame).toBeVisible({ timeout: 10000 });
    
    // Wait a bit more for layout to stabilize
    await page.waitForTimeout(1000);

    const scanFrameBox = await scanFrame.boundingBox();
    const photoFrameBox = await photoFrame.boundingBox();

    console.log('Frame bounding boxes:', {
      scan: scanFrameBox,
      photo: photoFrameBox,
    });

    expect(scanFrameBox).not.toBeNull();
    expect(photoFrameBox).not.toBeNull();

    if (scanFrameBox && photoFrameBox) {
      const scanAspectRatio = scanFrameBox.width / scanFrameBox.height;
      const photoAspectRatio = photoFrameBox.width / photoFrameBox.height;

      console.log('Aspect ratios:', {
        scan: scanAspectRatio,
        photo: photoAspectRatio,
        expected: 0.75,
      });

      // Both should have 3:4 aspect ratio (0.75)
      expect(scanAspectRatio).toBeCloseTo(0.75, 1);
      expect(photoAspectRatio).toBeCloseTo(0.75, 1);

      // Both should have the same dimensions
      expect(scanFrameBox.width).toBeCloseTo(photoFrameBox.width, 1);
      expect(scanFrameBox.height).toBeCloseTo(photoFrameBox.height, 1);
    }
  });
});
