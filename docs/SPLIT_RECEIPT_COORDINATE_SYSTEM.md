# Split Receipt Coordinate System

## Coordinate System Consistency

The split receipt script uses **OCR space** (y=0 at bottom) for Receipt corner coordinates, which matches:
- **Scan processing**: Uses OCR space (`"y": 1 - box_4_ordered[0][1] / image.height`)
- **Native processing**: Uses OCR space (`top_left={"x": 0.0, "y": 1.0}`)

**Note**: Photo processing uses **image space** (y=0 at top), but since we're splitting from existing receipts (which are typically from scans), we use OCR space to match the majority case.

## Padding

**Removed padding** to match upload process:
- **Scan**: No padding (uses exact corners from geometry)
- **Photo**: No padding in normal case (only 10px fixed in fallback)
- **Split script**: No padding (matches upload process)

Previously, the split script used 2% padding (`image_width * 0.02`), but this has been removed to match the upload workflow.

## Image Dimensions

The script now:
1. Downloads the actual image from S3
2. Uses actual image dimensions (`image.width`, `image.height`) for all calculations
3. Verifies dimensions match DynamoDB (warns if mismatch)
4. Uses actual dimensions for coordinate conversion and cropping

This ensures accurate transformations even if DynamoDB dimensions differ from the actual image.

## Coordinate Flow

1. **ReceiptWord coordinates**: Normalized (0-1) relative to original receipt, in OCR space
2. **Convert to absolute**: Use original receipt bounds + actual image dimensions
3. **Calculate new bounds**: From word coordinates in absolute image space
4. **Store in Receipt**: Normalized (0-1) relative to original image, in OCR space
5. **Crop image**: Convert OCR space to image space (y=0 at top) for PIL

## Verification

The coordinate system matches the upload process for scan/native receipts. Photo receipts use a different coordinate system, but since we're splitting existing receipts (typically scans), OCR space is the correct choice.

