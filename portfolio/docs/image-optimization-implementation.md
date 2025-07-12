# Image Optimization Implementation

## Overview

This document describes the implementation of multiple image sizes for optimized loading performance in the portfolio application. The optimization reduces bandwidth usage by serving appropriately sized images based on display needs.

## Changes Made

### 1. Backend Image Processing

#### Updated `upload_all_cdn_formats()` in `receipt_upload/utils.py`
- Added `generate_image_sizes()` function to create multiple sizes while maintaining aspect ratio
- Enhanced `upload_all_cdn_formats()` to generate:
  - **Thumbnail**: 300px max dimension (2x the 150px display size)
  - **Small**: 600px max dimension (4x display size for retina)
  - **Medium**: 1200px max dimension (for larger previews)
  - **Full**: Original size (for detailed viewing)
- Each size is generated in JPEG, WebP, and AVIF formats

### 2. Database Schema Updates

#### Enhanced DynamoDB Entities
Both `Image` and `Receipt` entities now include fields for each size variant:
- `cdn_thumbnail_s3_key`, `cdn_thumbnail_webp_s3_key`, `cdn_thumbnail_avif_s3_key`
- `cdn_small_s3_key`, `cdn_small_webp_s3_key`, `cdn_small_avif_s3_key`
- `cdn_medium_s3_key`, `cdn_medium_webp_s3_key`, `cdn_medium_avif_s3_key`

### 3. Processing Pipeline Updates

#### Updated `process_photo()` and `process_native()`
- Modified to use `generate_thumbnails=True` when calling `upload_all_cdn_formats()`
- Updated entity creation to populate all new thumbnail fields

### 4. Frontend Optimization

#### Enhanced `getBestImageUrl()` in `utils/imageFormat.ts`
- Added `ImageSize` type: `'thumbnail' | 'small' | 'medium' | 'full'`
- Updated function to accept size parameter (defaults to 'full')
- Intelligently selects the best format (AVIF > WebP > JPEG) for the requested size

#### Updated Components
- **ImageStack**: Uses 'thumbnail' size (images displayed at 150px)
- **ReceiptStack**: Uses 'thumbnail' size (receipts displayed at 100px)
- Both components implement progressive fallback on error (thumbnail → small → medium → full)

## Benefits

1. **90%+ Bandwidth Reduction**: Serving 300px thumbnails instead of 4000px originals
2. **Faster Page Loads**: Smaller initial image downloads
3. **Better UX**: Quick-loading thumbnails with option to view full resolution
4. **Cost Savings**: Reduced S3 bandwidth costs
5. **Maintains Quality**: High-quality Lanczos resampling preserves image clarity

## Migration Strategy

For existing images without thumbnails:
1. The frontend gracefully falls back to full-size images
2. A migration script can be created to process existing images
3. New uploads automatically generate all sizes

## Testing

The implementation has been tested and builds successfully with no TypeScript errors. Components handle missing thumbnail fields gracefully with progressive fallback.