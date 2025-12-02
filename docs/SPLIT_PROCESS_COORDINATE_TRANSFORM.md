# Split Process Coordinate Transform

## Split Process Flow

1. **Start**: Original Receipt 1 with ReceiptLines and ReceiptWords
   - ReceiptLines have coordinates normalized (0-1) relative to Receipt 1 bounds, in OCR space
   - Receipt 1 bounds are normalized (0-1) relative to image, in OCR space

2. **Re-clustering**:
   - Load image-level Lines (not ReceiptLines) - these are in absolute image coordinates
   - Cluster these Lines using two-phase approach
   - Result: cluster_dict mapping cluster_id -> List[Line]

3. **Create New Receipts**:
   - For each cluster, filter ReceiptLines/ReceiptWords that match cluster line_ids
   - Calculate new receipt bounds from words in that cluster
   - Transform ReceiptLine/ReceiptWord coordinates from Receipt 1 space to new receipt space
   - Create Receipt 2, Receipt 3, etc.

4. **Coordinate Spaces**:
   - Receipt 1 lines: normalized (0-1) relative to Receipt 1 bounds, OCR space
   - Receipt 2 lines: normalized (0-1) relative to Receipt 2 bounds, OCR space
   - Receipt 3 lines: normalized (0-1) relative to Receipt 3 bounds, OCR space

## Visualization Issue

The visualization needs to transform ALL lines to the ORIGINAL IMAGE coordinate space:
- Receipt 1 lines: receipt-relative -> image-absolute (using Receipt 1 bounds)
- Receipt 2 lines: receipt-relative -> image-absolute (using Receipt 2 bounds)
- Receipt 3 lines: receipt-relative -> image-absolute (using Receipt 3 bounds)

Then convert from OCR space (y=0 at bottom) to PIL space (y=0 at top) for drawing.

## Current Problem

Receipt 2 lines have y > 1.0, indicating they're stored incorrectly (outside receipt bounds).
This suggests the transform in split_receipt.py is not working correctly.

