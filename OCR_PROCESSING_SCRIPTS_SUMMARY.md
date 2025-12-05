# OCR Processing Scripts - Evolution & Current State

## Overview

This document summarizes the OCR re-processing scripts that have been iteratively developed to fix bounding box issues in receipt data.

## Key Finding

**495 words with labels have the same bounding boxes as their respective lines** - these words could not be matched in the OCR update because they have incorrect (line-level) bounding boxes instead of word-level bounding boxes.

**File:** `/tmp/words_with_matching_line_bboxes.json` (698.9 KB)

## Script Files

### 1. `dev.apply_new_ocr_to_image.py` (64 KB, ~1517 lines)
**Purpose:** Main script for applying new OCR results to a specific image and its receipts.

**Key Features:**
- Downloads raw image from S3
- Runs Swift OCR engine (fixed version with `boundingBox(for: wordRange)`)
- Hierarchical matching: Lines → Words → Letters
- Updates Word, Letter, ReceiptWord, ReceiptLetter entities
- Rollback functionality (saves old geometry to JSON)
- Angle calculation from corners

**Matching Strategy (Hierarchical):**
1. **Line Matching:** Uses centroid containment (`is_point_in_bbox`) - OCR line centroid must be within existing line bbox
2. **Word Matching (within matched lines):**
   - Sorts by `word_id` (left-to-right order)
   - First tries exact position + text match
   - Then searches ±3 positions for text match
   - Falls back to position-only match
3. **Letter Matching (within matched words):**
   - Constrained to matched OCR words
   - Sorts by `letter_id` (left-to-right order)
   - First tries exact position + text match
   - Then searches ±2 positions for text match
   - Falls back to position-only match

**Key Functions:**
- `match_words_hierarchical()` - Hierarchical word matching
- `match_letters_hierarchical()` - Hierarchical letter matching
- `calculate_angle_from_corners()` - Computes angle from top_left/top_right
- `update_word_geometry()` / `update_receipt_word_geometry()` - Updates with new OCR data
- `rollback_from_results()` - Restores old geometry from saved JSON

**Evolution Notes:**
- Initially used location-first matching (IoU-based)
- Evolved to hierarchical matching for better accuracy with duplicates
- Fixed word ordering issues (was using sliding window, now uses direct position-based)
- Added angle calculation to match upload process
- Preserves all non-geometry fields (`extracted_data`, `embedding_status`, `is_noise`, etc.)

### 2. `dev.update_all_receipts_ocr.py` (14 KB, ~372 lines)
**Purpose:** Batch processing script to update OCR for all receipts in DynamoDB.

**Key Features:**
- Lists all receipts from DynamoDB
- Processes each receipt individually
- Reuses functions from `dev.apply_new_ocr_to_image.py`
- Supports dry-run mode
- Saves results for rollback
- Can run in background with `nohup`

**Usage:**
```bash
# Dry run on first 10 receipts
python dev.update_all_receipts_ocr.py --limit 10

# Process all receipts
python dev.update_all_receipts_ocr.py --limit 0 --update --yes

# Background process
nohup python dev.update_all_receipts_ocr.py --limit 0 --update --yes --save-results /tmp/results.json > /tmp/log.log 2>&1 &
```

**Results:**
- Processed 509 receipts
- Generated `/tmp/all_receipts_ocr_update_results.json` (624 MB)
- 254 receipts had unmatched words
- 495 unmatched words have labels (all with matching line bboxes)

### 3. `dev.migrate_ocr_coordinates.py` (88 KB, ~2077 lines)
**Purpose:** Older migration script (likely from earlier iterations).

**Status:** Appears to be an earlier version - check if still needed or can be archived.

## Supporting Scripts

### 4. `dev.download_receipt_data.py`
**Purpose:** Download all receipt data (lines, words, labels) to local JSON files for analysis.

**Output:** `/tmp/receipt_data/` directory with:
- Individual receipt files: `receipt_{receipt_id}_image_{image_id}.json`
- Summary file: `summary.json`

**Usage:**
```bash
python dev.download_receipt_data.py --output-dir /tmp/receipt_data
```

### 5. `dev.analyze_unmatched_words_bboxes.py`
**Purpose:** Analyze unmatched words with labels and compare their bounding boxes with their lines.

**Key Finding:** 100% of unmatched words with labels (495/495) have matching line bounding boxes.

**Output:** `/tmp/unmatched_words_bbox_analysis.json`

### 6. `dev.check_unmatched_words_labels.py`
**Purpose:** Check if unmatched words have ReceiptWordLabel records.

**Status:** Has a bug (tries to access `confidence` attribute that doesn't exist) - needs fix.

## Current Issues

### 1. Words with Line-Level Bounding Boxes
- **495 words** have labels but couldn't be matched
- **100% of these** have the same bounding box as their line
- These words need their bounding boxes fixed using new OCR data

### 2. Matching Strategy
The hierarchical matching works well for receipts (100% match rate) but:
- Image-level matching has lower success rate (due to complexity/scale)
- Some words with incorrect bboxes can't be matched

## Next Steps

1. **Fix the 495 words with line-level bboxes:**
   - Create a script to specifically target these words
   - Use text matching + line context to find correct OCR word
   - Update bounding boxes while preserving labels

2. **Improve matching for edge cases:**
   - Handle words with incorrect bboxes more gracefully
   - Consider text-only fallback for words that can't be matched geometrically

3. **Clean up old scripts:**
   - Review `dev.migrate_ocr_coordinates.py` - archive if obsolete
   - Fix `dev.check_unmatched_words_labels.py` bug

## Data Files

- `/tmp/words_with_matching_line_bboxes.json` - The 495 problematic words
- `/tmp/all_receipts_ocr_update_results.json` - OCR update results (624 MB)
- `/tmp/receipt_data/` - Downloaded receipt data for analysis
- `/tmp/unmatched_words_bbox_analysis.json` - Bounding box analysis

## Timeline

All scripts were last modified: **2025-12-01 07:34:02**

The evolution shows:
1. Initial location-first matching (IoU-based)
2. Hierarchical matching (lines → words → letters)
3. Position-based matching with text validation
4. Angle calculation fixes
5. Rollback functionality
6. Batch processing capabilities





