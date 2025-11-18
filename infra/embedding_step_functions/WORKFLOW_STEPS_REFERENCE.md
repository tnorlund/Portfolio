# Step Functions Workflow Steps Reference

This document describes each step in the line and word embedding ingestion workflows, including input/output formats, differences between the two workflows, troubleshooting guides, and visual flow diagrams.

## Quick Navigation

- [Workflow Overview](#workflow-overview)
- [Visual Flow Diagrams](#visual-flow-diagrams)
- [Step-by-Step Reference](#step-by-step-reference)
- [Troubleshooting Guide](#troubleshooting-guide)
- [Performance Characteristics](#performance-characteristics)
- [Quick Reference Tables](#quick-reference-tables)
- [Quick Start Guide](#quick-start-guide)
- [Real-World Examples](#real-world-examples)
- [Glossary](#glossary)

---

## Quick Start Guide

### Running a Workflow

```bash
# Start line ingestion workflow
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:us-east-1:ACCOUNT:stateMachine:line-ingest-sf-dev-1554303" \
  --name "manual-run-$(date +%s)"

# Start word ingestion workflow
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:us-east-1:ACCOUNT:stateMachine:word-ingest-sf-dev-8fa425a" \
  --name "manual-run-$(date +%s)"
```

### Monitoring an Execution

```bash
# Get execution status
EXECUTION_ARN="arn:aws:states:us-east-1:ACCOUNT:execution:workflow-name:execution-id"
aws stepfunctions describe-execution --execution-arn "$EXECUTION_ARN"

# Get execution history (last 50 events)
aws stepfunctions get-execution-history \
  --execution-arn "$EXECUTION_ARN" \
  --max-results 50 \
  --reverse-order

# Get failed tasks
aws stepfunctions get-execution-history \
  --execution-arn "$EXECUTION_ARN" \
  --query 'events[?type==`TaskFailed`]' \
  --output json | jq '.[] | {name: .taskFailedEventDetails.resource, error: .taskFailedEventDetails.error}'
```

### Common Commands

```bash
# List recent executions
aws stepfunctions list-executions \
  --state-machine-arn "arn:..." \
  --max-results 10

# Check batch summaries
python dev.reset_batch_status.py --status PENDING dev

# Check S3 files for a batch
BATCH_ID="f8205d14-9360-4a03-a201-6e55d44be986"
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/chunk_groups/$BATCH_ID/
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/intermediate/$BATCH_ID/
```

---

## Workflow Overview

Both workflows follow the same pattern:
1. **List Pending Batches** → Find batches ready for processing
2. **Poll Batches** → Download completed embeddings from OpenAI
3. **Split Into Chunks** → Partition data for parallel processing
4. **Process Chunks** → Create intermediate ChromaDB snapshots
5. **Create Chunk Groups** → Group chunks for hierarchical merging
6. **Merge Chunk Groups** → Merge groups in parallel
7. **Final Merge** → Merge all groups into final snapshot
8. **Mark Batches Complete** → Update batch status in DynamoDB

### Key Design Principles

- **Parallel Processing**: Chunks processed concurrently (max 10), groups merged concurrently (max 6)
- **S3 Offloading**: Large payloads automatically offloaded to S3 to avoid 256KB Step Functions limit
- **Hierarchical Merging**: Groups of 10 chunks merged first, then groups merged into final snapshot
- **Lock Management**: Only final merge acquires DynamoDB lock to prevent concurrent writes
- **Error Resilience**: Retry policies with exponential backoff on all Lambda tasks

---

## Visual Flow Diagrams

### Main Workflow Flow

```
┌─────────────────────┐
│ ListPendingBatches  │ → [pending_batches: [...]]
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   PollBatches (Map)  │ → [poll_results: [...]]
│  MaxConcurrency: 50  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  SplitIntoChunks    │ → [chunked_data: {...}]
└──────────┬──────────┘
           │
           ├─ use_s3? ──┐
           │            │
           ▼            ▼
    ┌──────────┐  ┌──────────────┐
    │  Inline  │  │ LoadChunks   │
    │  Chunks  │  │  FromS3       │
    └────┬─────┘  └──────┬───────┘
         │                │
         └────────┬───────┘
                  ▼
    ┌─────────────────────────┐
    │ ProcessChunksInParallel │ → [chunk_results: [...]]
    │   MaxConcurrency: 10    │
    └──────────┬──────────────┘
               │
               ▼
    ┌─────────────────────┐
    │ GroupChunksForMerge  │
    └──────────┬──────────┘
               │
               ├─ total_chunks > 4? ──┐
               │                      │
               ▼                      ▼
    ┌──────────────────┐    ┌─────────────────┐
    │ CreateChunkGroups│    │ PrepareFinalMerge│
    └──────────┬───────┘    └────────┬─────────┘
               │                     │
               ├─ use_s3? ──┐        │
               │            │        │
               ▼            ▼        │
    ┌──────────────┐  ┌──────────────┐
    │  Inline      │  │ LoadGroups   │
    │  Groups      │  │  FromS3      │
    └──────┬───────┘  └──────┬───────┘
           │                  │
           └─────────┬────────┘
                     ▼
    ┌────────────────────────────┐
    │ MergeChunkGroupsInParallel │ → [merged_groups: [...]]
    │   MaxConcurrency: 6        │
    └──────────┬─────────────────┘
               │
               ▼
    ┌──────────────────────────┐
    │ PrepareHierarchicalFinal │
    │         Merge            │
    └──────────┬───────────────┘
               │
               ▼
    ┌─────────────────────┐
    │    FinalMerge       │ → [final_merge_result: {...}]
    │  (with DynamoDB lock)│
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │ MarkBatchesComplete │ → [mark_complete_result: {...}]
    └─────────────────────┘
```

### Data Size Decision Tree

```
SplitIntoChunks Output
│
├─ Size < 150KB?
│  └─→ Return inline chunks
│
└─ Size ≥ 150KB?
   └─→ Upload to S3
       └─→ Return S3 reference

CreateChunkGroups Output
│
└─→ Always upload to S3 (aggressive approach)
    └─→ Return S3 reference

poll_results
│
├─ Size < 100KB?
│  └─→ Return inline
│
└─ Size ≥ 100KB?
   └─→ Upload to S3
       └─→ Return null with S3 keys
```

---

## Step 1: ListPendingBatches / ListPendingWordBatches

**Purpose**: Query DynamoDB for batches with `PENDING` status

**Lambda**: `embedding-list-pending`

**Input**:
```json
{
  "batch_type": "line",  // or "word"
  "execution_id": "{execution_name}"  // Step Functions execution name
}
```

**Output** (small payload - inline mode):
```json
{
  "pending_batches": [
    {
      "batch_id": "uuid",
      "openai_batch_id": "batch_xxx"
    }
  ],
  "batch_indices": [0, 1, 2, ...],
  "use_s3": false,
  "manifest_s3_key": null,
  "manifest_s3_bucket": null,
  "execution_id": "{execution_name}",
  "total_batches": 10
}
```

**Output** (large payload - S3 manifest mode):
```json
{
  "pending_batches": null,
  "batch_indices": [0, 1, 2, ..., 99],
  "use_s3": true,
  "manifest_s3_key": "poll_manifests/{execution_id}/manifest.json",
  "manifest_s3_bucket": "chromadb-dev-...",
  "execution_id": "{execution_name}",
  "total_batches": 100
}
```

**Manifest Structure** (stored in S3):
```json
{
  "execution_id": "{execution_name}",
  "created_at": "2024-01-15T10:30:00Z",
  "total_batches": 100,
  "batch_type": "line",
  "batches": [
    {
      "index": 0,
      "batch_id": "uuid",
      "openai_batch_id": "batch_xxx"
    },
    ...
  ]
}
```

**Behavior**:
- If payload size > 150KB OR batch count > 50 → Creates S3 manifest
- Otherwise → Returns batches inline (backward compatible)
- Always returns consistent structure with `use_s3` flag

**Differences**:
- Line workflow uses `ListPendingBatches`, word workflow uses `ListPendingWordBatches`
- Only difference is `batch_type` parameter ("line" vs "word")

---

## Step 2: PollBatches / PollWordBatches

**Purpose**: Poll OpenAI API for each batch's completion status and download results

**Lambda**: `embedding-line-poll` or `embedding-word-poll`

**Type**: Map state (processes batches in parallel, max concurrency: 50)

**Step Function Flow**:
1. `ListPendingBatches` → Returns batches (inline or S3 manifest)
2. `CheckPendingBatches` → Checks if `total_batches > 0`
3. `NormalizePendingBatches` → Normalizes data structure
4. `PollBatches` → Map state iterates over `batch_indices`

**Input** (per batch iteration - S3 manifest mode):
```json
{
  "batch_index": 0,
  "manifest_s3_key": "poll_manifests/{execution_id}/manifest.json",
  "manifest_s3_bucket": "chromadb-dev-...",
  "pending_batches": null,
  "skip_sqs_notification": true
}
```

**Input** (per batch iteration - inline mode):
```json
{
  "batch_index": 0,
  "manifest_s3_key": null,
  "manifest_s3_bucket": null,
  "pending_batches": [
    { "batch_id": "uuid1", "openai_batch_id": "batch_xxx1" },
    { "batch_id": "uuid2", "openai_batch_id": "batch_xxx2" },
    ...
  ],
  "skip_sqs_notification": true
}
```

**Lambda Processing**:
- If `manifest_s3_key` provided: Downloads manifest from S3, looks up batch info using `batch_index`
- If `pending_batches` provided: Uses `pending_batches[batch_index]` to get batch info
- Otherwise (backward compatible): Uses `batch_id` and `openai_batch_id` directly from event

**Output** (per batch):
```json
{
  "batch_id": "uuid",
  "openai_batch_id": "batch_xxx",
  "batch_status": "completed",
  "action": "process_results",
  "results_count": 100,
  "delta_id": "uuid",
  "delta_key": "lines/delta/lines/receipt_lines/{delta_id}/",  // or "words/delta/words/receipt_words/{delta_id}/"
  "embedding_count": 100,
  "storage": "s3_delta",
  "collection": "lines",  // or "words"
  "database": "lines"     // or "words"
}
```

**Map Output** (aggregated):
```json
{
  "poll_results": [
    { /* batch 1 result */ },
    { /* batch 2 result */ },
    ...
  ]
}
```

**Differences**:
- Line workflow uses `embedding-line-poll`, word workflow uses `embedding-word-poll`
- `delta_key` paths differ: `lines/delta/...` vs `words/delta/...`
- `collection` and `database` fields differ: `"lines"` vs `"words"`

### Step 3: SplitIntoChunks / SplitWordIntoChunks

**Purpose**: Partition `poll_results` into chunks of 10 for parallel processing

**Lambda**: `embedding-split-chunks`

**Input**:
```json
{
  "batch_id": "{execution_name}",
  "poll_results": [ /* array of batch results */ ]
}
```

**Output** (small payload):
```json
{
  "chunked_data": {
    "batch_id": "uuid",
    "chunks": [
      {
        "chunk_index": 0,
        "batch_id": "uuid",
        "operation": "process_chunk",
        "delta_results": [ /* array of delta objects */ ]
      }
    ],
    "total_chunks": 3,
    "use_s3": false,
    "chunks_s3_key": null,
    "chunks_s3_bucket": null
  }
}
```

**Output** (large payload - uses S3):
```json
{
  "chunked_data": {
    "batch_id": "uuid",
    "chunks": [ /* array of chunk metadata */ ],
    "total_chunks": 30,
    "use_s3": true,
    "chunks_s3_key": "chunks/{batch_id}/chunks.json",
    "chunks_s3_bucket": "bucket-name"
  }
}
```

**Differences**: None - same logic for both workflows

### Step 4: CheckChunksSource / CheckWordChunksSource

**Purpose**: Determine if chunks are inline or in S3

**Type**: Choice state

**Input**: Same as `SplitIntoChunks` output

**Logic**:
- If `$.chunked_data.use_s3 == true` → `LoadChunksFromS3`
- Otherwise → `ProcessChunksInParallel`

**Differences**: None - same logic

### Step 5: LoadChunksFromS3 / LoadWordChunksFromS3

**Purpose**: Create chunk index array (doesn't load full chunks to avoid payload limits)

**Lambda**: `embedding-split-chunks` (with `operation: "load_chunks_from_s3"`)

**Input**:
```json
{
  "chunks_s3_key": "chunks/{batch_id}/chunks.json",
  "chunks_s3_bucket": "bucket-name",
  "batch_id": "uuid"
}
```

**Output**:
```json
{
  "chunked_data": {
    "batch_id": "uuid",
    "chunks": [
      {
        "chunk_index": 0,
        "batch_id": "uuid",
        "chunks_s3_key": "chunks/{batch_id}/chunks.json",
        "chunks_s3_bucket": "bucket-name",
        "operation": "process_chunk",
        "delta_results": null  // Will be downloaded by processing Lambda
      }
    ],
    "total_chunks": 30,
    "use_s3": true,
    "chunks_s3_key": "chunks/{batch_id}/chunks.json",
    "chunks_s3_bucket": "bucket-name"
  }
}
```

**Differences**: None - same logic

### Step 6: NormalizeChunkData / NormalizeWordChunkData

**Purpose**: Ensure `chunks_s3_key` and `chunks_s3_bucket` always exist (even if `null`)

**Type**: Pass state

**Input**: `chunked_data` from previous step

**Output**:
```json
{
  "chunked_data": {
    "batch_id": "uuid",
    "chunks": [ /* ... */ ],
    "total_chunks": 30,
    "use_s3": true,
    "chunks_s3_key": "chunks/{batch_id}/chunks.json",  // Always present
    "chunks_s3_bucket": "bucket-name"  // Always present
  }
}
```

**Differences**: None - same logic

### Step 7: ProcessChunksInParallel / ProcessWordChunksInParallel

**Purpose**: Process each chunk in parallel to create intermediate ChromaDB snapshots

**Lambda**: `embedding-vector-compact` (with `operation: "process_chunk"`)

**Type**: Map state (max concurrency: 10)

**Input** (per chunk):
```json
{
  "chunk_index": 0,
  "batch_id": "uuid",
  "operation": "process_chunk",
  "delta_results": [ /* array of delta objects */ ],
  "chunks_s3_key": "chunks/{batch_id}/chunks.json",  // May be null
  "chunks_s3_bucket": "bucket-name"  // May be null
}
```

**Output** (per chunk):
```json
{
  "intermediate_key": "intermediate/{batch_id}/chunk-0/",
  "total_embeddings": 100
}
```

**Map Output** (aggregated):
```json
{
  "chunk_results": [
    { "intermediate_key": "intermediate/{batch_id}/chunk-0/" },
    { "intermediate_key": "intermediate/{batch_id}/chunk-1/" },
    ...
  ]
}
```

**Differences**: None - same logic

### Step 8: GroupChunksForMerge

**Purpose**: Prepare data for chunk grouping

**Type**: Pass state

**Input**:
```json
{
  "chunked_data": { /* ... */ },
  "chunk_results": [ /* ... */ ],
  "poll_results": [ /* ... */ ]
}
```

**Output**:
```json
{
  "batch_id": "uuid",
  "total_chunks": 30,
  "chunk_results": [ /* ... */ ],
  "group_size": 10,
  "poll_results": [ /* ... */ ]
}
```

**Differences**: None - same logic

### Step 9: CheckChunkGroupCount / CheckWordChunkGroupCount

**Purpose**: Determine if hierarchical merge is needed (>1 group)

**Type**: Choice state

**Logic**:
- If `total_chunks <= 10` → `FinalMerge` (skip grouping)
- Otherwise → `CreateChunkGroups`

**Differences**: None - same logic

### Step 10: CreateChunkGroups

**Purpose**: Partition `chunk_results` into groups of 10 and upload to S3

**Lambda**: `embedding-create-chunk-groups`

**Input**:
```json
{
  "batch_id": "uuid",
  "chunk_results": [ /* array of intermediate_key objects */ ],
  "group_size": 10,
  "poll_results": [ /* array of batch results */ ]
}
```

**Output**:
```json
{
  "chunk_groups": {
    "batch_id": "uuid",
    "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
    "groups_s3_bucket": "bucket-name",
    "total_groups": 3,
    "use_s3": true,
    "poll_results_s3_key": "chunk_groups/{batch_id}/poll_results.json",  // If poll_results > 100KB
    "poll_results_s3_bucket": "bucket-name",
    "poll_results": null  // Set to null if stored in S3
  }
}
```

**Differences**: None - same logic

### Step 11: CheckChunkGroupsSource / CheckWordChunkGroupsSource

**Purpose**: Determine if groups are inline or in S3

**Type**: Choice state

**Logic**:
- If `$.chunk_groups.use_s3 == true` → `LoadChunkGroupsFromS3`
- Otherwise → `MergeChunkGroupsInParallel`

**Differences**: None - same logic

### Step 12: LoadChunkGroupsFromS3 / LoadWordChunkGroupsFromS3

**Purpose**: Create group index array (doesn't load full groups to avoid payload limits)

**Lambda**: `embedding-create-chunk-groups` (with `operation: "load_groups_from_s3"`)

**Input**:
```json
{
  "operation": "load_groups_from_s3",
  "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
  "groups_s3_bucket": "bucket-name",
  "batch_id": "uuid",
  "poll_results_s3_key": "chunk_groups/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "bucket-name"
}
```

**Output**:
```json
{
  "chunk_groups": {
    "batch_id": "uuid",
    "groups": [
      {
        "group_index": 0,
        "batch_id": "uuid",
        "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
        "groups_s3_bucket": "bucket-name",
        "chunk_group": null  // Will be downloaded by merge Lambda
      }
    ],
    "total_groups": 3,
    "use_s3": true,
    "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
    "groups_s3_bucket": "bucket-name",
    "poll_results_s3_key": "chunk_groups/{batch_id}/poll_results.json",
    "poll_results_s3_bucket": "bucket-name",
    "poll_results": null
  }
}
```

**Differences**: None - same logic

### Step 13: MergeChunkGroupsInParallel

**Purpose**: Merge each chunk group in parallel

**Lambda**: `embedding-vector-compact` (with `operation: "merge_chunk_group"`)

**Type**: Map state (max concurrency: 6)

**Input** (per group):
```json
{
  "chunk_group": null,  // Will be downloaded from S3 by Lambda
  "batch_id": "uuid",
  "group_index": 0,
  "groups_s3_key": "chunk_groups/{batch_id}/groups.json",
  "groups_s3_bucket": "bucket-name"
}
```

**Lambda Processing**:
1. Downloads `groups.json` from S3
2. Extracts `groups[group_index]` (array of intermediate keys)
3. Merges those intermediate snapshots into a single intermediate snapshot

**Output** (per group):
```json
{
  "intermediate_key": "intermediate/{batch_id}-group-0/merged/"
}
```

**Map Output** (aggregated):
```json
{
  "merged_groups": [
    { "intermediate_key": "intermediate/{batch_id}-group-0/merged/" },
    { "intermediate_key": "intermediate/{batch_id}-group-1/merged/" },
    ...
  ]
}
```

**Differences**: None - same logic

### Step 14: PrepareHierarchicalFinalMerge / PrepareWordHierarchicalFinalMerge

**Purpose**: Prepare data for final merge

**Type**: Pass state

**Input**:
```json
{
  "chunk_groups": {
    "batch_id": "uuid",
    "poll_results": [ /* ... */ ] or null,
    "poll_results_s3_key": "...",
    "poll_results_s3_bucket": "..."
  },
  "merged_groups": [ /* array of intermediate_key objects */ ]
}
```

**Output**:
```json
{
  "batch_id": "uuid",
  "operation": "final_merge",
  "chunk_results": [ /* array of intermediate_key objects */ ],
  "poll_results": [ /* ... */ ] or null,
  "poll_results_s3_key": "...",
  "poll_results_s3_bucket": "..."
}
```

**Differences**: None - same logic

### Step 15: FinalMerge / WordFinalMerge

**Purpose**: Merge all intermediate snapshots into final ChromaDB snapshot (with lock)

**Lambda**: `embedding-vector-compact` (with `operation: "final_merge"`)

**Input**:
```json
{
  "batch_id": "uuid",
  "operation": "final_merge",
  "chunk_results": [ /* array of intermediate_key objects */ ],
  "poll_results": [ /* ... */ ] or null,
  "poll_results_s3_key": "...",
  "poll_results_s3_bucket": "...",
  "database": "lines"  // or "words"
}
```

**Lambda Processing**:
1. Acquires DynamoDB lock for final merge
2. Downloads/loads existing snapshot from S3/EFS
3. Merges all intermediate snapshots into snapshot
4. Uploads final snapshot to S3/EFS
5. Releases lock

**Output**:
```json
{
  "final_merge_result": {
    "statusCode": 200,
    "batch_id": "uuid",
    "snapshot_key": "lines/snapshot/latest/",  // or "words/snapshot/latest/"
    "total_embeddings": 1000,
    "processing_time_seconds": 45.2,
    "message": "Final merge completed successfully"
  }
}
```

**Differences**:
- Line workflow: `database: "lines"`, snapshot path: `lines/snapshot/latest/`
- Word workflow: `database: "words"`, snapshot path: `words/snapshot/latest/`

### Step 16: MarkBatchesComplete / MarkWordBatchesComplete

**Purpose**: Mark all processed batches as `COMPLETED` in DynamoDB

**Lambda**: `embedding-mark-batches-complete`

**Input**:
```json
{
  "poll_results": [ /* array of batch results */ ] or null,
  "poll_results_s3_key": "chunk_groups/{batch_id}/poll_results.json",
  "poll_results_s3_bucket": "bucket-name"
}
```

**Lambda Processing**:
1. Loads `poll_results` from S3 if `poll_results_s3_key` is provided
2. Extracts unique `batch_id` values from `poll_results`
3. Updates each batch summary status to `COMPLETED`

**Output**:
```json
{
  "mark_complete_result": {
    "batches_marked": 50,
    "batch_ids": [ "uuid1", "uuid2", ... ]
  }
}
```

**Differences**: None - same logic

---

## Key Differences Summary

| Aspect | Line Workflow | Word Workflow |
|--------|---------------|---------------|
| **Step Names** | `ListPendingBatches`, `PollBatches`, etc. | `ListPendingWordBatches`, `PollWordBatches`, etc. |
| **Polling Lambda** | `embedding-line-poll` | `embedding-word-poll` |
| **Delta Paths** | `lines/delta/lines/receipt_lines/{id}/` | `words/delta/words/receipt_words/{id}/` |
| **Database Name** | `"lines"` | `"words"` |
| **Collection Name** | `"lines"` | `"words"` |
| **Snapshot Path** | `lines/snapshot/latest/` | `words/snapshot/latest/` |
| **Everything Else** | Identical | Identical |

---

## Data Flow Patterns

### S3 Offloading Strategy

To avoid Step Functions' 256KB payload limit:

1. **Chunks**: If `SplitIntoChunks` output > 150KB → upload to S3, return metadata
2. **Groups**: Always upload groups to S3 (aggressive approach)
3. **Poll Results**: If > 100KB → upload to S3, return `null` with S3 keys

### State Data Structure

Each state maintains this structure:
```json
{
  "pending_batches": [ /* ... */ ],
  "poll_results": [ /* ... */ ],
  "chunked_data": { /* ... */ },
  "chunk_results": [ /* ... */ ],
  "chunk_groups": { /* ... */ },
  "merged_groups": [ /* ... */ ],
  "final_merge_result": { /* ... */ },
  "mark_complete_result": { /* ... */ }
}
```

### Error Handling

- All Lambda tasks have retry policies (exponential backoff)
- Failed states are caught and routed to error handlers
- Error information is preserved in `$.error` path

---

## Notes

- **Execution Name**: Used as `batch_id` for the entire ingestion run
- **Intermediate Keys**: Format: `intermediate/{batch_id}/chunk-{index}/` or `intermediate/{batch_id}-group-{index}/merged/`
- **S3 Keys**: Always prefixed with `chunk_groups/{batch_id}/` for group-related data
- **Lock Management**: Only `FinalMerge` acquires a DynamoDB lock to prevent concurrent writes
- **Parallel Processing**: Chunks processed with max concurrency 10, groups with max concurrency 6

---

## Troubleshooting Guide

### Common Errors and Solutions

#### 1. "States.DataLimitExceeded" Error

**Symptoms**: Step Function fails with "returned a result with a size exceeding the maximum number of bytes service limit"

**Causes**:
- `SplitIntoChunks` output > 256KB
- `CreateChunkGroups` output > 256KB
- `LoadChunkGroupsFromS3` output > 256KB

**Solutions**:
- ✅ Already handled: `SplitIntoChunks` automatically uploads to S3 if > 150KB
- ✅ Already handled: `CreateChunkGroups` always uploads to S3
- ✅ Already handled: `LoadChunkGroupsFromS3` returns minimal metadata, not full groups

**Debug Steps**:
```bash
# Check execution output size
aws stepfunctions get-execution-history \
  --execution-arn "arn:..." \
  --query 'events[?type==`TaskSucceeded`]' \
  --output json | jq '.[] | {name: .taskSucceededEventDetails.resource, outputSize: (.taskSucceededEventDetails.output | length)}'
```

#### 2. "JSONPath could not be found" Error

**Symptoms**: Step Function fails with "The JSONPath '$.field' specified for the field 'field.$' could not be found"

**Causes**:
- Field is `null` instead of missing
- Field name mismatch
- Field not included in previous step output

**Solutions**:
- ✅ Fixed: `NormalizeChunkData` ensures `chunks_s3_key` and `chunks_s3_bucket` always exist
- ✅ Fixed: `merge_chunk_group_handler` handles `null` values properly
- Check Step Function definition for JSONPath typos

**Debug Steps**:
```bash
# Check state input/output
aws stepfunctions get-execution-history \
  --execution-arn "arn:..." \
  --query 'events[?contains(stateEnteredEventDetails.name || ``, `StateName`)]' \
  --output json | jq '.[] | {input: .stateEnteredEventDetails.input, output: .stateExitedEventDetails.output}'
```

#### 3. "Empty chunk group processed" Warnings

**Symptoms**: All groups return "Empty chunk group processed" messages

**Causes**:
- `groups_s3_key` and `groups_s3_bucket` not passed to merge Lambda
- S3 download failing silently
- Groups file in S3 is empty or malformed

**Solutions**:
- ✅ Fixed: S3 keys are now passed in Step Function parameters
- ✅ Fixed: S3 download errors are logged and returned
- Check S3 bucket permissions and file existence

**Debug Steps**:
```bash
# Check S3 file
aws s3 cp s3://bucket/chunk_groups/{batch_id}/groups.json /tmp/groups.json
cat /tmp/groups.json | jq 'length, .[0]'

# Check Lambda logs
aws logs filter-log-events \
  --log-group-name "/aws/lambda/embedding-vector-compact-dev" \
  --filter-pattern "{batch_id}" \
  --max-items 50
```

#### 4. "chunk_group must be a list" Error

**Symptoms**: Lambda fails with type error

**Causes**:
- Step Functions passes `null` (JSON) which becomes `None` (Python)
- Code tries to slice/iterate over `None`

**Solutions**:
- ✅ Fixed: Explicit `None` handling and type validation added
- ✅ Fixed: S3 download validates data structure

**Debug Steps**:
```bash
# Check Lambda input
aws logs filter-log-events \
  --log-group-name "/aws/lambda/embedding-vector-compact-dev" \
  --filter-pattern "merge_chunk_group" \
  --max-items 20 | grep -E "(chunk_group|ERROR|error)"
```

#### 5. Batch Status Not Updating

**Symptoms**: Batches remain `PENDING` after workflow completes

**Causes**:
- `MarkBatchesComplete` not reached (workflow failed earlier)
- `poll_results` not loaded from S3
- DynamoDB update failing

**Solutions**:
- Check workflow execution status
- Verify `poll_results_s3_key` is passed correctly
- Check DynamoDB permissions

**Debug Steps**:
```bash
# Check execution status
aws stepfunctions describe-execution \
  --execution-arn "arn:..." \
  --query '{status: status, error: error}'

# Check MarkBatchesComplete logs
aws logs filter-log-events \
  --log-group-name "/aws/lambda/embedding-mark-batches-complete-lambda-dev" \
  --filter-pattern "{execution_id}" \
  --max-items 20
```

### Debugging Checklist

- [ ] Check Step Function execution status and error messages
- [ ] Verify Lambda logs for the failing step
- [ ] Check S3 file existence and format (if using S3)
- [ ] Validate JSONPath references in Step Function definition
- [ ] Verify data types match expected formats
- [ ] Check DynamoDB batch summary status
- [ ] Verify IAM permissions for S3, DynamoDB, Lambda
- [ ] Check payload sizes (should be < 256KB for Step Functions)

---

## Performance Characteristics

### Typical Execution Times

| Step | Typical Duration | Notes |
|------|------------------|-------|
| ListPendingBatches | < 1s | Simple DynamoDB query |
| PollBatches (per batch) | 2-5s | OpenAI API call + delta upload |
| SplitIntoChunks | 1-2s | In-memory partitioning |
| ProcessChunksInParallel | 30-120s | Depends on chunk size, parallelized |
| CreateChunkGroups | 1-3s | S3 upload |
| MergeChunkGroupsInParallel | 60-300s | Depends on group size, parallelized |
| FinalMerge | 30-180s | Single-threaded with lock |
| MarkBatchesComplete | 2-5s | DynamoDB updates |

### Scalability Limits

- **Max Concurrency**:
  - Poll batches: 50 concurrent
  - Process chunks: 10 concurrent
  - Merge groups: 6 concurrent
- **Payload Limits**:
  - Step Functions: 256KB per state
  - S3 offloading: Automatic for chunks > 150KB
- **Batch Size**:
  - Recommended: 50-200 batches per execution
  - Maximum: Limited by Step Functions execution time (1 year)

### Optimization Tips

1. **Batch Size**: Larger batches = fewer Step Function executions = lower overhead
2. **Chunk Size**: 10 items per chunk balances parallelism vs overhead
3. **Group Size**: 10 chunks per group balances merge efficiency vs parallelism
4. **S3 Usage**: Always use S3 for groups to avoid payload limit issues
5. **EFS vs S3**: EFS faster for reads, S3 more reliable for storage

---

## Quick Reference Tables

### Lambda Functions

| Function Name | Purpose | Memory | Timeout | Storage |
|---------------|---------|--------|---------|---------|
| `embedding-list-pending` | List pending batches | 256MB | 60s | 512MB |
| `embedding-line-poll` | Poll line batches | 3008MB | 900s | 3072MB |
| `embedding-word-poll` | Poll word batches | 3008MB | 900s | 3072MB |
| `embedding-split-chunks` | Split into chunks | 512MB | 60s | 512MB |
| `embedding-vector-compact` | Process/merge chunks | 8192MB | 900s | 10240MB |
| `embedding-create-chunk-groups` | Create groups | 512MB | 60s | 512MB |
| `embedding-mark-batches-complete` | Mark complete | 256MB | 60s | 512MB |

### Step Function State Types

| State Type | Purpose | Examples |
|------------|---------|----------|
| **Task** | Invoke Lambda | `ListPendingBatches`, `SplitIntoChunks` |
| **Map** | Parallel processing | `PollBatches`, `ProcessChunksInParallel` |
| **Choice** | Conditional routing | `CheckChunksSource`, `CheckChunkGroupCount` |
| **Pass** | Data transformation | `NormalizeChunkData`, `GroupChunksForMerge` |
| **Fail** | Error termination | `GroupCreationFailed`, `CompactionFailed` |
| **Succeed** | Success termination | `NoChunksToProcess` |

### Data Structure Locations

| Data | Location | Format |
|------|----------|--------|
| Batch summaries | DynamoDB | `BATCH#{batch_id}` / `STATUS` |
| Delta files | S3 | `{database}/delta/{database}/receipt_{type}/{delta_id}/` |
| Intermediate snapshots | S3 | `intermediate/{batch_id}/chunk-{index}/` |
| Chunk groups | S3 | `chunk_groups/{batch_id}/groups.json` |
| Poll results | S3 (if large) | `chunk_groups/{batch_id}/poll_results.json` |
| Final snapshots | S3/EFS | `{database}/snapshot/latest/` |

### Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `DYNAMODB_TABLE_NAME` | DynamoDB table | `receipts-dev` |
| `CHROMADB_BUCKET` | S3 bucket for ChromaDB | `chromadb-dev-...` |
| `CHROMA_ROOT` | EFS mount path | `/mnt/efs/chromadb` |
| `CHROMADB_STORAGE_MODE` | Storage mode | `auto`, `s3`, `efs` |
| `GOOGLE_PLACES_API_KEY` | Places API key | `AIza...` |
| `OPENAI_API_KEY` | OpenAI API key | `sk-...` |

---

## Testing Strategies

### Unit Testing

Test individual Lambda handlers with mock events:
```python
# Example: Test merge_chunk_group_handler
event = {
    "operation": "merge_chunk_group",
    "batch_id": "test-batch",
    "group_index": 0,
    "chunk_group": [{"intermediate_key": "intermediate/test/chunk-0/"}],
    "database": "lines"
}
result = merge_chunk_group_handler(event)
assert "intermediate_key" in result
```

### Integration Testing

Test Step Function workflows with real AWS resources:
```bash
# Start execution
aws stepfunctions start-execution \
  --state-machine-arn "arn:..." \
  --input '{"test": true}'

# Monitor execution
aws stepfunctions describe-execution \
  --execution-arn "arn:..."
```

### Local Testing

Use `dev.test_receipt_metadata_creation.py` and similar scripts to test Lambda logic locally with Pulumi secrets.

---

## Best Practices

### 1. Error Handling
- Always check for `null` values from Step Functions
- Validate data types before operations
- Use structured error responses with status codes

### 2. Logging
- Log input/output sizes for debugging payload issues
- Include batch_id and execution context in all logs
- Use structured logging with consistent field names

### 3. Data Validation
- Validate S3 data structure after download
- Check for empty arrays/lists before processing
- Verify required fields exist before accessing

### 4. Performance
- Use S3 aggressively to avoid payload limits
- Monitor Lambda memory usage and adjust if needed
- Consider increasing concurrency for large batches

### 5. Monitoring
- Set up CloudWatch alarms for failed executions
- Monitor Lambda error rates and durations
- Track S3 storage costs and usage

---

---

## Real-World Examples

### Example 1: Small Batch (3 chunks, no grouping)

**Input**: 30 batches polled, 3 chunks created

**Flow**:
```
ListPendingBatches → PollBatches (30 items) → SplitIntoChunks (3 chunks)
  → ProcessChunksInParallel (3 chunks) → CheckChunkGroupCount (≤4)
  → PrepareFinalMerge → FinalMerge → MarkBatchesComplete
```

**Execution Time**: ~2-3 minutes

### Example 2: Large Batch (30 chunks, hierarchical merge)

**Input**: 300 batches polled, 30 chunks created, 3 groups

**Flow**:
```
ListPendingBatches → PollBatches (300 items) → SplitIntoChunks (30 chunks, uses S3)
  → LoadChunksFromS3 → ProcessChunksInParallel (30 chunks)
  → CreateChunkGroups (3 groups, uses S3) → LoadChunkGroupsFromS3
  → MergeChunkGroupsInParallel (3 groups) → PrepareHierarchicalFinalMerge
  → FinalMerge → MarkBatchesComplete
```

**Execution Time**: ~10-15 minutes

### Example 3: Failed Execution Debugging

**Scenario**: Execution fails at `MergeChunkGroupsInParallel`

**Debug Steps**:
1. Check execution status: `aws stepfunctions describe-execution --execution-arn "arn:..."`
2. Find failed task: Look for `TaskFailed` events
3. Check Lambda logs: Filter by batch_id
4. Verify S3 files: Check if `groups.json` exists and is valid
5. Check data format: Verify `chunk_group` is `null` (expected) and S3 keys are present

**Common Fix**: Ensure `groups_s3_key` and `groups_s3_bucket` are passed to merge Lambda

---

## Glossary

| Term | Definition |
|------|------------|
| **Batch** | A group of embeddings submitted to OpenAI Batch API |
| **Batch Summary** | DynamoDB record tracking batch status and metadata |
| **Chunk** | A group of 10 delta results processed together |
| **Chunk Group** | A group of 10 chunks merged together hierarchically |
| **Delta** | A ChromaDB snapshot containing new embeddings |
| **Intermediate Snapshot** | Temporary ChromaDB snapshot created during processing |
| **Final Snapshot** | The merged ChromaDB snapshot stored at `{database}/snapshot/latest/` |
| **Execution** | A single Step Function workflow run |
| **Execution Name** | UUID used as `batch_id` for the entire ingestion run |
| **Hierarchical Merge** | Two-stage merge: chunks → groups → final snapshot |
| **Lock** | DynamoDB-based mutex preventing concurrent final merges |
| **Payload Limit** | Step Functions 256KB limit for state input/output |
| **S3 Offloading** | Storing large data in S3 instead of Step Function state |
| **Poll Results** | Array of batch polling results with delta keys |

---

## Additional Resources

- [AWS Step Functions Best Practices](https://docs.aws.amazon.com/step-functions/latest/dg/best-practices.html)
- [Step Functions Limits](https://docs.aws.amazon.com/step-functions/latest/dg/limits.html)
- [Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Data Flow Best Practices](./DATA_FLOW_BEST_PRACTICES.md) - Detailed guide on handling null values and type safety

