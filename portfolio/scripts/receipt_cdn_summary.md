# Receipt Files CDN Analysis Summary

## Overview

The CDN bucket (`sitebucket-778abc9`) contains receipt files that follow a specific naming pattern. Based on the user's comment that "the receipt images were saved as the 'image' images", it appears these receipts were extracted from larger images containing multiple receipts.

## Key Findings

### File Statistics
- **Total receipt files**: 1,450
- **Unique base images**: 633 (images that have receipts extracted from them)
- **Total storage**: 0.81 GB
- **Average file size**: 0.57 MB

### Naming Pattern
Receipt files follow this standard pattern:
```
assets/{UUID}_RECEIPT_{NUMBER}.{EXTENSION}
```

Example: `assets/00ccdf00-cd34-464e-a826-b6521e3a1174_RECEIPT_00001.png`

### Format Distribution
- **JPG**: 420 files (29.0%)
- **PNG**: 395 files (27.2%)
- **AVIF**: 318 files (21.9%)
- **WebP**: 317 files (21.9%)

### Multiple Receipts Per Image
Many base images contain multiple receipts:
- **296 images**: Have 1 receipt
- **77 images**: Have 2 receipts
- **185 images**: Have 3 receipts
- **6 images**: Have 5 receipts
- **68 images**: Have 6 receipts
- **1 image**: Has 7 receipts

Total: 337 images have multiple receipts extracted from them.

### Key Observations

1. **All base images have their originals**: Every receipt file has a corresponding original image file (without the `_RECEIPT_` suffix) in the CDN.

2. **Modern format support**: The presence of AVIF and WebP formats (43.8% combined) indicates modern image optimization has been applied.

3. **Receipt extraction pattern**: The `_RECEIPT_00001`, `_RECEIPT_00002` numbering suggests an automated extraction process that identifies and crops individual receipts from images containing multiple receipts.

4. **Non-standard patterns**: Only 1 non-standard pattern was found:
   - `assets/0f9ebb93-b4b1-47ed-b958-1762bb9b1a18_RECEIPT_00001_RECEIPT_WINDOW_TOP_LEFT.jpg`
   
   This suggests a special case where additional metadata about the receipt's position was included in the filename.

## Recommendations

1. **Consistent Naming**: The receipt files follow a very consistent naming pattern, which is good for programmatic access.

2. **Format Optimization**: The presence of modern formats (AVIF, WebP) alongside traditional formats (PNG, JPG) provides good browser compatibility while optimizing for performance.

3. **Storage Efficiency**: With an average size of 0.57 MB per receipt, the storage is reasonably efficient for high-quality receipt images.

## Scripts Created

1. `simple_s3_investigation.py` - Initial S3 bucket investigation
2. `analyze_receipt_patterns.py` - Detailed receipt file analysis

These scripts can be reused for future CDN analysis or monitoring.