# How We Use S3 Information to Determine Batch Completion

## Overview

This document explains the complete flow of how we use S3-stored information to determine which batch summaries have successfully completed and need to be marked as `COMPLETED` in DynamoDB.

## The Complete Flow

### Step 1: List Pending Batches

**Handler**: `ListPendingWordBatches` / `ListPendingLineBatches`

**What it does**:
- Queries DynamoDB for `BatchSummary` records with `status = "PENDING"`
- Returns an array of batch summaries, each containing:
  - `batch_id`: Unique identifier for the batch
  - `openai_batch_id`: OpenAI batch ID to poll
  - `status`: Current status (should be "PENDING")

**Output**:
```json
{
  "batch_summaries": [
    {
      "batch_id": "uuid-1",
      "openai_batch_id": "batch_xxx1",
      "status": "PENDING"
    },
    {
      "batch_id": "uuid-2",
      "openai_batch_id": "batch_xxx2",
      "status": "PENDING"
    }
  ]
}
```

---

### Step 2: Poll Each Batch (Map State)

**Handler**: `PollBatch` (runs in parallel for each batch)

**What it does**:
1. Takes a single `BatchSummary` from Step 1
2. Calls OpenAI API: `openai.batches.retrieve(openai_batch_id)`
3. Checks the batch status:
   - `"completed"`: Batch finished successfully
   - `"processing"`: Still in progress
   - `"failed"`: Batch failed
   - `"expired"`: Batch expired
   - `"cancelled"`: Batch was cancelled

4. **If status is "completed"**:
   - Downloads the batch results from OpenAI
   - Processes the embeddings
   - Uploads individual result to S3: `poll_results/{batch_id}/result.json`
   - Returns S3 reference (not full data)

**Output** (for each batch):
```json
{
  "batch_id": "uuid-1",
  "openai_batch_id": "batch_xxx1",
  "batch_status": "completed",
  "result_s3_key": "poll_results/uuid-1/result.json",
  "result_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "embedding_count": 100,
  "storage": "s3_delta"
}
```

**S3 File Structure** (`poll_results/{batch_id}/result.json`):
```json
{
  "batch_id": "uuid-1",
  "openai_batch_id": "batch_xxx1",
  "batch_status": "completed",
  "embedding_count": 100,
  "results": [
    {
      "id": "image-1/receipt-1/line-1/word-1",
      "embedding": [0.1, 0.2, ...],
      ...
    }
  ],
  "processing_time_seconds": 45.2
}
```

**Key Point**: Each `PollBatch` Lambda uploads its result to S3 and returns only a small S3 reference (~100 bytes) to avoid Step Functions payload limits.

---

### Step 3: Normalize Poll Results

**Handler**: `NormalizePollBatchesData`

**What it does**:
1. Receives array of S3 references from Step 2:
   ```json
   [
     {
       "batch_id": "uuid-1",
       "result_s3_key": "poll_results/uuid-1/result.json",
       "result_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
     },
     {
       "batch_id": "uuid-2",
       "result_s3_key": "poll_results/uuid-2/result.json",
       "result_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
     }
   ]
   ```

2. **Downloads each individual result from S3**:
   - For each S3 reference, downloads `poll_results/{batch_id}/result.json`
   - Each file contains the full result with `batch_id`

3. **Combines all results into a single array**:
   ```json
   [
     {
       "batch_id": "uuid-1",
       "openai_batch_id": "batch_xxx1",
       "batch_status": "completed",
       "embedding_count": 100,
       ...
     },
     {
       "batch_id": "uuid-2",
       "openai_batch_id": "batch_xxx2",
       "batch_status": "completed",
       "embedding_count": 150,
       ...
     }
   ]
   ```

4. **Uploads combined array to S3**:
   - Location: `poll_results/{execution_id}/poll_results.json`
   - This is the **single source of truth** for which batches completed

5. **Returns S3 reference** (not the full array):
   ```json
   {
     "batch_id": "execution-uuid",
     "poll_results": null,  // Always null (always in S3)
     "poll_results_s3_key": "poll_results/{execution_id}/poll_results.json",
     "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
   }
   ```

**Key Point**: The combined `poll_results.json` file in S3 contains an array where **each element has a `batch_id` field**. This is what `MarkBatchesComplete` needs to know which batches to mark as complete.

---

### Step 4: Process Embeddings (Split, Process, Merge)

**Steps**: `SplitIntoChunks` → `ProcessChunks` → `FinalMerge`

**What happens**:
- The embeddings are processed and merged into ChromaDB snapshots
- **Important**: `poll_results_s3_key` must be preserved through all these steps
- The actual `poll_results` data stays in S3 (not passed through Step Functions)

---

### Step 5: Mark Batches Complete

**Handler**: `MarkBatchesComplete`

**What it needs**:
- `poll_results_s3_key`: S3 key where combined poll results are stored
- `poll_results_s3_bucket`: S3 bucket name

**What it does**:

1. **Downloads the combined poll results from S3**:
   ```python
   s3_client.download_file(
       poll_results_s3_bucket,
       poll_results_s3_key,
       tmp_file_path
   )
   ```

2. **Parses the JSON file**:
   ```python
   with open(tmp_file_path, "r") as f:
       poll_results = json.load(f)
   ```

   The file contains:
   ```json
   [
     {
       "batch_id": "uuid-1",
       "openai_batch_id": "batch_xxx1",
       "batch_status": "completed",
       "embedding_count": 100,
       ...
     },
     {
       "batch_id": "uuid-2",
       "openai_batch_id": "batch_xxx2",
       "batch_status": "completed",
       "embedding_count": 150,
       ...
     }
   ]
   ```

3. **Extracts `batch_id`s from the results**:
   ```python
   batch_ids = []
   for result in poll_results:
       if isinstance(result, dict) and "batch_id" in result:
           batch_id = result["batch_id"]
           if batch_id and batch_id not in batch_ids:
               batch_ids.append(batch_id)
   ```

   Result: `["uuid-1", "uuid-2", ...]`

4. **Fetches corresponding `BatchSummary` records from DynamoDB**:
   ```python
   batch_summaries = dynamo_client.get_batch_summaries_by_batch_ids(batch_ids)
   ```

5. **Updates status to "COMPLETED"**:
   ```python
   for batch_summary in batch_summaries:
       batch_summary.status = "COMPLETED"
       dynamo_client.update_batch_summary(batch_summary)
   ```

6. **Returns summary**:
   ```json
   {
     "batches_marked": 2,
     "batch_ids": ["uuid-1", "uuid-2"]
   }
   ```

---

## How We Know Batches Completed Successfully

### The Logic

1. **OpenAI Status Check**: Each `PollBatch` Lambda checks OpenAI API for batch status. If status is `"completed"`, it means OpenAI successfully processed the batch.

2. **Result Download**: If status is `"completed"`, the Lambda downloads the results from OpenAI. If this succeeds, we have the actual embeddings.

3. **S3 Storage**: The results are uploaded to S3. If the upload succeeds, we have persistent storage of the completion.

4. **Combined Results**: `NormalizePollBatchesData` combines all completed batches into a single S3 file. If a batch appears in this file, it means:
   - OpenAI marked it as completed
   - Results were successfully downloaded
   - Results were successfully stored in S3

5. **Final Merge Success**: The embeddings are processed and merged into ChromaDB. If `MarkBatchesComplete` runs, it means the entire workflow succeeded (including FinalMerge).

6. **DynamoDB Update**: `MarkBatchesComplete` extracts `batch_id`s from the S3 file and updates DynamoDB. If a `batch_id` is in the S3 file, it means:
   - The batch completed successfully at OpenAI
   - The embeddings were processed successfully
   - The data was merged into ChromaDB successfully
   - **Therefore, the batch summary should be marked as COMPLETED**

---

## Why We Use S3

### Problem: Step Functions Payload Limits

AWS Step Functions has a **256KB limit** for state data. With hundreds of batches, each with thousands of embeddings, the payload can easily exceed this limit.

### Solution: S3 References

Instead of passing the full `poll_results` array through Step Functions, we:
1. Store the data in S3
2. Pass only small S3 references (~100 bytes) through Step Functions
3. Download from S3 only when needed (in `MarkBatchesComplete`)

### Benefits

- **Avoids payload limits**: S3 references are tiny
- **Persistent storage**: Data survives Step Functions execution
- **Efficient**: Only download when needed
- **Scalable**: Works with any number of batches

---

## Current Problem

**The `poll_results_s3_key` is being lost** somewhere between `NormalizePollBatchesData` and `MarkBatchesComplete`.

**Impact**: Without the S3 key, `MarkBatchesComplete` cannot:
- Download the combined poll results
- Extract `batch_id`s
- Update DynamoDB batch summaries

**Solution**: Ensure `poll_results_s3_key` is preserved at every step in the workflow.

---

## Example: Complete Flow

### Execution: `8ee0d036-2881-4a5d-b6d5-57022197226a`

1. **ListPendingBatches** finds 10 batches with `status = "PENDING"`

2. **PollBatches** (Map) polls each batch:
   - 8 batches have `status = "completed"` → Results uploaded to S3
   - 2 batches have `status = "processing"` → Skipped

3. **NormalizePollBatchesData**:
   - Downloads 8 individual results from S3
   - Combines into array with 8 elements
   - Uploads to: `poll_results/8ee0d036-2881-4a5d-b6d5-57022197226a/poll_results.json`
   - Returns: `poll_results_s3_key = "poll_results/8ee0d036-2881-4a5d-b6d5-57022197226a/poll_results.json"`

4. **SplitIntoChunks** → **ProcessChunks** → **FinalMerge**:
   - Processes embeddings
   - Merges into ChromaDB
   - **Should preserve**: `poll_results_s3_key` through all steps

5. **MarkBatchesComplete**:
   - **Should receive**: `poll_results_s3_key = "poll_results/8ee0d036-2881-4a5d-b6d5-57022197226a/poll_results.json"`
   - **Should download**: The file from S3
   - **Should extract**: 8 `batch_id`s from the file
   - **Should update**: 8 `BatchSummary` records to `status = "COMPLETED"`

**Current Issue**: Step 5 fails because `poll_results_s3_key` is `null`.

