# Ingestion Step Function Cost Analysis

## Overview

This document analyzes the cost of running the ingestion step function, including AWS services used, state transitions, and scalability considerations.

## AWS Services Used

1. **AWS Step Functions** - Orchestration
2. **AWS Lambda** - Compute (6 different functions)
3. **Amazon S3** - Storage (deltas, snapshots, intermediate results)
4. **Amazon DynamoDB** - Metadata storage (batch summaries, locks)
5. **CloudWatch Logs** - Logging (included in Lambda cost)

## Step Function State Transitions

### Base States (Always Executed)

| State | Type | Count |
|-------|------|-------|
| ListPendingBatches | Task | 1 |
| CheckPendingBatches | Choice | 1 |
| NormalizePendingBatches | Pass | 1 |
| PollBatches | Map | 1 (parent) |
| NormalizePollBatchesData | Task | 1 |
| SplitIntoChunks | Task | 1 |
| CheckChunksSource | Choice | 1 |
| CheckForChunks | Choice | 1 |
| NormalizeChunkData | Pass | 1 |
| ProcessChunksInParallel | Map | 1 (parent) |
| GroupChunksForMerge | Pass | 1 |
| CheckChunkGroupCount | Choice | 1 |
| PrepareFinalMerge / PrepareHierarchicalFinalMerge | Pass | 1 |
| FinalMerge | Task | 1 |
| MarkBatchesComplete | Task | 1 |

**Base State Transitions: ~15-20** (depending on path)

### Variable States (Based on Batch Count)

| State | Type | Count Formula |
|-------|------|---------------|
| PollBatch (inside Map) | Task | `N` batches (max 50 concurrent) |
| ProcessSingleChunk (inside Map) | Task | `ceil(completed_batches / 10)` chunks (max 5 concurrent) |
| CreateChunkGroups | Task | 1 (if chunks > 4) |
| CheckChunkGroupsSource | Choice | 1 (if chunks > 4) |
| LoadChunkGroupsFromS3 | Task | 0 or 1 (if chunks > 4 and use_s3) |
| MergeChunkGroupsInParallel | Map | 1 (if chunks > 4) |
| MergeSingleChunkGroup (inside Map) | Task | `ceil(total_chunks / 10)` groups (max 6 concurrent) |

**Total State Transitions Formula:**
```
Base: ~20
+ N (PollBatch iterations)
+ ceil(completed_batches / 10) (ProcessSingleChunk iterations)
+ (if chunks > 4): 1 + ceil(total_chunks / 10) (group merge iterations)
= ~20 + N + ceil(completed_batches / 10) + (conditional group merge)
```

### Example: Processing 100 Completed Batches

- **Base states**: 20
- **PollBatch iterations**: 100 (Map state)
- **Chunks created**: 10 (100 batches / 10 per chunk)
- **ProcessSingleChunk iterations**: 10 (Map state)
- **Group merge**: 1 group (10 chunks / 10 per group)
- **Total state transitions**: ~131

## Cost Breakdown (2024 AWS Pricing - us-east-1)

### 1. Step Functions State Transitions

**Pricing**: $0.000025 per state transition

**Example (100 batches)**:
- 131 state transitions × $0.000025 = **$0.0033**

**Scalability**: Linear with batch count
- 10 batches: ~$0.0005
- 100 batches: ~$0.0033
- 1,000 batches: ~$0.033

### 2. Lambda Invocations

#### A. ListPendingBatches (`embedding-list-pending`)
- **Memory**: 512 MB
- **Timeout**: 15 minutes
- **Invocations**: 1 per execution
- **Estimated duration**: 2-5 seconds
- **Cost**: (512 MB × 0.002 GB-seconds) × $0.0000166667 = **$0.000000017**

#### B. PollBatch (`embedding-line-poll` or `embedding-word-poll`)
- **Memory**: 1.5 GB (line) or 1 GB (word)
- **Timeout**: 15 minutes
- **Invocations**: N (number of batches)
- **Estimated duration**: 5-30 seconds per batch (depends on OpenAI API response time)
- **Cost per batch**:
  - Line: (1.5 GB × 0.01 GB-seconds) × $0.0000166667 = **$0.00000025**
  - Word: (1 GB × 0.01 GB-seconds) × $0.0000166667 = **$0.00000017**

**Example (100 batches, line)**:
- 100 × $0.00000025 = **$0.000025**

#### C. NormalizePollBatchesData (`embedding-normalize-poll-batches`)
- **Memory**: 512 MB
- **Timeout**: 5 minutes
- **Invocations**: 1 per execution
- **Estimated duration**: 1-3 seconds
- **Cost**: **$0.00000001**

#### D. SplitIntoChunks (`embedding-split-chunks`)
- **Memory**: 1 GB
- **Timeout**: 15 minutes
- **Invocations**: 1 per execution
- **Estimated duration**: 1-2 seconds
- **Cost**: **$0.00000002**

#### E. ProcessSingleChunk (`embedding-vector-compact`)
- **Memory**: 2 GB
- **Timeout**: 15 minutes
- **Invocations**: `ceil(completed_batches / 10)` chunks
- **Estimated duration**: 30-120 seconds per chunk (depends on delta size)
- **Cost per chunk**: (2 GB × 0.05 GB-seconds) × $0.0000166667 = **$0.00000167**

**Example (10 chunks)**:
- 10 × $0.00000167 = **$0.0000167**

#### F. CreateChunkGroups (`embedding-create-chunk-groups`)
- **Memory**: 1 GB
- **Timeout**: 15 minutes
- **Invocations**: 0 or 1 (if chunks > 4)
- **Estimated duration**: 1-2 seconds
- **Cost**: **$0.00000002**

#### G. MergeSingleChunkGroup (`embedding-vector-compact`)
- **Memory**: 2 GB
- **Timeout**: 15 minutes
- **Invocations**: `ceil(total_chunks / 10)` groups (if chunks > 4)
- **Estimated duration**: 60-180 seconds per group
- **Cost per group**: (2 GB × 0.1 GB-seconds) × $0.0000166667 = **$0.00000333**

**Example (1 group)**:
- 1 × $0.00000333 = **$0.00000333**

#### H. FinalMerge (`embedding-vector-compact`)
- **Memory**: 2 GB
- **Timeout**: 15 minutes
- **Invocations**: 1 per execution
- **Estimated duration**: 120-300 seconds (depends on total data size)
- **Cost**: (2 GB × 0.15 GB-seconds) × $0.0000166667 = **$0.000005**

#### I. MarkBatchesComplete (`embedding-mark-batches-complete`)
- **Memory**: 512 MB
- **Timeout**: 15 minutes
- **Invocations**: 1 per execution
- **Estimated duration**: 2-5 seconds
- **Cost**: **$0.00000001**

**Total Lambda Cost (100 batches, line)**:
- ListPendingBatches: $0.000000017
- PollBatch (100×): $0.000025
- NormalizePollBatchesData: $0.00000001
- SplitIntoChunks: $0.00000002
- ProcessSingleChunk (10×): $0.0000167
- CreateChunkGroups: $0.00000002
- MergeSingleChunkGroup (1×): $0.00000333
- FinalMerge: $0.000005
- MarkBatchesComplete: $0.00000001
- **Total: ~$0.00005**

### 3. S3 Operations

**Pricing**:
- PUT requests: $0.005 per 1,000 requests
- GET requests: $0.0004 per 1,000 requests
- Storage: $0.023 per GB/month (not per execution)

**Operations per execution (100 batches)**:
- **PUT requests**:
  - Poll results (100 batches): 100
  - Combined poll results: 1
  - Chunks (if use_s3): 10
  - Intermediate merges: 1
  - Final snapshot: 1
  - **Total: ~113 PUT requests** = **$0.000565**

- **GET requests**:
  - Download poll results (if use_s3): 1
  - Download chunks (if use_s3): 10
  - Download intermediate merges: 1
  - Download existing snapshot: 1
  - **Total: ~13 GET requests** = **$0.0000052**

**Total S3 Cost**: **~$0.00057**

### 4. DynamoDB Operations

**Pricing**:
- Read units: $0.25 per million
- Write units: $1.25 per million

**Operations per execution (100 batches)**:
- **Reads**:
  - ListPendingBatches: ~100 (query batch summaries)
  - PollBatch (100×): ~100 (get batch summaries)
  - MarkBatchesComplete: ~100 (get batch summaries)
  - Lock management: ~5 (check/acquire locks)
  - **Total: ~305 reads** = **$0.00007625**

- **Writes**:
  - Update batch status (100×): ~100 (during polling)
  - Mark batches complete (100×): ~100
  - Lock management: ~5
  - **Total: ~205 writes** = **$0.00025625**

**Total DynamoDB Cost**: **~$0.00033**

## Total Cost Per Execution

### Example: 100 Completed Batches (Line Embeddings)

| Service | Cost |
|---------|------|
| Step Functions | $0.0033 |
| Lambda | $0.00005 |
| S3 | $0.00057 |
| DynamoDB | $0.00033 |
| **Total** | **~$0.00425** |

**Cost per batch**: $0.00425 / 100 = **$0.0000425 per batch**

### Cost Scaling

| Batches | Step Functions | Lambda | S3 | DynamoDB | **Total** |
|---------|----------------|--------|-----|----------|-----------|
| 10 | $0.0005 | $0.000005 | $0.00006 | $0.00003 | **$0.0006** |
| 100 | $0.0033 | $0.00005 | $0.00057 | $0.00033 | **$0.0043** |
| 500 | $0.0165 | $0.00025 | $0.00285 | $0.00165 | **$0.021** |
| 1,000 | $0.033 | $0.0005 | $0.0057 | $0.0033 | **$0.042** |
| 5,000 | $0.165 | $0.0025 | $0.0285 | $0.0165 | **$0.21** |

## Scalability Analysis

### ✅ Strengths

1. **Parallel Processing**:
   - PollBatches: Max 50 concurrent (handles 50 batches simultaneously)
   - ProcessChunks: Max 5 concurrent (handles 5 chunks simultaneously)
   - MergeChunkGroups: Max 6 concurrent (handles 6 groups simultaneously)

2. **S3 Offloading**:
   - Large payloads automatically offloaded to S3
   - Prevents Step Functions 256KB limit issues
   - Reduces state transition costs

3. **Hierarchical Merging**:
   - For large batch counts, uses hierarchical merge (groups → final)
   - Reduces final merge time and cost

4. **Cost Efficiency**:
   - Very low cost per batch (~$0.00004)
   - Most cost is Step Functions state transitions (fixed overhead)
   - Lambda costs are minimal due to short durations

### ⚠️ Limitations

1. **Step Functions State Transition Limits**:
   - **25,000 state transitions per execution** (hard limit)
   - At ~131 transitions per 100 batches, can handle ~19,000 batches per execution
   - **Solution**: Run multiple executions if needed

2. **Lambda Concurrency**:
   - PollBatches: Max 50 concurrent (AWS account limit may apply)
   - ProcessChunks: Max 5 concurrent (bottleneck for large batch counts)
   - **Solution**: Increase MaxConcurrency if needed (but watch costs)

3. **Execution Time**:
   - Step Functions: **1 year maximum execution time**
   - With 100 batches: ~10-30 minutes
   - With 1,000 batches: ~2-5 hours
   - With 10,000 batches: ~20-50 hours
   - **Solution**: Run multiple executions in parallel if needed

4. **S3 Storage Costs**:
   - Deltas accumulate over time
   - Snapshots grow with data
   - **Solution**: Implement lifecycle policies to delete old deltas

### 📊 Scalability Recommendations

1. **For < 1,000 batches**: Single execution is fine
2. **For 1,000-5,000 batches**: Single execution works, but consider splitting
3. **For > 5,000 batches**: Split into multiple executions
   - Run multiple executions in parallel
   - Each handles a subset of batches
   - Use `ListPendingBatches` with filtering if needed

4. **Optimization Opportunities**:
   - Increase `ProcessChunks` MaxConcurrency from 5 to 10 (if Lambda concurrency allows)
   - Increase `MergeChunkGroups` MaxConcurrency from 6 to 10
   - Use S3 lifecycle policies to delete old deltas (> 30 days)

## Cost Comparison: Daily Operations

### Scenario: Processing 1,000 batches per day

**Option 1: Single execution per day**
- Cost: $0.042 per execution
- **Monthly cost**: $0.042 × 30 = **$1.26**

**Option 2: 10 executions per day (100 batches each)**
- Cost: $0.0043 × 10 = $0.043 per day
- **Monthly cost**: $0.043 × 30 = **$1.29**

**Option 3: Continuous (run every hour, process whatever is ready)**
- Average: 42 batches per execution
- Cost: ~$0.0018 per execution
- Executions per day: 24
- **Monthly cost**: $0.0018 × 24 × 30 = **$1.30**

**Conclusion**: Cost is similar regardless of execution frequency. Choose based on latency requirements.

## Summary

- **Cost per batch**: ~$0.00004 (extremely low)
- **Primary cost driver**: Step Functions state transitions (77% of total)
- **Scalability**: Can handle up to ~19,000 batches per execution
- **Recommendation**: Run continuously (hourly) to process batches as they complete
- **Monthly cost estimate**: ~$1-2 for processing 1,000 batches/day

The ingestion step function is **highly cost-effective and scalable** for typical workloads.

