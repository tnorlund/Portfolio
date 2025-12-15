# Final Merge Result Preservation

## Problem

The `final_merge_result` from `WordFinalMerge` / `FinalMerge` was being lost in the execution output because the `PrepareMarkWordBatchesComplete` / `PrepareMarkBatchesComplete` Pass state was overwriting the entire state.

## Solution: Preserve Inline (Option 1)

Since `final_merge_result` is small (~200-300 bytes), we preserve it inline in the Pass state by explicitly including it in the Parameters.

### Implementation

**Before** (lost `final_merge_result`):
```python
"PrepareMarkWordBatchesComplete": {
    "Type": "Pass",
    "Parameters": {
        "poll_results_s3_key.$": "$.final_merge_result.poll_results_s3_key",
        # ... other fields, but NO final_merge_result
    },
    "Next": "MarkWordBatchesComplete",
}
```

**After** (preserves `final_merge_result`):
```python
"PrepareMarkWordBatchesComplete": {
    "Type": "Pass",
    "Parameters": {
        "final_merge_result.$": "$.final_merge_result",  # ✅ Preserve this!
        "poll_results_s3_key.$": "$.final_merge_result.poll_results_s3_key",
        # ... other fields
    },
    "Next": "MarkWordBatchesComplete",
}
```

### Why This Works

- `final_merge_result` is small (~200-300 bytes) - well under Step Functions' 256KB limit
- No S3 round-trip needed
- Simple and straightforward
- Preserves the data in execution output for debugging/monitoring

### What's Preserved

The `final_merge_result` contains:
```json
{
  "statusCode": 200,
  "batch_id": "batch-uuid",
  "snapshot_key": "words/snapshot/latest/",
  "total_embeddings": 12345,
  "processing_time_seconds": 45.2,
  "message": "Final merge completed successfully",
  "poll_results_s3_key": "poll_results/{execution_id}/poll_results.json",
  "poll_results_s3_bucket": "bucket-name"
}
```

## Alternative: S3 Pattern (Option 2)

If you prefer consistency with your S3 pattern for all data, you could upload `final_merge_result` to S3. However, this is overkill for such small data.

### How S3 Pattern Would Work

1. **In `final_merge_handler`**:
   - Upload `final_merge_result` to S3: `final_merge_results/{batch_id}/result.json`
   - Return S3 reference instead of full data:
     ```python
     return {
         "statusCode": 200,
         "final_merge_result_s3_key": f"final_merge_results/{batch_id}/result.json",
         "final_merge_result_s3_bucket": bucket,
         "poll_results_s3_key": poll_results_s3_key,
         "poll_results_s3_bucket": poll_results_s3_bucket,
     }
     ```

2. **In `PrepareMarkWordBatchesComplete`**:
   - Preserve the S3 reference:
     ```python
     "final_merge_result_s3_key.$": "$.final_merge_result.final_merge_result_s3_key",
     "final_merge_result_s3_bucket.$": "$.final_merge_result.final_merge_result_s3_bucket",
     ```

3. **When needed** (e.g., in monitoring/debugging):
   - Download from S3 using the reference

### Why We Chose Option 1

- **Simplicity**: No S3 upload/download needed
- **Performance**: No network round-trip
- **Size**: Data is small enough to keep inline
- **Consistency**: Other small metadata (like `poll_results_s3_key`) is also kept inline

## Files Changed

- `infra/embedding_step_functions/components/word_workflow.py`
- `infra/embedding_step_functions/components/line_workflow.py`

## Testing

After deployment, verify that `final_merge_result` is present in execution output:

```bash
aws stepfunctions describe-execution \
  --execution-arn "arn:..." \
  --query 'output' \
  --output text | jq '.final_merge_result'
```

Should return:
```json
{
  "statusCode": 200,
  "batch_id": "...",
  "snapshot_key": "...",
  "total_embeddings": 12345,
  ...
}
```

