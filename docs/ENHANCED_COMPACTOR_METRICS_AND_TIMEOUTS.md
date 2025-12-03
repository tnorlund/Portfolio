# Enhanced Compactor Metrics and Timeout Analysis

## Overview

This document reviews all custom metrics and timeout configurations for the Enhanced Compactor Lambda, providing recommendations for fine-tuning based on current behavior.

## Current Lambda Configuration

### Infrastructure Settings (from `lambda_functions.py`)

```python
timeout: 900 seconds (15 minutes)
memory_size: 4096 MB (4GB)
ephemeral_storage: 10240 MB (10GB)
reserved_concurrent_executions: 10
```

**Rationale (from code comments):**
- Timeout increased from 300s to 900s due to:
  - Multiple timeouts at 300s with operations still running
  - Snapshot operations (400-550MB) require more time
  - Evidence shows operations taking 300-516 seconds
- Memory increased from 2048MB to 4096MB due to:
  - Multiple failures showing "Max Memory Used: 2048 MB" (hitting limit)
  - Snapshot uploads average 446MB, largest 552MB
  - Need headroom for runtime + ChromaDB + snapshot operations
- Ephemeral storage increased to 10GB for large snapshot operations

### Code-Level Timeout Protection

```python
@with_compaction_timeout_protection(max_duration=840)  # 14 minutes
```

**Note:** The code-level timeout (840s = 14 minutes) is slightly less than the Lambda timeout (900s = 15 minutes) to allow for graceful shutdown and error handling.

## Lock Configuration

### Current Settings

```python
heartbeat_interval = 30 seconds  # HEARTBEAT_INTERVAL_SECONDS
lock_duration_minutes = 1 minute  # LOCK_DURATION_MINUTES
max_heartbeat_failures = 2  # MAX_HEARTBEAT_FAILURES
```

**Rationale:**
- Heartbeat interval (30s) must be < lock duration (60s) to ensure lock extension
- Reduced lock duration from default to minimize contention
- Fast recovery with max 2 heartbeat failures

### SQS Queue Configuration

```python
visibility_timeout_seconds: 1200  # 20 minutes
maxReceiveCount: 3  # Retry 3 times before DLQ
message_retention_seconds: 345600  # 4 days
```

**Note:** Visibility timeout (20 minutes) must be > Lambda timeout (15 minutes) to prevent message reprocessing during execution.

## Custom Metrics

### Lambda-Level Metrics

| Metric Name | Type | Unit | Description |
|------------|------|------|-------------|
| `CompactionLambdaExecutionTime` | Gauge | Seconds | Total Lambda execution time |
| `CompactionLambdaSuccess` | Count | Count | Successful Lambda invocations |
| `CompactionLambdaError` | Count | Count | Failed Lambda invocations |
| `CompactionDirectInvocationAttempt` | Count | Count | Direct invocation attempts (not supported) |
| `CompactionRecordsReceived` | Count | Count | Number of SQS records received |
| `CompactionPartialBatchFailure` | Count | Count | Partial batch failures (some messages failed) |
| `CompactionFailedMessages` | Count | Count | Number of failed messages in a batch |

### Lock Metrics

| Metric Name | Type | Unit | Dimensions | Description |
|------------|------|------|------------|-------------|
| `CompactionLockCollision` | Count | Count | `phase`, `collection`, `type`, `attempt` | Lock acquisition failures |
| `CompactionLockDuration` | Gauge | Milliseconds | `phase`, `collection`, `type` | Time spent holding lock |
| `CompactionLockWaitTime` | Gauge | Milliseconds | `phase`, `collection`, `type` | Time spent waiting for lock |

**Lock Phases:**
- `phase: "1"` - Initial validation (CAS check)
- `phase: "3"` - Snapshot upload (EFS or S3)

**Lock Types:**
- `type: "validation"` - Phase 1 validation
- `type: "upload"` - Phase 3 upload (EFS)
- `type: "upload_s3"` - Phase 3 upload (S3-only)
- `type: "backoff"` - Backoff delay

### Delta Processing Metrics

| Metric Name | Type | Unit | Description |
|------------|------|------|-------------|
| `CompactionDeltaMessagesSkipped` | Count | Count | Delta messages skipped (not implemented) |

### Compaction Operation Metrics

From `compaction/operations.py`:

| Metric Name | Type | Unit | Description |
|------------|------|------|-------------|
| `CompactionMetadataUpdated` | Count | Count | Metadata updates applied |
| `CompactionMetadataUpdateError` | Count | Count | Metadata update failures |
| `CompactionLabelUpdated` | Count | Count | Label updates applied |
| `CompactionLabelUpdateError` | Count | Count | Label update failures |
| `CompactionReceiptDeleted` | Count | Count | Receipt deletions processed |
| `CompactionReceiptDeletionError` | Count | Count | Receipt deletion failures |
| `CompactionWordEmbeddingNotFound` | Count | Count | Word embeddings not found during update |
| `CompactionWordEmbeddingNotFoundForRemoval` | Count | Count | Word embeddings not found during deletion |
| `CompactionWordLabelUpdated` | Count | Count | Word labels updated |
| `CompactionWordLabelRemoved` | Count | Count | Word labels removed |
| `CompactionInvalidChromaDBID` | Count | Count | Invalid ChromaDB IDs encountered |

### Compaction Run Metrics

From `compaction/compaction_run.py`:

| Metric Name | Type | Unit | Description |
|------------|------|------|-------------|
| `CompactionRunProcessed` | Count | Count | CompactionRun messages processed |
| `CompactionRunProcessingError` | Count | Count | CompactionRun processing failures |
| `CompactionRunCompleted` | Count | Count | CompactionRun completions detected |
| `CompactionRunCompletionDetected` | Count | Count | CompactionRun completion messages |

### EFS Snapshot Manager Metrics

From `compaction/efs_snapshot_manager.py`:

| Metric Name | Type | Unit | Description |
|------------|------|------|-------------|
| `EFSSnapshotDownloaded` | Count | Count | Snapshots downloaded from S3 to EFS |
| `EFSSnapshotUploaded` | Count | Count | Snapshots uploaded from EFS to S3 |
| `EFSSnapshotCopyTime` | Gauge | Milliseconds | Time to copy snapshot from EFS to local |
| `EFSSnapshotDownloadTime` | Gauge | Milliseconds | Time to download snapshot from S3 to EFS |
| `EFSSnapshotUploadTime` | Gauge | Milliseconds | Time to upload snapshot from EFS to S3 |

### Message Builder Metrics

From `compaction/message_builder.py`:

| Metric Name | Type | Unit | Description |
|------------|------|------|-------------|
| `CompactionMessageMissingCollection` | Count | Count | Messages missing collection field |
| `CompactionInvalidCollection` | Count | Count | Invalid collection values |
| `CompactionStreamMessage` | Count | Count | Stream messages processed |
| `CompactionDeltaMessage` | Count | Count | Delta messages processed |
| `CompactionRunMessageBuildError` | Count | Count | CompactionRun message build failures |
| `CompactionEntityMessageBuildError` | Count | Count | Entity message build failures |
| `CompactionPartialBatchFailure` | Count | Count | Partial batch failures |
| `CompactionBatchProcessingSuccess` | Count | Count | Successful batch processing |

## Timeout Analysis

### Current Timeout Hierarchy

1. **Lambda Timeout**: 900 seconds (15 minutes)
   - Hard limit enforced by AWS Lambda
   - Exceeding this causes immediate termination

2. **Code-Level Timeout**: 840 seconds (14 minutes)
   - Enforced by `@with_compaction_timeout_protection`
   - Allows 60 seconds for graceful shutdown

3. **SQS Visibility Timeout**: 1200 seconds (20 minutes)
   - Must be > Lambda timeout to prevent reprocessing
   - Current: 20 minutes > 15 minutes ✅

4. **Lock Duration**: 60 seconds (1 minute)
   - Maximum time a lock can be held
   - Extended via heartbeat (30-second intervals)

### Timeout Recommendations

#### 1. Lambda Timeout: Keep at 900s (15 minutes)

**Current Status:** ✅ Appropriate

**Reasoning:**
- Largest observed operation: 516 seconds (~8.6 minutes)
- Snapshot operations: 300-516 seconds
- 15 minutes provides ~2x headroom for worst case
- Code-level timeout (14 minutes) provides safety margin

**Monitoring:**
- Track `CompactionLambdaExecutionTime` to identify operations approaching timeout
- Alert if execution time > 12 minutes (80% of timeout)

#### 2. Code-Level Timeout: Keep at 840s (14 minutes)

**Current Status:** ✅ Appropriate

**Reasoning:**
- Provides 60-second buffer before Lambda timeout
- Allows graceful shutdown and error handling
- Prevents hard Lambda timeouts

**Monitoring:**
- Track timeout protection triggers
- Alert if timeout protection is frequently triggered

#### 3. SQS Visibility Timeout: Consider Reducing to 960s (16 minutes)

**Current Status:** ⚠️ Could be optimized

**Current:** 1200 seconds (20 minutes)
**Recommended:** 960 seconds (16 minutes)

**Reasoning:**
- Current: 20 minutes > 15 minutes (Lambda timeout) ✅
- Recommended: 16 minutes = Lambda timeout (15 min) + 1 minute buffer
- Reduces retry delay from 20 minutes to 16 minutes
- Still provides safety margin

**Trade-off:**
- Faster retries (16 min vs 20 min)
- Slightly less buffer if Lambda execution exceeds 15 minutes

**Action:** Monitor Lambda execution times. If consistently < 14 minutes, reduce visibility timeout to 960s.

#### 4. Lock Duration: Keep at 60s (1 minute)

**Current Status:** ✅ Appropriate

**Reasoning:**
- Minimizes lock contention
- Heartbeat (30s) extends lock before expiration
- Fast recovery (max 2 heartbeat failures = 60s)

**Monitoring:**
- Track `CompactionLockDuration` to ensure locks are released quickly
- Alert if lock duration > 45 seconds (75% of limit)

#### 5. Heartbeat Interval: Keep at 30s

**Current Status:** ✅ Appropriate

**Reasoning:**
- Must be < lock duration (60s)
- 30s provides 2x safety margin
- Frequent enough to maintain lock ownership

## Metrics Monitoring Recommendations

### Critical Metrics (Alert on Threshold)

1. **`CompactionLambdaExecutionTime`**
   - Alert if > 12 minutes (80% of timeout)
   - Indicates operations approaching timeout

2. **`CompactionLockCollision`**
   - Alert if > 5 per hour
   - Indicates high contention

3. **`CompactionLambdaError`**
   - Alert if > 0
   - Indicates failures requiring investigation

4. **`CompactionPartialBatchFailure`**
   - Alert if > 0
   - Indicates some messages failed (will retry)

### Performance Metrics (Track Trends)

1. **`CompactionLockDuration`**
   - Track by phase and collection
   - Identify slow operations

2. **`CompactionLockWaitTime`**
   - Track backoff delays
   - Identify contention patterns

3. **`EFSSnapshotCopyTime`**, **`EFSSnapshotDownloadTime`**, **`EFSSnapshotUploadTime`**
   - Track snapshot operation performance
   - Identify slow S3/EFS operations

### Capacity Metrics (Track Growth)

1. **`CompactionRecordsReceived`**
   - Track message volume
   - Identify traffic spikes

2. **`CompactionRunProcessed`**
   - Track compaction run volume
   - Identify processing patterns

## Fine-Tuning Recommendations

### 1. Reduce SQS Visibility Timeout

**Current:** 1200 seconds (20 minutes)
**Recommended:** 960 seconds (16 minutes)

**Benefits:**
- Faster retry on lock collisions (16 min vs 20 min)
- Still provides safety margin (16 min > 15 min Lambda timeout)

**Action:**
1. Monitor `CompactionLambdaExecutionTime` for 1 week
2. If max execution time < 14 minutes, reduce visibility timeout to 960s
3. Update `sqs_queues.py`:

```python
visibility_timeout_seconds=960,  # 16 minutes - Lambda timeout (15 min) + 1 min buffer
```

### 2. Add Metric for Timeout Protection Triggers

**Current:** No metric for timeout protection triggers

**Recommended:** Add metric when timeout protection is triggered

**Action:**
Add to `timeout_handler.py`:

```python
metrics.count("CompactionTimeoutProtectionTriggered", 1)
```

### 3. Add Metric for Lock Acquisition Attempts

**Current:** Only tracks collisions, not total attempts

**Recommended:** Track total lock acquisition attempts

**Action:**
Add to `enhanced_compaction_handler.py`:

```python
metrics.count("CompactionLockAcquisitionAttempt", 1, {"phase": phase, "collection": collection})
```

### 4. Add Metric for Snapshot Operation Durations

**Current:** EFS operations tracked, but not S3-only operations

**Recommended:** Track S3 snapshot download/upload times

**Action:**
Add timing metrics for S3-only snapshot operations in `enhanced_compaction_handler.py`.

### 5. Optimize Lock Backoff Strategy

**Current:** Fixed backoff delays (0.1s, 0.2s for EFS; 0.15s, 0.3s for S3)

**Recommended:** Exponential backoff with jitter

**Action:**
Consider implementing exponential backoff:
- Attempt 1: 0.1s
- Attempt 2: 0.2s
- Attempt 3: 0.4s (if needed)

## CloudWatch Dashboard Recommendations

### Create Dashboard with:

1. **Lambda Performance**
   - `CompactionLambdaExecutionTime` (line chart)
   - `CompactionLambdaSuccess` vs `CompactionLambdaError` (stacked bar)
   - Lambda memory utilization (from AWS/Lambda metrics)

2. **Lock Performance**
   - `CompactionLockCollision` (bar chart, by phase)
   - `CompactionLockDuration` (line chart, by phase)
   - `CompactionLockWaitTime` (line chart)

3. **Processing Volume**
   - `CompactionRecordsReceived` (line chart)
   - `CompactionRunProcessed` (line chart)
   - `CompactionMetadataUpdated`, `CompactionLabelUpdated`, `CompactionReceiptDeleted` (stacked area)

4. **Error Tracking**
   - `CompactionLambdaError` (bar chart)
   - `CompactionPartialBatchFailure` (bar chart)
   - Error rate (errors / total invocations)

## Summary

### Current Configuration: ✅ Mostly Optimal

- **Lambda Timeout (900s)**: Appropriate for current workload
- **Code-Level Timeout (840s)**: Good safety margin
- **SQS Visibility Timeout (1200s)**: Could be reduced to 960s for faster retries
- **Lock Duration (60s)**: Minimizes contention
- **Heartbeat Interval (30s)**: Appropriate for lock maintenance

### Recommended Actions

1. **Immediate:**
   - Monitor `CompactionLambdaExecutionTime` for 1 week
   - Create CloudWatch dashboard for key metrics

2. **Short-term (1-2 weeks):**
   - If max execution time < 14 minutes, reduce SQS visibility timeout to 960s
   - Add timeout protection trigger metric
   - Add lock acquisition attempt metric

3. **Long-term (1 month):**
   - Analyze lock collision patterns
   - Optimize backoff strategy if needed
   - Review and optimize snapshot operation performance

