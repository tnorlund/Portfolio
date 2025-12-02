# Coordinate Transform Review

## Coordinate Systems

1. **OCR Space**: y=0 at bottom, y=1 at top (normalized) or y=height at top (absolute)
2. **PIL/Image Space**: y=0 at top, y=height at bottom (absolute pixels)
3. **Receipt Bounds**: Normalized (0-1) relative to image, in OCR space
4. **ReceiptLine/ReceiptWord coordinates**: Normalized (0-1) relative to receipt bounds, in OCR space

## Transform Flow in split_receipt.py

1. **Receipt.get_transform_to_image()** (receipt.py:288-385):
   - Input: Receipt corners in OCR space (normalized 0-1, y=0 at bottom)
   - Flips Y: `(1 - self.top_left["y"]) * image_height` - OCR -> PIL
   - Returns: Transform coefficients (receipt space -> image space)

2. **warp_transform with flip_y=True** (split_receipt.py:486-493):
   - Input: ReceiptLine coordinates in OCR space (normalized 0-1 relative to receipt)
   - `flip_y=True` converts from OCR space (y=0 at bottom) to PIL space (y=0 at top)
   - Output: Coordinates normalized (0-1) in PIL space

3. **Convert to absolute image coordinates** (split_receipt.py:497-512):
   - `line_tl_img["y"] = line_copy.top_left["y"] * image_height`
   - This is in PIL space (y=0 at top)

4. **img_to_receipt_coord** (split_receipt.py:452-458):
   - `ocr_y = image_height - img_y` - Flips Y: PIL -> OCR
   - Then normalizes relative to new receipt bounds
   - Output: Coordinates in OCR space (normalized 0-1 relative to new receipt)

## Visualization Flow in visualize_split_receipts_with_boxes.py

1. **Receipt bounds conversion** (line 166):
   - `receipt_top_y_img = image_height - receipt_tl['y'] * image_height`
   - Flips Y: OCR -> PIL (correct)

2. **Line coordinates conversion** (line 198):
   - `line_tl_y = receipt_top_y_img + (1 - tl['y']) * receipt_height_abs`
   - Assumes `tl['y']` is in OCR space (y=0 at bottom of receipt, y=1 at top)
   - Converts to PIL space by: `receipt_top_y_img + (1 - tl['y']) * receipt_height_abs`
   - This is correct IF the coordinates are in OCR space

## Issue

The visualization script assumes ReceiptLine coordinates are in OCR space (y=0 at bottom of receipt).
But after `warp_transform` with `flip_y=True`, the coordinates are in PIL space.
Then `img_to_receipt_coord` flips them back to OCR space.

So the coordinates stored in DynamoDB should be in OCR space, which matches what the visualization expects.

**The problem might be that we're flipping Y twice in the visualization:**
1. Once when converting receipt bounds: `receipt_top_y_img = image_height - receipt_tl['y'] * image_height`
2. Once when converting line coordinates: `line_tl_y = receipt_top_y_img + (1 - tl['y']) * receipt_height_abs`

But wait - the `(1 - tl['y'])` is NOT a Y-flip, it's just converting from OCR space (where y=1 is top) to a coordinate system where we add to the top position.

Actually, the formula `receipt_top_y_img + (1 - tl['y']) * receipt_height_abs` is correct:
- If `tl['y'] = 1.0` (top of receipt in OCR), then `(1 - 1.0) = 0`, so `line_tl_y = receipt_top_y_img` (top)
- If `tl['y'] = 0.0` (bottom of receipt in OCR), then `(1 - 0.0) = 1`, so `line_tl_y = receipt_top_y_img + receipt_height_abs` (bottom)

So the visualization logic seems correct. The issue might be that the coordinates in DynamoDB are not actually in OCR space as expected.

