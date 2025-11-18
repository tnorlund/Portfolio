# Ingestion Step Function Data Flow Documentation

## Overview

The ingestion step functions (line and word) use an S3 manifest approach to pass data between steps, avoiding Step Functions' 256KB payload limit. This document describes the data structures and flow for each step.

## Architecture Principles

1. **S3-Only for Large Payloads**: Large arrays (poll_results, chunks, groups) are always stored in S3 and referenced via S3 keys
2. **Manifest Pattern**: When arrays exceed size limits, we create a manifest file in S3 with metadata, and pass only the manifest S3 key
3. **Consistent Data Passing**: S3 keys (`poll_results_s3_key`, `chunks_s3_key`, `groups_s3_key`) are passed through all steps to ensure downstream steps can load data when needed

## Step-by-Step Data Flow

### Step 1: ListPendingBatches

**Lambda**: `embedding-list-pending`

**Input**:
```json
{
  "batch_type": "line" | "word",
  "execution_id": "execution-uuid"
}
```

**Output** (`$.list_result`):
```json
{
  "total_batches": 465,
  "batch_indices": [0, 1, 2, ...],  // Array of indices
  "pending_batches": [...],  // Full batch objects (if small)
  "manifest_s3_key": "manifests/{execution_id}/batches.json",  // If use_s3=true
  "manifest_s3_bucket": "bucket-name",
  "use_s3": true,  // true if batches array > 100KB
  "execution_id": "execution-uuid"
}
```

**Purpose**: Lists pending batches from DynamoDB. If the batches array is large, uploads to S3 manifest.

---

### Step 2: NormalizePendingBatches

**Type**: Pass State

**Input**: `$.list_result`

**Output** (`$.poll_batches_data`):
```json
{
  "batch_indices": [0, 1, 2, ...],
  "pending_batches": [...],  // May be null if use_s3=true
  "manifest_s3_key": "manifests/{execution_id}/batches.json",
  "manifest_s3_bucket": "bucket-name",
  "use_s3": true,
  "execution_id": "execution-uuid",
  "total_batches": 465
}
```

**Purpose**: Normalizes the data structure for the PollBatches Map state.

---

### Step 3: PollBatches (Map State)

**Lambda**: `embedding-line-poll` or `embedding-word-poll`

**Input** (per iteration):
```json
{
  "batch_index": 0,
  "manifest_s3_key": "manifests/{execution_id}/batches.json",
  "manifest_s3_bucket": "bucket-name",
  "pending_batches": [...],  // May be null if use_s3=true
  "skip_sqs_notification": true
}
```

**Output** (`$.poll_results` - array of results):
```json
[
  {
    "batch_id": "batch-uuid",
    "result_s3_key": "poll_results/{batch_id}/result.json",
    "result_s3_bucket": "bucket-name"
  },
  ...
]
```

**Purpose**: Polls each batch from OpenAI. Each Lambda uploads its full result to S3 and returns only a small S3 reference (~100 bytes) to keep the Map state output small.

**S3 Structure**: Each batch result is stored at `poll_results/{batch_id}/result.json`

---

### Step 4: NormalizePollBatchesData

**Lambda**: `embedding-normalize-poll-batches`

**Input**:
```json
{
  "batch_id": "execution-uuid",
  "poll_results": [
    {"batch_id": "...", "result_s3_key": "...", "result_s3_bucket": "..."},
    ...
  ]
}
```

**Output** (`$.poll_results_data`):
```json
{
  "batch_id": "execution-uuid",
  "poll_results": null,  // Always null (always in S3)
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Purpose**:
- Downloads individual batch results from S3 (from Step 3)
- Combines them into a single array
- Uploads combined results to S3
- Returns only S3 reference

**S3 Structure**: Combined results stored at `poll_results/{batch_id}/poll_results.json`

---

### Step 5: SplitIntoChunks

**Lambda**: `embedding-split-chunks`

**Input**:
```json
{
  "batch_id": "execution-uuid",
  "poll_results": null,  // null because it's in S3
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Output** (`$.chunked_data`):
```json
{
  "batch_id": "execution-uuid",
  "chunks": [...],  // Array of chunk objects (if use_s3=false)
  "chunks_s3_key": "chunks/{batch_id}/chunks.json",  // If use_s3=true
  "chunks_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "total_chunks": 47,
  "use_s3": true,  // true if chunks array > 150KB
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",  // Pass through
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"  // Pass through
}
```

**Purpose**:
- Loads `poll_results` from S3 using `poll_results_s3_key`
- Splits delta results into chunks of 10 deltas each
- If chunks array is large, uploads to S3 manifest
- Passes through `poll_results_s3_key/bucket` for downstream steps

**S3 Structure**: Chunks stored at `chunks/{batch_id}/chunks.json` (array of chunk objects)

---

### Step 6: LoadChunksFromS3 (Conditional)

**Lambda**: `embedding-split-chunks` (operation: `load_chunks_from_s3`)

**Input**:
```json
{
  "operation": "load_chunks_from_s3",
  "chunks_s3_key": "chunks/{batch_id}/chunks.json",
  "chunks_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "batch_id": "execution-uuid",
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Output** (`$.chunked_data`):
```json
{
  "batch_id": "execution-uuid",
  "chunks": [
    {"chunk_index": 0, "batch_id": "...", "delta_results": null},  // delta_results null when use_s3=true
    {"chunk_index": 1, "batch_id": "...", "delta_results": null},
    ...
  ],
  "total_chunks": 47,
  "use_s3": true,
  "chunks_s3_key": "chunks/{batch_id}/chunks.json",  // Pass through
  "chunks_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",  // Pass through
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Purpose**: Creates chunk index array instead of loading full chunks. Each processing Lambda will download its specific chunk from S3 using `chunks_s3_key` and `chunk_index`.

---

### Step 7: ProcessChunksInParallel (Map State)

**Lambda**: `embedding-vector-compact` (operation: `process_chunk`)

**Input** (per iteration):
```json
{
  "chunk": {
    "chunk_index": 0,
    "batch_id": "execution-uuid",
    "delta_results": null  // null when use_s3=true
  },
  "chunks_s3_key": "chunks/{batch_id}/chunks.json",
  "chunks_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "use_s3": true,
  "database": "lines" | "words"
}
```

**Output** (`$.chunk_results` - array of results):
```json
[
  {
    "intermediate_key": "intermediate/{batch_id}/chunk-0/"
  },
  {
    "intermediate_key": "intermediate/{batch_id}/chunk-1/"
  },
  ...
]
```

**Purpose**: Processes each chunk in parallel. Downloads chunk from S3 if `use_s3=true`, processes deltas, uploads intermediate snapshot to S3.

**S3 Structure**: Intermediate snapshots stored at `intermediate/{batch_id}/chunk-{index}/`

---

### Step 8: GroupChunksForMerge

**Type**: Pass State

**Input**: `$.chunked_data` and `$.chunk_results`

**Output**:
```json
{
  "batch_id": "execution-uuid",
  "total_chunks": 47,
  "chunk_results": [
    {"intermediate_key": "intermediate/{batch_id}/chunk-0/"},
    ...
  ],
  "group_size": 10,
  "poll_results": null,  // null (in S3)
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",  // Pass through
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Purpose**: Prepares data for chunk grouping. **CRITICAL**: Passes through `poll_results_s3_key/bucket` for downstream steps.

---

### Step 9: CreateChunkGroups (Conditional - if total_chunks > 4)

**Lambda**: `embedding-create-chunk-groups`

**Input**:
```json
{
  "batch_id": "execution-uuid",
  "chunk_results": [
    {"intermediate_key": "intermediate/{batch_id}/chunk-0/"},
    ...
  ],
  "group_size": 10,
  "poll_results": null,  // null (in S3)
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Output** (`$.chunk_groups`):
```json
{
  "batch_id": "execution-uuid",
  "groups": [
    {
      "group_index": 0,
      "chunk_group": [
        {"intermediate_key": "intermediate/{batch_id}/chunk-0/"},
        ...
      ],
      "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
      "groups_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
    },
    ...
  ],
  "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
  "groups_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "total_groups": 5,
  "use_s3": true,  // Always true (always uploads to S3)
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",  // Pass through
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Purpose**: Groups chunks into groups of 10 for hierarchical merging. Always uploads groups to S3. Passes through `poll_results_s3_key/bucket`.

**S3 Structure**: Groups stored at `chunk_groups/{batch_id}/groups.json` (array of group arrays)

---

### Step 10: LoadChunkGroupsFromS3 (Conditional)

**Lambda**: `embedding-create-chunk-groups` (operation: `load_groups_from_s3`)

**Input**:
```json
{
  "operation": "load_groups_from_s3",
  "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
  "groups_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "batch_id": "execution-uuid",
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Output** (`$.chunk_groups`):
```json
{
  "batch_id": "execution-uuid",
  "groups": [
    {
      "group_index": 0,
      "chunk_group": null,  // null (will be downloaded by Lambda)
      "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
      "groups_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
    },
    ...
  ],
  "total_groups": 5,
  "use_s3": true,
  "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
  "groups_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",  // Pass through
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Purpose**: Creates group index array instead of loading full groups. Each merge Lambda will download its specific group from S3.

---

### Step 11: MergeChunkGroupsInParallel (Map State)

**Lambda**: `embedding-vector-compact` (operation: `merge_chunk_group`)

**Input** (per iteration):
```json
{
  "chunk_group": null,  // null (will be downloaded from S3)
  "batch_id": "execution-uuid-group-0",
  "group_index": 0,
  "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
  "groups_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "database": "lines" | "words"
}
```

**Output** (`$.merged_groups` - array of results):
```json
[
  {
    "intermediate_key": "intermediate/{batch_id}-group-0-group-0/merged/"
  },
  {
    "intermediate_key": "intermediate/{batch_id}-group-1-group-1/merged/"
  },
  ...
]
```

**Purpose**: Merges chunk groups in parallel. Downloads group from S3, merges intermediate snapshots, uploads merged snapshot to S3.

**S3 Structure**: Merged snapshots stored at `intermediate/{batch_id}-group-{index}-group-{index}/merged/`

---

### Step 12: PrepareHierarchicalFinalMerge

**Type**: Pass State

**Input**: `$.chunk_groups` and `$.merged_groups`

**Output**:
```json
{
  "batch_id": "execution-uuid",
  "operation": "final_merge",
  "chunk_results": [
    {"intermediate_key": "intermediate/{batch_id}-group-0-group-0/merged/"},
    ...
  ],
  "poll_results": null,  // null (in S3)
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Purpose**: Prepares data for final merge. Gets `poll_results_s3_key/bucket` from `$.chunk_groups`.

---

### Step 13: PrepareFinalMerge (Alternative - if total_chunks <= 4)

**Type**: Pass State

**Input**: `$.chunked_data` and `$.chunk_results`

**Output**:
```json
{
  "batch_id": "execution-uuid",
  "chunk_results": [
    {"intermediate_key": "intermediate/{batch_id}/chunk-0/"},
    ...
  ],
  "operation": "final_merge",
  "poll_results": null,  // null (in S3)
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Purpose**: Prepares data for final merge when skipping hierarchical merge. Gets `poll_results_s3_key/bucket` from `$.chunked_data`.

---

### Step 14: FinalMerge

**Lambda**: `embedding-vector-compact` (operation: `final_merge`)

**Input**:
```json
{
  "operation": "final_merge",
  "batch_id": "execution-uuid",
  "chunk_results": [
    {"intermediate_key": "intermediate/{batch_id}/chunk-0/"},
    ...
  ],
  "poll_results": null,  // null (in S3)
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
  "database": "lines" | "words"
}
```

**Output** (`$.final_merge_result`):
```json
{
  "statusCode": 200,
  "batch_id": "execution-uuid",
  "snapshot_key": "lines/snapshot/latest/",
  "total_embeddings": 26501,
  "processing_time_seconds": 45.2,
  "message": "Final merge completed successfully",
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",  // Pass through
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Purpose**: Merges all intermediate snapshots into final snapshot. Downloads `poll_results` from S3 if needed for metadata. Uploads final snapshot to S3.

**S3 Structure**: Final snapshot stored at `{database}/snapshot/latest/`

---

### Step 15: MarkBatchesComplete

**Lambda**: `embedding-mark-batches-complete`

**Input**:
```json
{
  "batch_id": "execution-uuid",
  "poll_results": null,  // null (in S3)
  "poll_results_s3_key": "poll_results/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843"
}
```

**Output**:
```json
{
  "statusCode": 200,
  "batches_marked_complete": 465,
  "message": "Batches marked as complete"
}
```

**Purpose**: Loads `poll_results` from S3, marks batches as `COMPLETED` in DynamoDB.

---

## S3 Bucket Structure

```
chromadb-dev-shared-buckets-vectors-c239843/
├── manifests/
│   └── {execution_id}/
│       └── batches.json                    # Batch manifest (if batches > 100KB)
├── poll_results/
│   └── {batch_id}/
│       ├── result.json                      # Individual batch result (from PollBatch)
│       └── poll_results.json                # Combined poll results (from NormalizePollBatchesData)
├── chunks/
│   └── {batch_id}/
│       └── chunks.json                      # Chunks manifest (if chunks > 150KB)
├── chunk_groups/
│   └── {batch_id}/
│       └── groups.json                      # Groups manifest (always)
└── intermediate/
    └── {batch_id}/
        ├── chunk-{index}/                   # Individual chunk snapshots
        └── {batch_id}-group-{index}-group-{index}/
            └── merged/                      # Merged group snapshots
```

## Key Data Passing Rules

1. **Always Pass S3 Keys**: `poll_results_s3_key` and `poll_results_s3_bucket` must be passed through ALL steps from `SplitIntoChunks` onwards
2. **Use Root Level**: After `GroupChunksForMerge`, S3 keys are at root level (`$.poll_results_s3_key`), not nested in `chunked_data`
3. **Null Indicates S3**: When `poll_results` is `null`, it means the data is in S3 and must be loaded using `poll_results_s3_key`
4. **Consistent Pattern**: All large arrays follow the same pattern: upload to S3, return S3 key, pass key through all steps

## Common Issues and Fixes

### Issue: `poll_results_s3_key` is null in FinalMerge

**Cause**: S3 keys not passed through `GroupChunksForMerge` or `CreateChunkGroups`

**Fix**: Ensure `GroupChunksForMerge` passes through `poll_results_s3_key/bucket` from `chunked_data`, and `CreateChunkGroups` receives them from root level (not `chunked_data`)

### Issue: JSONPath error accessing `$.chunked_data.poll_results_s3_key`

**Cause**: After `GroupChunksForMerge`, `chunked_data` is no longer in the state

**Fix**: Update `CreateChunkGroups` to access `$.poll_results_s3_key` (root level) instead of `$.chunked_data.poll_results_s3_key`

### Issue: Step Functions payload limit exceeded

**Cause**: Large arrays passed inline instead of using S3

**Fix**: Ensure all handlers check payload size and upload to S3 when approaching limits

