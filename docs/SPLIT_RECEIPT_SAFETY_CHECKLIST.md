# Split Receipt Safety Checklist

## ✅ Ready to Run

The split receipt script is **safe to run** and will **NOT delete or modify** any existing data.

## Safety Guarantees

### 1. **Original Receipt Preserved**
- ✅ Original receipt and all sub-records are **kept intact**
- ✅ Original labels remain on original receipt
- ✅ Original receipt can be used for rollback

### 2. **No ID Conflicts**
- ✅ Script finds next available receipt ID automatically
- ✅ Starts at `max(existing_receipt_ids) + 1`
- ✅ Fallback to ID 1000 if query fails (safe high number)
- ✅ New receipts get unique IDs that won't conflict

### 3. **New Records Only**
- ✅ Creates **new** Receipt entities
- ✅ Creates **new** ReceiptLine entities
- ✅ Creates **new** ReceiptWord entities
- ✅ Creates **new** ReceiptLetter entities
- ✅ Creates **new** ReceiptWordLabel entities (migrated from original)
- ✅ Creates **new** CompactionRun entities
- ✅ Uploads **new** images to S3/CDN

### 4. **No Deletions**
- ❌ **NO** deletion of original receipt
- ❌ **NO** deletion of original labels
- ❌ **NO** deletion of original words/lines/letters
- ❌ **NO** deletion of original images

### 5. **Coordinate System**
- ✅ Uses OCR space (y=0 at bottom) - matches scan/native processing
- ✅ No padding - matches upload process
- ✅ Uses actual image dimensions for accuracy

### 6. **Embedding Safety**
- ✅ Creates new embeddings in ChromaDB
- ✅ Uses unique ChromaDB IDs (based on new receipt IDs)
- ✅ Waits for compaction before adding labels
- ✅ Labels update existing embeddings (don't create duplicates)

### 7. **Image Upload**
- ✅ Uploads cropped receipt images to raw bucket
- ✅ Uploads CDN variants (JPEG, WebP, AVIF) to site bucket
- ✅ Uses unique S3 keys: `{image_id}_RECEIPT_{new_receipt_id:05d}`
- ✅ Won't overwrite existing images

## What Gets Created

For each new receipt:
1. **DynamoDB Records**:
   - 1 Receipt entity
   - N ReceiptLine entities
   - M ReceiptWord entities
   - L ReceiptLetter entities (if any)
   - K ReceiptWordLabel entities (migrated from original)
   - 1 CompactionRun entity (if embedding enabled)

2. **S3 Files**:
   - Raw image: `raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png`
   - CDN images: `assets/{image_id}_RECEIPT_{new_receipt_id:05d}.*` (JPEG, WebP, AVIF, thumbnails)
   - NDJSON: `artifacts/{image_id}/receipt_{new_receipt_id:05d}/lines.ndjson`
   - NDJSON: `artifacts/{image_id}/receipt_{new_receipt_id:05d}/words.ndjson`

3. **ChromaDB Embeddings**:
   - Line embeddings (if embedding enabled)
   - Word embeddings (if embedding enabled)

## Rollback Strategy

If something goes wrong:
1. **Use rollback script**: `scripts/rollback_split_receipt.py`
2. **Delete new receipts**: Script deletes in correct order (labels → words → lines → letters → receipt)
3. **Original receipt remains**: Can continue using original receipt
4. **ChromaDB cleanup**: Note that deleting DynamoDB records does NOT delete ChromaDB embeddings (they become orphaned)

## Testing Recommendations

1. **Start with dry-run**:
   ```bash
   python scripts/split_receipt.py \
       --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
       --original-receipt-id 1 \
       --dry-run
   ```

2. **Review local files**: Check `local_receipt_splits/{image_id}/` for generated records

3. **Run for real**:
   ```bash
   python scripts/split_receipt.py \
       --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
       --original-receipt-id 1
   ```

4. **Verify in DynamoDB**: Check that new receipts exist and original is intact

5. **Verify in ChromaDB**: Check that embeddings were created (if enabled)

6. **Verify images**: Check that CDN images are accessible

## Potential Issues

1. **Receipt ID conflicts**: ✅ Handled - script finds next available ID
2. **S3 key conflicts**: ✅ Handled - uses unique receipt IDs in keys
3. **ChromaDB ID conflicts**: ✅ Handled - uses unique receipt IDs in ChromaDB IDs
4. **Label migration**: ✅ Handled - creates new labels, keeps originals
5. **Coordinate accuracy**: ✅ Handled - uses actual image dimensions

## Conclusion

**✅ SAFE TO RUN** - The script only creates new records and never modifies or deletes existing data.

