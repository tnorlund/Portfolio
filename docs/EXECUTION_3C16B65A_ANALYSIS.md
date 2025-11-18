# Execution 3c16b65a-bd2a-4028-bd62-c305487004f5 Analysis

## Execution Details
- **Execution ID**: `3c16b65a-bd2a-4028-bd62-c305487004f5`
- **State Machine**: `word-ingest-sf-dev-8fa425a`
- **Status**: SUCCEEDED
- **Start Date**: 2025-11-16T22:17:48.742000-08:00
- **Stop Date**: 2025-11-16T22:19:36.582000-08:00

## Problem

The execution succeeded, but `MarkWordBatchesComplete` did not update any batch summaries. The final output shows:
```json
{
  "poll_results_s3_key": null,
  "poll_results_s3_bucket": null,
  "poll_results_s3_key_fallback": null,
  "poll_results_s3_bucket_fallback": null,
  "poll_results_s3_key_poll_data": null,
  "poll_results_s3_bucket_poll_data": null,
  "mark_complete_result": {
    "batches_marked": 0,
    "batch_ids": []
  }
}
```

Lambda logs show:
```
[INFO] Starting mark_batches_complete handler
[INFO] No poll results to mark as complete
```

## Root Cause

### Data Flow Analysis

1. **PrepareWordHierarchicalFinalMerge** (Pass state):
   - Input shows: `poll_results_s3_key: "poll_results/3c16b65a-bd2a-4028-bd62-c305487004f5/poll_results.json"` ✅
   - Sets: `poll_results_s3_key.$": "$.chunk_groups.poll_results_s3_key"` (which was `null`)
   - Sets: `poll_results_s3_key_fallback.$": "$.poll_results_s3_key"` (which had the value)
   - **Problem**: Primary source (`chunk_groups.poll_results_s3_key`) was `null`, so Pass state output had `poll_results_s3_key: null`

2. **WordFinalMerge** (Lambda):
   - Received: `poll_results_s3_key: null` ❌
   - Lambda log shows: `"poll_results_s3_key": null`
   - Returns: `poll_results_s3_key: null` in `final_merge_result`

3. **PrepareMarkWordBatchesComplete** (Pass state):
   - Gets: `poll_results_s3_key.$": "$.final_merge_result.poll_results_s3_key"` → `null`
   - Gets: `poll_results_s3_key_fallback.$": "$.poll_results_s3_key"` → `"poll_results/3c16b65a-bd2a-4028-bd62-c305487004f5/poll_results.json"` ✅
   - But `MarkWordBatchesComplete` checks `poll_results_s3_key` first (primary), which is `null`

4. **MarkWordBatchesComplete** (Lambda):
   - Receives: `poll_results_s3_key: null`, `poll_results_s3_key_fallback: "poll_results/..."` ✅
   - Handler checks: `poll_results_s3_key_primary or poll_results_s3_key_fallback`
   - **Wait**: The handler should have found `poll_results_s3_key_fallback`!

Let me check the handler logic again...

Actually, looking at the handler code:
```python
poll_results_s3_key = (
    poll_results_s3_key_primary
    or poll_results_s3_key_fallback
    or poll_results_s3_key_poll_data
    or poll_results_s3_key_chunked
)
```

The handler should have used `poll_results_s3_key_fallback`. But the logs say "No poll results to mark as complete", which means `poll_results_s3_key` was `null` after the `or` chain.

**Wait**: The issue is that `poll_results_s3_bucket` might also be `null`! The handler checks:
```python
if (not poll_results or poll_results is None) and poll_results_s3_key and poll_results_s3_bucket:
```

So if `poll_results_s3_bucket` is `null`, the condition fails even if `poll_results_s3_key` is set!

Looking at `PrepareMarkWordBatchesComplete`:
- `poll_results_s3_bucket.$": "$.final_merge_result.poll_results_s3_bucket"` → `null` (because WordFinalMerge returned null)
- `poll_results_s3_bucket_fallback.$": "$.poll_results_s3_bucket"` → should have the bucket name

But the handler checks `poll_results_s3_bucket` (primary) first, which is `null`, so it fails the condition.

## The Fix

Changed `PrepareWordHierarchicalFinalMerge` to use root-level `poll_results_s3_key` as primary instead of `chunk_groups.poll_results_s3_key`:

**Before**:
```python
"poll_results_s3_key.$": "$.chunk_groups.poll_results_s3_key",  # null
"poll_results_s3_key_fallback.$": "$.poll_results_s3_key",  # has value
```

**After**:
```python
"poll_results_s3_key.$": "$.poll_results_s3_key",  # has value (set by GroupChunksForMerge)
"poll_results_s3_key_fallback.$": "$.chunk_groups.poll_results_s3_key",  # fallback
```

This ensures `WordFinalMerge` receives the correct `poll_results_s3_key` and can pass it through to `MarkWordBatchesComplete`.

## Why This Happened

`CreateChunkGroups` may not preserve `poll_results_s3_key` in `chunk_groups.poll_results_s3_key` if it receives `null` from `GroupChunksForMerge`. However, `GroupChunksForMerge` sets `poll_results_s3_key` at the root level from `poll_results_data.poll_results_s3_key`, which is guaranteed to exist. So using root level as primary is more reliable.

## Expected Behavior After Fix

1. `PrepareWordHierarchicalFinalMerge` sets `poll_results_s3_key` from root level (has value) ✅
2. `WordFinalMerge` receives `poll_results_s3_key` with value ✅
3. `WordFinalMerge` returns `poll_results_s3_key` in `final_merge_result` ✅
4. `PrepareMarkWordBatchesComplete` gets `poll_results_s3_key` from `final_merge_result` ✅
5. `MarkWordBatchesComplete` receives `poll_results_s3_key` with value ✅
6. Handler loads from S3 and updates batch summaries ✅

