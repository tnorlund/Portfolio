import type { Line, Receipt, Point } from "../../../../types/api";

export interface CropViewBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Computes a 3:4 aspect ratio crop viewBox that contains all receipt content
 * (lines and receipt polygons) while ensuring no content is clipped.
 *
 * @param lines - Array of OCR lines with normalized coordinates (y=0 at bottom)
 * @param receipts - Array of receipt polygons with normalized coordinates (y=0 at bottom)
 * @param imageWidth - Full image width in pixels
 * @param imageHeight - Full image height in pixels
 * @returns Crop viewBox object or null if no content
 */
export function computeSmartCropViewBox(
  lines: Line[],
  receipts: Receipt[],
  imageWidth: number,
  imageHeight: number
): CropViewBox | null {
  // Collect all corner points from lines
  const allPoints: Point[] = [];
  
  lines.forEach((line) => {
    allPoints.push(
      line.top_left,
      line.top_right,
      line.bottom_left,
      line.bottom_right
    );
  });

  // Collect all corner points from receipts
  receipts.forEach((receipt) => {
    allPoints.push(
      receipt.top_left,
      receipt.top_right,
      receipt.bottom_left,
      receipt.bottom_right
    );
  });

  if (allPoints.length === 0) {
    return null;
  }

  // Find bounding box in normalized coordinates (y=0 at bottom)
  const minX = Math.min(...allPoints.map((p) => p.x));
  const maxX = Math.max(...allPoints.map((p) => p.x));
  const minY = Math.min(...allPoints.map((p) => p.y));
  const maxY = Math.max(...allPoints.map((p) => p.y));

  // Convert to pixel coordinates
  // Note: Y coordinates need to be flipped (OCR has y=0 at bottom, SVG has y=0 at top)
  const contentMinX = minX * imageWidth;
  const contentMaxX = maxX * imageWidth;
  const contentMinY = (1 - maxY) * imageHeight; // Flip Y: OCR maxY becomes SVG minY
  const contentMaxY = (1 - minY) * imageHeight; // Flip Y: OCR minY becomes SVG maxY

  const contentWidth = contentMaxX - contentMinX;
  const contentHeight = contentMaxY - contentMinY;

  // Add padding (5% on each side) to ensure content isn't right at the edge
  const paddingX = contentWidth * 0.05;
  const paddingY = contentHeight * 0.05;
  const paddedMinX = Math.max(0, contentMinX - paddingX);
  const paddedMaxX = Math.min(imageWidth, contentMaxX + paddingX);
  const paddedMinY = Math.max(0, contentMinY - paddingY);
  const paddedMaxY = Math.min(imageHeight, contentMaxY + paddingY);

  const paddedWidth = paddedMaxX - paddedMinX;
  const paddedHeight = paddedMaxY - paddedMinY;

  // Target aspect ratio is 3:4 (width:height)
  const targetAspectRatio = 3 / 4;

  // Compute the crop box that contains the content and has 3:4 aspect ratio
  let cropWidth: number;
  let cropHeight: number;
  let cropX: number;
  let cropY: number;

  const paddedAspectRatio = paddedWidth / paddedHeight;

  if (paddedAspectRatio > targetAspectRatio) {
    // Content is wider than target - fit to width, expand height
    cropWidth = paddedWidth;
    cropHeight = paddedWidth / targetAspectRatio;
    cropX = paddedMinX;
    // Center vertically
    cropY = (paddedMinY + paddedMaxY) / 2 - cropHeight / 2;
  } else {
    // Content is taller than target - fit to height, expand width
    cropHeight = paddedHeight;
    cropWidth = paddedHeight * targetAspectRatio;
    cropY = paddedMinY;
    // Center horizontally
    cropX = (paddedMinX + paddedMaxX) / 2 - cropWidth / 2;
  }

  // Clamp to image bounds
  cropX = Math.max(0, Math.min(cropX, imageWidth - cropWidth));
  cropY = Math.max(0, Math.min(cropY, imageHeight - cropHeight));
  cropWidth = Math.min(cropWidth, imageWidth - cropX);
  cropHeight = Math.min(cropHeight, imageHeight - cropY);

  return { x: cropX, y: cropY, width: cropWidth, height: cropHeight };
}
