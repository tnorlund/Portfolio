# Split Receipt Script Capabilities

## Overview

The **correct script** for the full workflow is: **`scripts/split_receipt.py`**

This script handles the complete process of splitting a receipt into multiple receipts, matching the original upload workflow.

## Capabilities

### ✅ 1. Creates New Receipt Records
- **Function**: `create_split_receipt_records()` (line 403)
- **Creates**: `Receipt` entity with all required fields:
  - Receipt ID, width, height
  - Timestamp
  - S3 buckets and keys (raw and CDN)
  - Corner coordinates (TL, TR, BL, BR)
  - SHA256 hash
  - All CDN format keys (JPEG, WebP, AVIF, thumbnails, small, medium)

### ✅ 2. Creates ReceiptLines
- **Function**: `create_split_receipt_records()` (line 757)
- **Creates**: `ReceiptLine` entities for all lines in the cluster
- **Transforms coordinates**: From original receipt space to new receipt space
- **Preserves**: Text, angles, confidence, geometry

### ✅ 3. Creates ReceiptWords
- **Function**: `create_split_receipt_records()` (line 758)
- **Creates**: `ReceiptWord` entities for all words in the cluster
- **Transforms coordinates**: From original receipt space to new receipt space
- **Preserves**: Text, angles, confidence, geometry
- **Maps IDs**: Creates word_id_map for label migration

### ✅ 4. Creates ReceiptLetters
- **Function**: `create_split_receipt_records()` (line 759)
- **Creates**: `ReceiptLetter` entities for all letters in the cluster
- **Preserves**: Text, angles, confidence, geometry

### ✅ 5. Creates NDJSON Files for S3
- **Function**: `export_receipt_ndjson_to_s3()` (line 851)
- **Exports**:
  - `lines.ndjson` - All ReceiptLine entities
  - `words.ndjson` - All ReceiptWord entities
- **Location**: `s3://artifacts-bucket/receipts/{image_id}/receipt-{receipt_id:05d}/`
- **Format**: Matches upload workflow pattern

### ✅ 6. Creates Embeddings and CompactionRun Records
- **Function**: `create_embeddings_and_compaction_run()` (line 907)
- **Creates**:
  - ChromaDB embeddings (lines and words)
  - Delta files uploaded to S3
  - `CompactionRun` record in DynamoDB
- **Triggers**: Compaction via DynamoDB streams (same as upload workflow)
- **Waits**: For compaction to complete before adding labels

### ✅ 7. Creates CDN Images
- **Function**: `upload_all_cdn_formats()` (called in `create_split_receipt_records`)
- **Creates**: All CDN image formats:
  - JPEG (full, thumbnail, small, medium)
  - WebP (full, thumbnail, small, medium)
  - AVIF (full, thumbnail, small, medium)
- **Process**:
  1. Crops receipt from original image
  2. Applies affine transformation (perspective correction)
  3. Uploads all formats to CDN bucket
  4. Sets all CDN keys in Receipt entity

### ✅ 8. Migrates ReceiptWordLabels
- **Function**: `migrate_receipt_word_labels()` (line 765)
- **Maps**: Old line_id/word_id to new line_id/word_id
- **Creates**: New `ReceiptWordLabel` entities with updated IDs
- **Adds**: Labels AFTER compaction completes (correct order)

## Clustering Logic

### ✅ Uses Correct Two-Phase Clustering
- **Function**: `recluster_receipt_lines()` (line 148)
- **Uses**: Same logic as `visualize_final_clusters_cropped.py`
- **Phases**:
  1. X-axis DBSCAN clustering
  2. Angle-based splitting
  3. X-proximity reassignment
  4. Vertical proximity reassignment
  5. Smart merging (with combine agent logic)
  6. Join overlapping clusters
  7. Post-processing (assign noise lines to nearest cluster)

### ✅ Uses Image-Level Lines for Clustering
- **Input**: `image_lines` (all 156 lines from image-level OCR)
- **Ensures**: Correct clustering (2 clusters for problematic images)
- **Includes**: All lines, even noise lines

## Modularity

### ✅ Modular Functions
- `recluster_receipt_lines()` - Clustering logic
- `create_split_receipt_records()` - Creates all entities
- `create_split_receipt_image()` - Creates receipt image
- `migrate_receipt_word_labels()` - Migrates labels
- `export_receipt_ndjson_to_s3()` - Exports NDJSON
- `create_embeddings_and_compaction_run()` - Creates embeddings
- `save_records_locally()` - Saves for rollback

## Order of Operations

1. **Load data** from DynamoDB
2. **Re-cluster** using two-phase approach
3. **Create receipt records** (Receipt, ReceiptLine, ReceiptWord, ReceiptLetter)
4. **Create receipt images** and upload to CDN
5. **Save locally** (for rollback)
6. **Save to DynamoDB** (receipts, lines, words, letters - NOT labels yet)
7. **Export NDJSON** to S3
8. **Create embeddings** and CompactionRun records
9. **Wait for compaction** to complete
10. **Add labels** (after embeddings exist in ChromaDB)

## Comparison with Upload Workflow

### ✅ Matches Upload Workflow
- Same NDJSON format and location
- Same embedding process (direct embedding + CompactionRun)
- Same CDN image formats and upload process
- Same coordinate transformation logic
- Same order of operations (embeddings before labels)

### ✅ Additional Features
- Local save for rollback
- Label migration (maps old IDs to new IDs)
- Preserves original receipt (for rollback)

## Summary

**`split_receipt.py`** is the correct, complete script that:
- ✅ Is modular (separate functions for each step)
- ✅ Creates all required entities (Receipt, ReceiptLine, ReceiptWord, ReceiptLetter)
- ✅ Creates NDJSON files for S3
- ✅ Creates embeddings and CompactionRun records
- ✅ Creates all CDN image formats
- ✅ Matches the original upload workflow
- ✅ Uses the correct clustering logic (same as `visualize_final_clusters_cropped.py`)

