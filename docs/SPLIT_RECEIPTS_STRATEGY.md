# Strategy: Splitting Receipts Based on Re-Clustering

## Overview

This document outlines the strategy for splitting incorrectly merged receipts into separate receipts based on the two-phase clustering results. This is the inverse operation of `combine_receipts` - we're splitting one receipt into multiple receipts.

## Problem Statement

For the two problem images:
- `13da1048-3888-429f-b2aa-b3e15341da5e`: Currently has 1 receipt, should have 2
- `752cf8e2-cb69-4643-8f28-0ac9e3d2df25`: Currently has 1 receipt, should have 2

We need to:
1. Split the existing receipt into 2 new receipts based on re-clustering
2. Create new Receipt, ReceiptLine, ReceiptWord entities
3. Migrate ReceiptWordLabel entities to the new receipts
4. Export NDJSON and queue for embedding
5. Handle ReceiptMetadata (if exists)

## Strategy

### Phase 1: Load and Validate Current State

1. **Load existing receipt data**:
   - Receipt entity (receipt_id=1)
   - ReceiptLine entities
   - ReceiptWord entities
   - ReceiptWordLabel entities (if any)
   - ReceiptMetadata (if any)

2. **Load image-level OCR data**:
   - Line entities (for re-clustering)
   - Word entities (for mapping)

3. **Re-cluster using two-phase approach**:
   - Use the same clustering logic as upload process
   - Get final cluster assignments (line_id -> new_cluster_id)

4. **Validate clustering results**:
   - Ensure we have 2 clusters (expected)
   - Verify all lines are assigned
   - Check that clusters are spatially separated

### Phase 2: Create New Receipt Records

For each new cluster:

1. **Map lines and words**:
   - Determine which ReceiptLines belong to this cluster
   - Determine which ReceiptWords belong to this cluster
   - Create line_id and word_id mappings (old -> new)

2. **Calculate receipt bounds**:
   - Find bounding box from all words in cluster
   - Calculate normalized coordinates (0-1) relative to image
   - Calculate receipt dimensions

3. **Create Receipt entity**:
   ```python
   Receipt(
       image_id=image_id,
       receipt_id=new_receipt_id,  # 1, 2, etc.
       width=calculated_width,
       height=calculated_height,
       timestamp_added=datetime.now(timezone.utc),
       raw_s3_bucket=raw_bucket,
       raw_s3_key=f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png",
       top_left={...},  # Normalized coordinates
       top_right={...},
       bottom_left={...},
       bottom_right={...},
       # Copy from original: sha256, cdn keys, etc.
   )
   ```

4. **Create ReceiptLine entities**:
   - Re-number line_ids (1, 2, 3, ...)
   - Recalculate coordinates relative to new receipt bounds
   - Preserve text, geometry, confidence

5. **Create ReceiptWord entities**:
   - Re-number word_ids within each line
   - Recalculate coordinates relative to new receipt bounds
   - Preserve text, geometry, confidence

6. **Create ReceiptLetter entities** (if needed):
   - Similar mapping as words

### Phase 3: Migrate Labels

1. **Load existing ReceiptWordLabel entities**:
   - Query all labels for the original receipt

2. **Map labels to new receipts**:
   - For each label, determine which new receipt it belongs to
   - Map word_id and line_id to new IDs
   - Create new ReceiptWordLabel entities:
     ```python
     ReceiptWordLabel(
         image_id=image_id,
         receipt_id=new_receipt_id,
         line_id=new_line_id,
         word_id=new_word_id,
         label=original_label.label,
         reasoning=f"Migrated from receipt {original_receipt_id}, word {original_word_id}",
         timestamp_added=datetime.now(timezone.utc),
         validation_status=original_label.validation_status,
         label_proposed_by=original_label.label_proposed_by or "receipt_split",
         label_consolidated_from=f"receipt_{original_receipt_id}_word_{original_word_id}",
     )
     ```

3. **Handle edge cases**:
   - Labels for words that don't map cleanly (shouldn't happen, but handle gracefully)
   - Labels that span multiple clusters (split or assign to primary cluster)

### Phase 4: Handle Metadata

1. **ReceiptMetadata**:
   - If metadata exists, decide how to split it:
     - Option A: Copy to both receipts (if applicable)
     - Option B: Assign to primary receipt (receipt_id=1)
     - Option C: Create separate metadata for each receipt
   - Recommendation: Copy to both receipts, let merchant validation update

### Phase 5: Export NDJSON and Queue Embedding

For each new receipt:

1. **Export NDJSON to S3**:
   ```python
   prefix = f"receipts/{image_id}/receipt-{receipt_id:05d}/"
   lines_key = prefix + "lines.ndjson"
   words_key = prefix + "words.ndjson"

   # Serialize entities
   line_rows = [dict(line) for line in receipt_lines]
   word_rows = [dict(word) for word in receipt_words]

   # Upload to S3
   s3_client.put_object(
       Bucket=artifacts_bucket,
       Key=lines_key,
       Body=json.dumps(line_rows, default=str),
       ContentType="application/x-ndjson",
   )
   ```

2. **Queue for embedding**:
   ```python
   payload = {
       "image_id": image_id,
       "receipt_id": receipt_id,
       "artifacts_bucket": artifacts_bucket,
       "lines_key": lines_key,
       "words_key": words_key,
   }
   sqs_client.send_message(
       QueueUrl=embed_ndjson_queue_url,
       MessageBody=json.dumps(payload),
   )
   ```

### Phase 6: Cleanup (Optional)

1. **Delete original receipt** (if desired):
   - Delete Receipt entity
   - Delete ReceiptLine entities
   - Delete ReceiptWord entities
   - Delete ReceiptWordLabel entities
   - Delete ReceiptMetadata (if applicable)

2. **Update OCR routing decision**:
   - Update `receipt_count` to reflect new count

## Implementation Approach

### Option A: Script-Based (Recommended for Initial Implementation)

Create a script similar to `combine_receipts_logic.py`:
- `scripts/split_receipt.py`
- Can be run manually for the 2 problem images
- Allows testing and validation before automation

**Pros**:
- Easy to test and iterate
- Can validate results before committing
- Can be run multiple times safely

**Cons**:
- Manual process
- Requires running script for each image

### Option B: Lambda/Step Function (Future)

Create a Step Function similar to `combine_receipts`:
- Triggered when clustering detects split needed
- Automated process
- Can be integrated into upload workflow

**Pros**:
- Automated
- Consistent process
- Can be triggered automatically

**Cons**:
- More complex
- Harder to test
- Requires infrastructure changes

## Key Functions Needed

1. **`split_receipt()`** - Main function (similar to `combine_receipts()`)
   - Takes: image_id, original_receipt_id, cluster_assignments
   - Returns: new_receipt_ids, migration_summary

2. **`_create_split_receipt_records()`** - Create new entities
   - Similar to `_create_combined_receipt_records()`
   - But creates multiple receipts instead of one

3. **`_migrate_receipt_word_labels_split()`** - Migrate labels
   - Similar to `_migrate_receipt_word_labels()`
   - But maps to multiple new receipts

4. **`_calculate_receipt_bounds()`** - Calculate bounds for each cluster
   - Similar to `_calculate_combined_receipt_bounds()`
   - But for individual clusters

5. **`_export_receipt_ndjson_and_queue()`** - Export and queue (reuse existing)

## Coordinate System Considerations

**Important**: Receipt coordinates use OCR space (y=0 at bottom), not image space.

- Receipt corners: Normalized (0-1) relative to image, OCR space
- ReceiptLine/ReceiptWord: Relative to receipt bounds, OCR space
- When splitting, need to:
  1. Calculate absolute coordinates in image space
  2. Calculate new receipt bounds
  3. Recalculate relative coordinates for lines/words

## Testing Strategy

1. **Dry run mode**:
   - Calculate all new entities
   - Don't write to DynamoDB
   - Validate results

2. **Validation checks**:
   - All lines/words assigned
   - No duplicate IDs
   - Coordinates valid
   - Labels mapped correctly

3. **Visualization**:
   - Generate images showing old vs new receipts
   - Verify clusters are correct

## Rollout Plan

1. **Phase 1**: Create script, test on local data
2. **Phase 2**: Test on dev environment with one image
3. **Phase 3**: Apply to both problem images
4. **Phase 4**: Monitor results, verify embeddings
5. **Phase 5**: (Future) Automate if needed

## Example Flow

```python
# Load existing receipt
original_receipt = client.get_receipt(image_id, receipt_id=1)
receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id=1)
receipt_words = client.list_receipt_words_from_receipt(image_id, receipt_id=1)
receipt_labels = client.list_receipt_word_labels_for_receipt(image_id, receipt_id=1)

# Re-cluster
image_lines = client.list_lines_from_image(image_id)
cluster_assignments = recluster_with_two_phase(image_lines)

# Split receipt
result = split_receipt(
    client=client,
    image_id=image_id,
    original_receipt_id=1,
    cluster_assignments=cluster_assignments,
    raw_bucket=raw_bucket,
    site_bucket=site_bucket,
    artifacts_bucket=artifacts_bucket,
    embed_ndjson_queue_url=embed_ndjson_queue_url,
    dry_run=False,
)

# Result contains:
# - new_receipt_ids: [1, 2]
# - migration_summary: {...}
# - ndjson_exported: True
# - embeddings_queued: True
```

## Questions to Resolve

1. **Receipt IDs**: Should new receipts use IDs 1, 2, or continue from existing?
   - Recommendation: Use 1, 2 (delete original first, or use 2, 3)

2. **ReceiptMetadata**: How to split?
   - Recommendation: Copy to both, let merchant validation update

3. **Image files**: Do we need to create new receipt images?
   - Recommendation: Optional - can be done later if needed

4. **Compaction runs**: Should we create new compaction runs?
   - Recommendation: Yes, for each new receipt

5. **Original receipt**: Delete or keep?
   - Recommendation: Delete after validation


