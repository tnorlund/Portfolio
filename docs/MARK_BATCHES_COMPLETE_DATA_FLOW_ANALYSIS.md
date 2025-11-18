# MarkBatchesComplete Data Flow Analysis

## Problem
`MarkWordBatchesComplete` and `MarkBatchesComplete` are not successfully updating batch summaries because `poll_results_s3_key` is not available when the handler runs.

## Data Flow Paths

### Path 1: Hierarchical Merge (when total_chunks > 4)

1. **GroupChunksForMerge** (Pass)
   - Sets `$.poll_results_s3_key` from `$.poll_results_data.poll_results_s3_key` ✅

2. **CreateChunkGroups** (Lambda)
   - Receives `$.poll_results_s3_key` from root
   - Returns `$.chunk_groups.poll_results_s3_key` ✅

3. **MergeChunkGroupsInParallel** (Map)
   - Uses `ResultPath: "$.merged_groups"` and `OutputPath: "$"`
   - Outputs entire state (preserves `$.chunk_groups.poll_results_s3_key`) ✅

4. **PrepareWordHierarchicalFinalMerge** (Pass)
   - **Sets `$.poll_results_s3_key` at root level** from `$.chunk_groups.poll_results_s3_key` ✅
   - This replaces the entire state with only the Parameters

5. **WordFinalMerge** (Lambda)
   - Receives `$.poll_results_s3_key` as input parameter ✅
   - Lambda returns `poll_results_s3_key` in response ✅
   - Uses `ResultPath: "$.final_merge_result"` → Lambda output goes to `$.final_merge_result.poll_results_s3_key`
   - Uses `OutputPath: "$"` → Entire state is output
   - **State should have BOTH:**
     - `$.poll_results_s3_key` (from Pass state - should still be there)
     - `$.final_merge_result.poll_results_s3_key` (from Lambda response)

6. **MarkWordBatchesComplete** (Lambda)
   - Tries to get `$.poll_results_s3_key` from root level
   - **Issue**: If Pass state replaced entire state, and WordFinalMerge's OutputPath doesn't preserve it, this might be missing

### Path 2: Non-Hierarchical Merge (when total_chunks ≤ 4)

1. **PrepareWordFinalMerge** (Pass)
   - **Sets `$.poll_results_s3_key` at root level** from `$.chunked_data.poll_results_s3_key` ✅

2. **WordFinalMerge** (Lambda)
   - Same as Path 1, step 5

3. **MarkWordBatchesComplete** (Lambda)
   - Same as Path 1, step 6

### Path 3: No Chunks

1. **NoWordChunksToProcess** (Pass)
   - **Sets `$.poll_results_s3_key` at root level** from `$.chunked_data.poll_results_s3_key` ✅

2. **MarkWordBatchesComplete** (Lambda)
   - Tries to get `$.poll_results_s3_key` from root level
   - **This path should work** since it doesn't go through WordFinalMerge

## The Issue

The problem is that **Pass states with `Parameters` replace the entire state** with only what's in Parameters. So:

1. `PrepareWordHierarchicalFinalMerge` or `PrepareWordFinalMerge` sets `$.poll_results_s3_key` at root
2. `WordFinalMerge` receives it as input parameter
3. `WordFinalMerge` uses `ResultPath: "$.final_merge_result"` which merges Lambda output into state
4. `WordFinalMerge` uses `OutputPath: "$"` which should output entire state
5. **BUT**: The Pass state already replaced the state, so `$.poll_results_s3_key` should still be there from the Pass state

However, the Lambda also returns `poll_results_s3_key` in its response, which goes to `$.final_merge_result.poll_results_s3_key`.

## Solution Options

### Option 1: Check both locations in MarkWordBatchesComplete
Update the step function to check:
- `$.final_merge_result.poll_results_s3_key` (from Lambda response)
- `$.poll_results_s3_key` (from Pass state)
- `$.chunked_data.poll_results_s3_key` (fallback for no-chunks path)

### Option 2: Use a Pass state before MarkWordBatchesComplete
Add a Pass state that normalizes the path:
- Check if `$.final_merge_result.poll_results_s3_key` exists → use it
- Otherwise check `$.poll_results_s3_key`
- Otherwise check `$.chunked_data.poll_results_s3_key`

### Option 3: Ensure WordFinalMerge preserves root-level keys
Modify `WordFinalMerge` to explicitly preserve `$.poll_results_s3_key` in its OutputPath, or use a Pass state after it to normalize.

## Recommended Fix

**Option 2** is cleanest: Add a Pass state before `MarkWordBatchesComplete` that normalizes the `poll_results_s3_key` location:

```json
"PrepareMarkBatchesComplete": {
  "Type": "Pass",
  "Comment": "Normalize poll_results_s3_key from various possible locations",
  "Parameters": {
    "poll_results_s3_key.$": "$.final_merge_result.poll_results_s3_key",
    "poll_results_s3_bucket.$": "$.final_merge_result.poll_results_s3_bucket",
    "poll_results.$": "$.poll_results"
  },
  "Next": "MarkWordBatchesComplete"
}
```

But this won't work because JSONPath can't do conditional logic. We need to use a Choice state or handle it in the Lambda.

Actually, the best solution is to **update MarkWordBatchesComplete Parameters** to check `$.final_merge_result.poll_results_s3_key` first, with fallbacks, OR update the step function to always ensure `$.poll_results_s3_key` is at root level after WordFinalMerge.

## Current State Analysis

Based on the step function definition:
- `WordFinalMerge` uses `OutputPath: "$"` which should preserve the entire state
- The Pass states before it set `$.poll_results_s3_key` at root
- So `$.poll_results_s3_key` **should** be available at root level

**The real question**: Is `$.poll_results_s3_key` actually in the state when `MarkWordBatchesComplete` runs, or is it only in `$.final_merge_result.poll_results_s3_key`?

We need to check the actual execution history to see what's in the state at that point.

