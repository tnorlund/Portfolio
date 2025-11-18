# Execution 8ee0d036 Analysis - MarkBatchesComplete Not Updating Batch Summaries

## Execution Details
- **Execution ID**: `8ee0d036-2881-4a5d-b6d5-57022197226a`
- **State Machine**: `line-ingest-sf-dev-1554303`
- **Status**: RUNNING (as of analysis time)
- **Start Date**: 2025-11-16T21:48:59.677000-08:00

## Problem Statement

The `MarkBatchesComplete` step is not updating batch summaries in DynamoDB. The Lambda logs show:
```
[INFO] Starting mark_batches_complete handler
[INFO] No poll results to mark as complete
```

This indicates that `poll_results_s3_key` is `null` when it reaches `MarkBatchesComplete`, preventing the handler from loading the batch IDs from S3.

## Expected Data Flow

### Path 1: Hierarchical Merge (when total_chunks > 4)

1. **NormalizePollBatchesData** → Returns:
   ```json
   {
     "poll_results": null,
     "poll_results_s3_key": "poll_results/{execution_id}/poll_results.json",
     "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
   }
   ```
   Stored at: `$.poll_results_data`

2. **SplitIntoChunks** → Receives `poll_results_s3_key` from `$.poll_results_data.poll_results_s3_key`
   - Should preserve it in output: `chunked_data.poll_results_s3_key`

3. **GroupChunksForMerge** (Pass) → Gets from `$.poll_results_data.poll_results_s3_key`
   - Sets: `poll_results_s3_key.$": "$.poll_results_data.poll_results_s3_key"`
   - This creates `$.poll_results_s3_key` at root level

4. **CreateChunkGroups** → Should preserve `poll_results_s3_key` in `chunk_groups.poll_results_s3_key`

5. **PrepareHierarchicalFinalMerge** (Pass) → Gets from `$.chunk_groups.poll_results_s3_key`
   - Sets: `poll_results_s3_key.$": "$.chunk_groups.poll_results_s3_key"`
   - This creates `$.poll_results_s3_key` at root level

6. **FinalMerge** (Lambda) → Receives `poll_results_s3_key` from `$.poll_results_s3_key`
   - Lambda handler receives it as input parameter
   - Lambda returns it in response: `poll_results_s3_key: poll_results_s3_key`
   - Response goes to: `$.final_merge_result.poll_results_s3_key`

7. **PrepareMarkBatchesComplete** (Pass) → Gets from `$.final_merge_result.poll_results_s3_key`
   - Sets: `poll_results_s3_key.$": "$.final_merge_result.poll_results_s3_key"`
   - Also sets fallback: `poll_results_s3_key_fallback.$": "$.poll_results_s3_key"`

8. **MarkBatchesComplete** (Lambda) → Receives:
   - `poll_results_s3_key.$": "$.poll_results_s3_key"` (primary)
   - `poll_results_s3_key_fallback.$": "$.poll_results_s3_key_fallback"` (fallback)

### Path 2: Non-Hierarchical Merge (when total_chunks ≤ 4)

1. **NormalizePollBatchesData** → Same as Path 1, step 1

2. **SplitIntoChunks** → Same as Path 1, step 2

3. **PrepareFinalMerge** (Pass) → Gets from `$.chunked_data.poll_results_s3_key`
   - Sets: `poll_results_s3_key.$": "$.chunked_data.poll_results_s3_key"`
   - This creates `$.poll_results_s3_key` at root level

4. **FinalMerge** (Lambda) → Same as Path 1, step 6

5. **PrepareMarkBatchesComplete** (Pass) → Same as Path 1, step 7

6. **MarkBatchesComplete** (Lambda) → Same as Path 1, step 8

## The Issue

Based on the completed execution `4809a6c7-af67-49ae-aac7-b2f5623c0670` output:
```json
{
  "poll_results_s3_bucket": null,
  "poll_results_s3_key": null,
  "poll_results_s3_bucket_fallback": null,
  "poll_results_s3_key_fallback": null,
  "mark_complete_result": {
    "batches_marked": 0,
    "batch_ids": []
  }
}
```

The `poll_results_s3_key` is `null` at `MarkBatchesComplete`. This means it was lost somewhere in the chain.

## Root Cause Analysis

### Potential Failure Points

1. **SplitIntoChunks not preserving `poll_results_s3_key`**
   - If `SplitIntoChunks` doesn't receive it from `$.poll_results_data.poll_results_s3_key`, or doesn't preserve it in output, it will be lost.

2. **CreateChunkGroups not preserving `poll_results_s3_key`**
   - For hierarchical merge path, if `CreateChunkGroups` doesn't preserve `poll_results_s3_key` from input to `chunk_groups.poll_results_s3_key`, `PrepareHierarchicalFinalMerge` will get `null`.

3. **PrepareFinalMerge / PrepareHierarchicalFinalMerge getting `null`**
   - If the upstream step didn't preserve `poll_results_s3_key`, the Pass state will set `$.poll_results_s3_key` to `null` at root level.

4. **FinalMerge receiving `null` and passing it through**
   - The Lambda handler correctly passes through `poll_results_s3_key` even if it's `null`, so `$.final_merge_result.poll_results_s3_key` will be `null`.

5. **PrepareMarkBatchesComplete getting `null` from `$.final_merge_result.poll_results_s3_key`**
   - If `FinalMerge` returned `null`, this Pass state will set `$.poll_results_s3_key` to `null`.

6. **MarkBatchesComplete receiving `null`**
   - Handler checks for `poll_results_s3_key` but finds `null`, so it can't load batch IDs from S3.

## Verification Steps Needed

To diagnose which step is losing `poll_results_s3_key`, we need to check:

1. **NormalizePollBatchesData output**: Does it return `poll_results_s3_key`?
   - Check Lambda logs: `/aws/lambda/embedding-normalize-poll-batches-lambda-dev-f5c4516`
   - Look for: "Uploaded poll_results to S3" with `s3_key`

2. **SplitIntoChunks input/output**: Does it receive and preserve `poll_results_s3_key`?
   - Check Step Functions execution history for `SplitIntoChunks` state entered event
   - Input should have: `poll_results_s3_key` from `$.poll_results_data.poll_results_s3_key`
   - Output should have: `chunked_data.poll_results_s3_key`

3. **CreateChunkGroups input/output** (for hierarchical path):
   - Check if `CreateChunkGroups` receives `poll_results_s3_key` from `GroupChunksForMerge`
   - Check if it preserves it in `chunk_groups.poll_results_s3_key`

4. **PrepareFinalMerge / PrepareHierarchicalFinalMerge input**:
   - Check what `poll_results_s3_key` value is at this point
   - Should be from `$.chunked_data.poll_results_s3_key` (non-hierarchical) or `$.chunk_groups.poll_results_s3_key` (hierarchical)

5. **FinalMerge input/output**:
   - Check Lambda logs: `/aws/lambda/embedding-vector-compact-lambda-dev`
   - Input should have `poll_results_s3_key` parameter
   - Output should have `final_merge_result.poll_results_s3_key`

6. **PrepareMarkBatchesComplete input**:
   - Check Step Functions execution history
   - Should have `$.final_merge_result.poll_results_s3_key` (may be `null`)

7. **MarkBatchesComplete input**:
   - Check Lambda logs: `/aws/lambda/embedding-mark-batches-complete-lambda-dev-65f48a2`
   - Should log: "Found poll_results_s3_key from ..." if it exists
   - Currently logs: "No poll results to mark as complete" (indicates `null`)

## What Information MarkBatchesComplete Needs

The `MarkBatchesComplete` handler needs:

1. **`poll_results_s3_key`**: S3 key where the combined `poll_results` array is stored
2. **`poll_results_s3_bucket`**: S3 bucket where the file is stored

The file at `s3://{bucket}/{key}` contains a JSON array like:
```json
[
  {
    "batch_id": "uuid-1",
    "openai_batch_id": "batch_xxx1",
    "batch_status": "completed",
    ...
  },
  {
    "batch_id": "uuid-2",
    ...
  }
]
```

The handler extracts `batch_id` from each element and updates the corresponding `BatchSummary` in DynamoDB to `COMPLETED`.

## Are the Inputs Sufficient?

**Yes, if `poll_results_s3_key` and `poll_results_s3_bucket` are provided correctly.**

The handler can:
1. Download the file from S3
2. Parse the JSON array
3. Extract `batch_id` from each element
4. Update DynamoDB `BatchSummary` records

**However, if `poll_results_s3_key` is `null`, the handler cannot:**
- Know which batches were processed in this execution
- Load the batch IDs from S3
- Update any batch summaries

## Actual Execution Analysis (4809a6c7)

From the completed execution `4809a6c7-af67-49ae-aac7-b2f5623c0670`:

**Final Output**:
```json
{
  "poll_results_s3_bucket": null,
  "poll_results_s3_key": null,
  "poll_results_s3_bucket_fallback": null,
  "poll_results_s3_key_fallback": null,
  "mark_complete_result": {
    "batches_marked": 0,
    "batch_ids": []
  }
}
```

**Lambda Logs** (MarkBatchesComplete):
```
[INFO] Starting mark_batches_complete handler
[INFO] No poll results to mark as complete
```

This confirms that `poll_results_s3_key` was `null` when it reached `MarkBatchesComplete`.

## What MarkBatchesComplete Actually Received

Based on the Step Functions definition in `line_workflow.py`, `MarkBatchesComplete` receives:

```json
{
  "poll_results_s3_key": null,  // From $.poll_results_s3_key (set by PrepareMarkBatchesComplete)
  "poll_results_s3_bucket": null,  // From $.poll_results_s3_bucket
  "poll_results_s3_key_fallback": null,  // From $.poll_results_s3_key_fallback
  "poll_results_s3_bucket_fallback": null,  // From $.poll_results_s3_bucket_fallback
  "poll_results_s3_key_chunked": null,  // Hardcoded to None
  "poll_results_s3_bucket_chunked": null  // Hardcoded to None
}
```

## Are These Inputs Sufficient?

**NO. The inputs are NOT sufficient.**

The handler needs at least ONE of these to be non-null:
- `poll_results_s3_key` (primary)
- `poll_results_s3_key_fallback` (fallback)
- `poll_results_s3_key_chunked` (chunked fallback)

Since all are `null`, the handler cannot:
1. Know which batches were processed in this execution
2. Load the batch IDs from S3
3. Update any batch summaries in DynamoDB

## Why the Inputs Are Insufficient

The handler logic (from `mark_batches_complete.py`):
```python
poll_results_s3_key = (
    poll_results_s3_key_primary
    or poll_results_s3_key_fallback
    or poll_results_s3_key_chunked
)

if (not poll_results or poll_results is None) and poll_results_s3_key and poll_results_s3_bucket:
    # Load from S3...
else:
    logger.info("No poll results to mark as complete")
    return {"batches_marked": 0, "batch_ids": []}
```

Since `poll_results_s3_key` is `null`, the condition fails and the handler returns early without updating any batch summaries.

## Root Cause

The `poll_results_s3_key` is being lost somewhere in the workflow. The most likely failure point is:

**`PrepareFinalMerge` or `PrepareHierarchicalFinalMerge` is getting `null` from upstream**

This happens because:
1. `SplitIntoChunks` should preserve `poll_results_s3_key` in `chunked_data.poll_results_s3_key`
2. `PrepareFinalMerge` tries to get it from `$.chunked_data.poll_results_s3_key`
3. If `SplitIntoChunks` didn't preserve it (or if it was `null` from `NormalizePollBatchesData`), then `PrepareFinalMerge` sets `$.poll_results_s3_key` to `null`
4. `FinalMerge` receives `null` and passes it through
5. `PrepareMarkBatchesComplete` gets `null` from `$.final_merge_result.poll_results_s3_key`
6. `MarkBatchesComplete` receives `null`

## Conclusion

The issue is that `poll_results_s3_key` is being lost somewhere in the Step Functions workflow between `NormalizePollBatchesData` and `MarkBatchesComplete`. The most likely failure points are:

1. **NormalizePollBatchesData** not returning `poll_results_s3_key` (unlikely - handler always returns it)
2. **SplitIntoChunks** not preserving it in `chunked_data.poll_results_s3_key` (possible - need to verify handler is receiving it)
3. **CreateChunkGroups** not preserving it in `chunk_groups.poll_results_s3_key` (for hierarchical path)
4. **PrepareFinalMerge / PrepareHierarchicalFinalMerge** getting `null` from upstream (most likely)

**Next Steps**:
1. Check `NormalizePollBatchesData` Lambda logs to verify it's returning `poll_results_s3_key`
2. Check `SplitIntoChunks` Lambda logs to verify it's receiving and preserving `poll_results_s3_key`
3. Check Step Functions execution history to see what value `PrepareFinalMerge` receives for `poll_results_s3_key`
4. Verify the Step Functions definition has been deployed correctly (Pulumi deployment)

