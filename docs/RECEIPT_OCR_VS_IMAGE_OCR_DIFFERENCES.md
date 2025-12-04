# Receipt-Level OCR vs Image-Level OCR Differences

## Overview

Receipt-level OCR is created by cropping the receipt from the original image and running OCR on the cropped image for better accuracy. However, this process introduces differences beyond just coordinate geometry.

## Key Differences

### 1. **Missing Lines** (14 lines)

**Image-level OCR**: 156 lines
**Receipt-level OCR**: 142 lines
**Missing**: 14 lines (line_ids: 143-156)

**Missing lines characteristics:**
- X-coordinate range: 0.51-0.83 (normalized)
- X-mean: 0.67 (right side of image)
- Content: Footer/advertisement text like:
  - "Pro Xtra"
  - "Credit Card."
  - "Apply and SAVE UP TO $100."
  - "RETURN POLICY DEFINITIONS"
  - Date/time stamps

**Why missing?**
- These lines are likely outside the receipt bounding box
- Or not detected in the cropped OCR due to different image quality/scale
- Footer/ad text may be filtered out during receipt processing

### 2. **Text Content Differences**

OCR on the cropped image reads text differently than the full image OCR:

**Examples:**
- Line 1: "How dors." (image) vs "How doris." (receipt)
- Line 2: "getmore -we" (image) vs "getmore .." (receipt)
- Line 3: "2745 TELLER RD.I.0." (image) vs "2745 TELLER RD.T.O." (receipt)
- Line 4: ".CA. 91320(805) 3756660" (image) vs "..CA. 91320(805) 3756680" (receipt)

**Impact on clustering:**
- The `evaluate_receipt_completeness()` function uses text content to score clusters
- Different text affects merchant detection, address detection, phone detection
- This can change whether clusters merge or not

### 3. **Confidence Scores**

Confidence scores may differ slightly between image-level and receipt-level OCR, though typically within 0.01.

### 4. **Angle Measurements**

Angle measurements may differ slightly, though typically within 0.5 degrees.

## Impact on Clustering

The clustering algorithm uses:

1. **X-coordinates** (for DBSCAN) - Should be similar after transformation
2. **Text content** (for completeness scoring) - **DIFFERENT** (affects merging)
3. **Number of lines** (for completeness scoring) - **DIFFERENT** (14 missing lines)
4. **Y-coordinates** (for spatial analysis) - Should be similar after transformation
5. **Angles** (for angle consistency splitting) - Slightly different

### Why Receipt-Level OCR Produces More Clusters

1. **Missing lines** change cluster composition
   - 14 lines missing from receipt-level OCR
   - These lines might have helped merge clusters in image-level OCR

2. **Different text** affects completeness scoring
   - `evaluate_receipt_completeness()` checks for merchant, address, phone, total
   - Different text can change these detection results
   - Lower completeness scores prevent merging

3. **Different cluster sizes** affect merging logic
   - `should_apply_smart_merging()` checks average lines per cluster
   - Missing lines change this calculation

## Recommendations

### Option 1: Use Image-Level OCR for Clustering

Use receipt-level OCR only for visualization/accuracy, but cluster using image-level OCR:

```python
# Cluster with image-level OCR (gets 2 clusters)
image_lines = client.list_lines_from_image(image_id)
cluster_dict = recluster_receipt_lines(image_lines, ...)

# Then use receipt-level OCR for visualization
receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
# Transform receipt_lines to image space for visualization
```

### Option 2: Fill Missing Lines

When using receipt-level OCR, add back the missing image-level lines:

```python
# Get all image-level lines
all_image_lines = client.list_lines_from_image(image_id)

# Get receipt-level lines and transform to image space
receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
transformed_receipt_lines = [transform_receipt_line_to_image_line(...) for ...]

# Create map of receipt line_ids
receipt_line_ids = {line.line_id for line in receipt_lines}

# Use receipt-level where available, image-level for missing
for img_line in all_image_lines:
    if img_line.line_id in receipt_line_ids:
        # Use transformed receipt-level (more accurate)
    else:
        # Use image-level (for missing lines)
```

### Option 3: Adjust Clustering Parameters

Since receipt-level OCR has:
- Different text (affects completeness scoring)
- Fewer lines (affects cluster sizes)

You may need to:
- Lower `merge_min_score` (0.3-0.4 instead of 0.5)
- Increase `merge_x_proximity_threshold` (0.5-0.6 instead of 0.4)
- Adjust other parameters to compensate

## Current Implementation

The current `visualize_final_clusters_cropped.py` script uses **Option 2** (hybrid approach):
- Uses receipt-level OCR where available (142 lines)
- Falls back to image-level OCR for missing lines (14 lines)
- Total: 156 lines for clustering

However, this still produces 7 clusters instead of 2, likely because:
1. The different text content affects completeness scoring
2. The missing lines were important for merging in image-level OCR
3. The transformed coordinates, while correct, may have slight differences that affect clustering

## Conclusion

**What's changing besides geometry:**
1. ✅ **Text content** - OCR reads text differently on cropped image
2. ✅ **Number of lines** - 14 lines missing from receipt-level OCR
3. ✅ **Completeness scores** - Affected by different text and missing lines
4. ✅ **Cluster sizes** - Affected by missing lines
5. ⚠️ **Confidence scores** - Slightly different (usually < 0.01)
6. ⚠️ **Angles** - Slightly different (usually < 0.5°)

The **text content differences** and **missing lines** are the primary drivers of different clustering results, not just geometry.

