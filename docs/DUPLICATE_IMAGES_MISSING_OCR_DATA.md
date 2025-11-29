# Duplicate Images with Missing OCR Data

## Problem Summary

An analysis of receipts not in `batch_summaries` revealed that 17 receipts exist in the database but have no OCR data (0 lines, 0 words). Investigation showed these receipts are associated with duplicate images (same SHA256 hash as other images in the database).

## Analysis Date

November 24, 2025

## Receipts Affected

17 receipts across 16 unique images:

1. `1905dba2-c408-48e9-aa4d-f47fec0a3835` / Receipt 1
2. `23e1b623-e3fa-4102-84ca-4b5b3862e6ed` / Receipt 2
3. `53e716ed-49bf-4562-90e7-fc8c860cb985` / Receipt 2
4. `6bfbbd7b-40af-4cb7-89c5-095796f9d1a5` / Receipt 2
5. `783f5ca8-dec8-4d72-bfc2-5196573942bb` / Receipt 2
6. `866ae94f-6274-4f02-a4ab-42eec72381fb` / Receipt 2
7. `910efdda-b8f9-42e4-9666-ce76cf8371bf` / Receipt 1
8. `9efaf9b1-b39d-49c6-93f2-bc58e52387b0` / Receipt 1
9. `a1dacbc8-ab6a-40af-8146-b15d44a7aa81` / Receipt 1
10. `abaec508-6730-4d75-9d48-76492a26a168` / Receipt 1
11. `b4791435-f9a7-4fb3-ac5f-35f8c933e3fe` / Receipt 2
12. `b4791435-f9a7-4fb3-ac5f-35f8c933e3fe` / Receipt 3
13. `baf2e4e5-fd03-4301-8f99-4a92b03ba40e` / Receipt 1
14. `c0900230-2907-4093-9bf7-29ca3cdf397a` / Receipt 1
15. `cb22100f-44c2-4b7d-b29f-46627a64355a` / Receipt 3
16. `dea7bb6f-ea6d-4ae8-a365-02c79b00970f` / Receipt 3
17. `e26d6ebe-ace2-45e2-9a3c-830fb947f4c7` / Receipt 1

## Timeline

- **Earliest receipt added**: June 22, 2025
- **Latest receipt added**: October 27, 2025
- **Analysis date**: November 24, 2025
- **Age range**: 1-5 months old

## Key Findings

### 1. Missing OCR Data

All 17 receipts have:
- ✅ Receipt records exist in DynamoDB
- ❌ 0 lines (no `RECEIPT_LINE` records)
- ❌ 0 words (no `RECEIPT_WORD` records)
- ❌ Not in `batch_summaries` (expected, since there's nothing to embed)

### 2. Duplicate Images

**All 16 unique images are duplicates** - they share SHA256 hashes with other images in the database:

#### Direct Duplicate (within affected receipts):
- **Hash**: `b5c3c17a4ac213675cd6fedd9a0bee11...`
  - `cb22100f-44c2-4b7d-b29f-46627a64355a`
    - Receipt 1: ✅ 85 lines, 196 words
    - Receipt 2: ✅ 20 lines, 48 words
    - Receipt 3: ❌ 0 lines, 0 words
  - `b4791435-f9a7-4fb3-ac5f-35f8c933e3fe`
    - Receipt 1: ✅ 85 lines, 196 words
    - Receipt 2: ❌ 0 lines, 0 words
    - Receipt 3: ❌ 0 lines, 0 words

#### Other Duplicates:
All other 15 images in the list share hashes with other images in the database (not in this list). Examples:
- `c0900230-2907-4093-9bf7-29ca3cdf397a` (hash: `fbb577db08f8e9a2...`) duplicates:
  - `a63a6c84-d399-452f-9e56-08ff664c8f35`
  - `49a1a1e7-348a-4cd9-af96-2312e838476e`
- `6bfbbd7b-40af-4cb7-89c5-095796f9d1a5` (hash: `8e7ed123b27f630a...`) duplicates:
  - `d67ee0e3-6f30-4142-8209-cebbf309f40e`
- And 13 more similar cases...

### 3. Pattern Observed

1. **Duplicate images were uploaded** (same SHA256 hash)
2. **Receipt records were created** for both the original and duplicate images
3. **OCR processing was likely skipped** for duplicate images (to avoid redundant processing)
4. **OCR data was not copied** from the original image's receipts to the duplicate image's receipts
5. **Result**: Duplicate image receipts exist but have no OCR data

## Root Cause Hypothesis

The system appears to:
1. Detect duplicate images by SHA256 hash
2. Skip OCR processing for duplicates (efficiency measure)
3. Still create receipt records for duplicate images
4. **Missing step**: Copy OCR data from original image receipts to duplicate image receipts

## Impact

- **17 receipts** cannot be processed for embeddings
- **17 receipts** cannot be included in batch summaries
- **17 receipts** are effectively "orphaned" - they exist but have no usable data

## Scripts Created

### 1. `scripts/dev.compare_batch_receipts.py`
Compares receipts managed by `batch_summaries` vs those that aren't.

**Usage:**
```bash
python scripts/dev.compare_batch_receipts.py --output dev_batch_comparison.json
```

### 2. `scripts/dev.update_embedding_statuses.py`
Updates embedding statuses for receipts (currently not applicable since these receipts have no OCR data).

**Usage:**
```bash
python scripts/dev.update_embedding_statuses.py --comparison-file dev_batch_comparison.json --dry-run
```

## Potential Solutions

### Option 1: Copy OCR Data from Duplicate Images
For each duplicate image, copy lines and words from the original image's receipts to the duplicate image's receipts.

**Pros:**
- Fast (no OCR processing needed)
- Preserves existing data
- Can be automated

**Cons:**
- Need to map which receipts correspond between original and duplicate
- May have different receipt boundaries

### Option 2: Manually Trigger OCR
Force OCR processing for these specific receipts.

**Pros:**
- Ensures fresh OCR data
- Handles any receipt boundary differences

**Cons:**
- Redundant processing (same image already OCR'd)
- Takes time and resources

### Option 3: Mark as Duplicates and Skip
Mark these receipts as duplicates and exclude them from processing.

**Pros:**
- Simple
- No redundant processing

**Cons:**
- Data loss (receipts exist but are unusable)
- May need these receipts if they have different receipt boundaries

### Option 4: Fix Duplicate Detection Logic
Update the duplicate detection/processing logic to:
1. Detect duplicates
2. Copy OCR data from original to duplicate
3. Or merge receipts appropriately

**Pros:**
- Prevents future occurrences
- Handles the root cause

**Cons:**
- Requires code changes
- May need to handle edge cases

## Recommended Approach

**Short-term**: Option 1 (Copy OCR Data)
- Create a script to identify duplicate images
- Copy lines/words from original image receipts to duplicate image receipts
- Update embedding statuses
- Run submit step function to create batch summaries

**Long-term**: Option 4 (Fix Duplicate Detection)
- Update duplicate detection logic to copy OCR data
- Or merge receipts when duplicates are detected
- Prevent this issue from recurring

## Next Steps

1. ✅ Document the issue (this file)
2. ⏳ Create script to copy OCR data from duplicate images
3. ⏳ Test the copy operation on a few receipts
4. ⏳ Apply to all 17 receipts
5. ⏳ Update embedding statuses
6. ⏳ Run submit step function
7. ⏳ Fix duplicate detection logic to prevent recurrence

## Related Files

- `scripts/dev.compare_batch_receipts.py` - Comparison script
- `scripts/dev.update_embedding_statuses.py` - Status update script
- `dev_batch_comparison.json` - Comparison results

## Notes

- All affected receipts are in the **dev** stack
- The duplicate images have receipts with OCR data, confirming the images were successfully processed
- The issue appears to be in the duplicate handling logic, not in OCR processing itself


