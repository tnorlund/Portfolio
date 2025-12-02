# Coordinate Transform Debug

## Issue Summary

1. **Giant receipt 3 line at top**: Coordinates with y > 1.0 are being stored, indicating lines outside receipt bounds
2. **Bounding boxes only align in original image coordinate space**: Suggests coordinates might be in absolute image coordinates, not receipt-relative

## Coordinate Systems

1. **OCR Space**: y=0 at bottom, y=1 at top (normalized) or y=height at top (absolute)
2. **PIL/Image Space**: y=0 at top, y=height at bottom (absolute pixels)
3. **Receipt Bounds**: Normalized (0-1) relative to image, in OCR space
4. **ReceiptLine/ReceiptWord coordinates**: Should be normalized (0-1) relative to receipt bounds, in OCR space

## Current State

- Receipt 3 has lines with y coordinates > 1.0 (e.g., 1.008)
- This means they're outside the receipt bounds or stored incorrectly
- Visualization script now clamps coordinates to [0, 1] to handle this

## Transform Flow in split_receipt.py

1. **Receipt.get_transform_to_image()**: Returns transform coefficients (receipt space -> image space)
2. **warp_transform with flip_y=True**: Converts from receipt OCR space to image PIL space (normalized 0-1)
3. **Convert to absolute pixels**: Multiply by image_width/height (PIL space, y=0 at top)
4. **img_to_receipt_coord**: Converts PIL -> OCR, then normalizes relative to receipt bounds

## Problem

Some coordinates end up > 1.0, suggesting:
- Lines are outside receipt bounds after transformation
- Normalization is incorrect
- Coordinate system mismatch

## Next Steps

1. Verify that `warp_transform` output is correct
2. Check that `img_to_receipt_coord` normalization is working correctly
3. Ensure coordinates are always clamped to [0, 1] when storing
4. Consider if coordinates should be stored in absolute image coordinates instead

