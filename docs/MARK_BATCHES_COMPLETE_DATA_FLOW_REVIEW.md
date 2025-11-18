# MarkBatchesComplete Data Flow Review

## Overview
This document reviews how `MarkWordBatchesComplete` / `MarkLineBatchesComplete` gets the necessary batch_summary information from `ListPendingWordBatches` / `ListPendingLineBatches`.

## Data Flow Path

### Step 1: ListPendingWordBatches / ListPendingLineBatches

**Handler**: `infra/embedding_step_functions/unified_embedding/handlers/list_pending.py`

**What it does**:
- Queries DynamoDB for batch summaries with `PENDING` status
- Returns a list of batch summaries with `batch_id` and `openai_batch_id`

**Output** (`$.list_result`):
```json
{
  "total_batches": 62,
  "batch_indices": [0, 1, 2, ...],
  "pending_batches": [
    {
      "batch_id": "uuid-1",
      "openai_batch_id": "batch_xxx1"
    },
    {
      "batch_id": "uuid-2",
      "openai_batch_id": "batch_xxx2"
    },
    ...
  ],
  "use_s3": false,  // or true if > 50 batches
  "manifest_s3_key": null,  // or "manifests/{execution_id}/batches.json"
  "manifest_s3_bucket": null,
  "execution_id": "execution-uuid"
}
```

**Key Point**: The `batch_id` from the batch summaries is what we need to track through the workflow.

---

### Step 2: PollWordBatches / PollBatches (Map State)

**Handler**: `infra/embedding_step_functions/unified_embedding/handlers/word_polling.py` or `line_polling.py`

**What it does**:
- For each batch from Step 1, polls OpenAI API for completion status
- Downloads results and processes them
- Creates delta files in S3
- **Returns a result object that includes the `batch_id`**

**Output per batch** (from `word_polling.py` lines 735-745):
```json
{
  "batch_id": "uuid-1",  // ← KEY: This is the batch_id from ListPendingBatches
  "openai_batch_id": "batch_xxx1",
  "batch_status": "completed",
  "action": "process_results",
  "results_count": 100,
  "delta_id": "delta-uuid",
  "delta_key": "words/delta/words/receipt_words/{delta_id}/",
  "embedding_count": 100,
  "storage": "s3_delta"
}
```

**S3 Storage**:
- Each individual result is uploaded to S3 at `poll_results/{batch_id}/result.json`
- The Map state returns only a small S3 reference (~100 bytes) to keep payload small:
```json
{
  "batch_id": "uuid-1",
  "result_s3_key": "poll_results/{batch_id}/result.json",
  "result_s3_bucket": "bucket-name"
}
```

**Key Point**: The `batch_id` is preserved in each poll result and in the S3 reference.

---

### Step 3: NormalizePollWordBatchesData / NormalizePollBatchesData

**Handler**: `infra/embedding_step_functions/unified_embedding/handlers/normalize_poll_batches_data.py`

**What it does**:
1. Receives array of S3 references from Step 2
2. Downloads each individual result from S3 (lines 84-117)
3. Each individual result contains the full result object with `batch_id` (line 103)
4. Combines all results into a single array
5. Uploads combined array to S3 at `poll_results/{execution_id}/poll_results.json`
6. Returns S3 reference (not the full array)

**Input**:
```json
{
  "batch_id": "execution-uuid",
  "poll_results": [
    {
      "batch_id": "uuid-1",
      "result_s3_key": "poll_results/{batch_id}/result.json",
      "result_s3_bucket": "bucket-name"
    },
    ...
  ]
}
```

**Process**:
- Downloads each `result.json` from S3
- Each downloaded file contains the full result with `batch_id`:
  ```json
  {
    "batch_id": "uuid-1",  // ← Preserved from Step 2
    "openai_batch_id": "batch_xxx1",
    "batch_status": "completed",
    ...
  }
  ```
- Combines all into array: `[result1, result2, ...]`
- Uploads to S3: `poll_results/{execution_id}/poll_results.json`

**Output** (`$.poll_results_data`):
```json
{
  "batch_id": "execution-uuid",
  "poll_results": null,  // Always null (always in S3)
  "poll_results_s3_key": "poll_results/{execution_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Key Point**: The combined `poll_results.json` file in S3 contains an array where each element has a `batch_id` field. This is what `MarkBatchesComplete` needs.

---

### Step 4: Workflow Processing (Chunking, Merging, etc.)

**What happens**:
- `poll_results_s3_key` is passed through all workflow steps
- The actual `poll_results` array is NOT passed (it's in S3)
- This prevents payload size issues

**Key Point**: The S3 key is preserved, but the actual data stays in S3.

---

### Step 5: MarkWordBatchesComplete / MarkBatchesComplete

**Handler**: `infra/embedding_step_functions/unified_embedding/handlers/mark_batches_complete.py`

**What it does**:
1. Receives `poll_results_s3_key` and `poll_results_s3_bucket` from the workflow
2. Loads `poll_results` array from S3 (lines 83-104)
3. Extracts `batch_id` from each result in the array (lines 113-119)
4. Fetches batch summaries from DynamoDB using the batch_ids (line 140)
5. Updates each batch summary status to `COMPLETED` (line 144)

**Input**:
```json
{
  "poll_results_s3_key": "poll_results/{execution_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Process** (lines 83-104):
1. Downloads `poll_results.json` from S3
2. Loads the array:
   ```json
   [
     {
       "batch_id": "uuid-1",  // ← Extract this
       "openai_batch_id": "batch_xxx1",
       ...
     },
     {
       "batch_id": "uuid-2",  // ← Extract this
       ...
     },
     ...
   ]
   ```

**Extract batch_ids** (lines 113-119):
```python
batch_ids = []
for result in poll_results:
    if isinstance(result, dict) and "batch_id" in result:
        batch_id = result["batch_id"]
        if batch_id and batch_id not in batch_ids:
            batch_ids.append(batch_id)
```

**Fetch batch summaries** (line 140):
```python
batch_summaries = dynamo_client.get_batch_summaries_by_batch_ids(batch_ids)
```

**Update status** (line 144):
```python
for batch_summary in batch_summaries:
    batch_summary.status = "COMPLETED"
dynamo_client.update_batch_summaries(batch_summaries)
```

**Key Point**: The handler uses the `batch_id` values from the `poll_results` array to fetch the actual batch summaries from DynamoDB, then updates their status.

---

## Summary: How batch_summary Information Flows

1. **ListPendingBatches** → Returns batch summaries with `batch_id`
2. **PollBatches** → Processes each batch, includes `batch_id` in result
3. **NormalizePollBatchesData** → Combines all results (each with `batch_id`) and uploads to S3
4. **Workflow Steps** → Pass `poll_results_s3_key` through (data stays in S3)
5. **MarkBatchesComplete** →
   - Loads `poll_results` array from S3
   - Extracts `batch_id` from each result
   - Fetches batch summaries from DynamoDB using batch_ids
   - Updates status to `COMPLETED`

## Critical Dependencies

1. **`batch_id` must be preserved** in each poll result through the entire workflow
2. **`poll_results_s3_key` must be passed** through all workflow steps
3. **The S3 file must contain** an array where each element has a `batch_id` field
4. **DynamoDB must have** the batch summaries with matching `batch_id` values

## Potential Issues

1. **Missing `batch_id` in poll results**: If a poll result doesn't have `batch_id`, it won't be marked complete
2. **Lost `poll_results_s3_key`**: If the S3 key isn't passed through workflow steps, the handler can't load the data
3. **S3 file corruption**: If the S3 file is corrupted or missing, batch IDs can't be extracted
4. **DynamoDB mismatch**: If batch_ids in poll_results don't match DynamoDB records, those batches won't be updated

## Verification

To verify the flow is working:
1. Check that `poll_results.json` in S3 contains `batch_id` in each result
2. Check that `poll_results_s3_key` is present in the workflow state before `MarkBatchesComplete`
3. Check logs from `MarkBatchesComplete` to see how many batch_ids were extracted
4. Check DynamoDB to verify batch summaries were updated to `COMPLETED`

