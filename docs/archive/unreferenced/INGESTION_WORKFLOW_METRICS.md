# Ingestion Workflow Custom Metrics Reference

This document explains all CloudWatch custom metrics (using EMF format) for the line and word embedding ingestion workflows.

## Namespace

All metrics are published to the **`EmbeddingWorkflow`** namespace.

---

## Step 1: PollBatches (Polling Handlers)

**Handlers**: `line_polling.py`, `word_polling.py`
**Step Function State**: `PollBatches` (Map state, MaxConcurrency: 100)

### Metrics Collected

#### 1. **Invocation & Status Metrics**

| Metric Name | Type | Description | When Logged |
|------------|------|-------------|-------------|
| `LinePollingInvocations` / `WordPollingInvocations` | Count | Number of Lambda invocations | Every invocation |
| `BatchStatusChecked` | Count | Number of times OpenAI batch status was checked | After checking batch status |
| `LinePollingSuccess` / `WordPollingSuccess` | Count | Successful completion of polling | On successful completion |
| `LinePollingPartialResults` / `WordPollingPartialResults` | Count | Batches with partial results (expired batches) | When processing expired batches |
| `LinePollingFailures` / `WordPollingFailures` | Count | Completely failed batches | When batch fails completely |
| `LinePollingWait` / `WordPollingWait` | Count | Batches still processing (wait action) | When batch is still in progress |
| `LinePollingHandleCancellation` / `WordPollingHandleCancellation` | Count | Cancelled batches | When batch is cancelled |

#### 2. **Data Processing Metrics**

| Metric Name | Type | Description | When Logged |
|------------|------|-------------|-------------|
| `DownloadedResults` | Count | Number of embedding results downloaded from OpenAI | After downloading batch results |
| `ProcessedDescriptions` | Count | Number of receipt descriptions processed | After retrieving descriptions |
| `SavedEmbeddings` | Count | Number of embeddings saved to delta | After successful delta save |
| `DeltasSaved` | Count | Number of deltas created (always 1 per batch) | After successful delta save |

#### 3. **Validation Metrics** ⭐ (Newly Added)

| Metric Name | Type | Description | When Logged |
|------------|------|-------------|-------------|
| `DeltaValidationAttempts` | Count | Number of validation attempts (1 for success, 3 for failure after retries) | After delta save attempt |
| `DeltaValidationRetries` | Count | Number of retries needed (only logged if > 0) | When validation requires retries |
| `DeltaValidationSuccess` | Count | 1 if validation succeeded, 0 if failed | After validation attempt |
| `DeltaSaveDuration` | Seconds | Total time for delta save operation (includes upload + validation) | After delta save completes |

#### 4. **Error & Timeout Metrics**

| Metric Name | Type | Description | When Logged |
|------------|------|-------------|-------------|
| `LinePollingTimeouts` / `WordPollingTimeouts` | Count | Lambda timeout errors | When timeout detected |
| `LinePollingErrors` / `WordPollingErrors` | Count | Unexpected errors | On exception |
| `LinePollingCircuitBreakerBlocked` / `WordPollingCircuitBreakerBlocked` | Count | Operations blocked by circuit breaker | When circuit breaker is open |

### Dimensions Used

- `collection`: "lines" or "words"
- `batch_status`: OpenAI batch status (e.g., "completed", "expired", "failed")
- `timeout_stage`: Where timeout occurred ("pre_processing", "pre_results", "pre_save", "handler")
- `error_type`: Type of error (exception class name)

### Properties (for debugging)

- `batch_id`: Batch identifier
- `openai_batch_id`: OpenAI batch ID
- `error_types`: Dictionary of error types and counts

---

## Step 2: ProcessChunksInParallel (Compaction Handler)

**Handler**: `compaction.py` → `process_chunk_handler()` → `download_and_merge_delta()`
**Step Function State**: `ProcessChunksInParallel` (Map state, MaxConcurrency: 20)

### Metrics Collected

#### 1. **Validation Metrics** ⭐ (Newly Added)

| Metric Name | Type | Description | When Logged |
|------------|------|-------------|-------------|
| `DeltaValidationSuccess` | Count | 1 if validation succeeded, 0 if failed | After each delta validation |
| `DeltaValidationAttempts` | Count | Always 1 (per delta) | After each delta validation |
| `DeltaValidationFailures` | Count | 1 if validation failed | When validation fails |
| `DeltaValidationDuration` | Seconds | Time taken for validation (download + ChromaDB open + collection check) | After validation completes |
| `ChunkDeltaValidationFailures` | Count | Number of failed deltas in a chunk | When a delta fails in chunk processing |

### Dimensions Used

- `validation_stage`: "process_chunks"
- `collection`: Collection name (e.g., "receipt_lines", "receipt_words")
- `error_type`: Type of validation error (e.g., "no_sqlite_files", "chromadb_open_failed_*", "no_collections")

### Properties (for debugging)

- `delta_key`: S3 key of the delta
- `embeddings_processed`: Number of embeddings processed (for successes)
- `error`: Error message (for failures)
- `batch_id`: Batch identifier
- `chunk_index`: Chunk index

---

## Metric Aggregation Strategy

### How Metrics Are Logged

1. **Polling Handlers**: Metrics are collected during processing and logged **once at the end** of successful completion (or on error). This batches all metrics into a single EMF log entry, reducing CloudWatch costs.

2. **Compaction Handler**: Validation metrics are logged **immediately** after each delta validation (success or failure), providing real-time visibility.

### EMF Format Benefits

- **No API calls**: Metrics are embedded in CloudWatch Logs, automatically parsed by CloudWatch
- **Cost-effective**: No per-metric charges (only log ingestion costs)
- **Automatic aggregation**: CloudWatch automatically aggregates metrics by dimensions

---

## Example CloudWatch Queries

### Success Rate Analysis

```sql
-- Overall validation success rate
SELECT
    SUM(DeltaValidationSuccess) / SUM(DeltaValidationAttempts) * 100 AS success_rate
FROM EmbeddingWorkflow
WHERE DeltaValidationAttempts > 0
```

### Performance Analysis

```sql
-- Average validation duration by stage
SELECT
    validation_stage,
    AVG(DeltaValidationDuration) AS avg_duration,
    PERCENTILE(DeltaValidationDuration, 95) AS p95_duration
FROM EmbeddingWorkflow
WHERE DeltaValidationDuration > 0
GROUP BY validation_stage
```

### Error Analysis

```sql
-- Validation failures by error type
SELECT
    error_type,
    SUM(DeltaValidationFailures) AS failure_count
FROM EmbeddingWorkflow
WHERE DeltaValidationFailures > 0
GROUP BY error_type
```

### Throughput Metrics

```sql
-- Embeddings processed per hour
SELECT
    collection,
    SUM(SavedEmbeddings) AS total_embeddings,
    COUNT(DeltasSaved) AS total_deltas
FROM EmbeddingWorkflow
WHERE SavedEmbeddings > 0
GROUP BY collection
```

### Cost Impact Analysis

```sql
-- Total time spent on validation
SELECT
    SUM(DeltaValidationDuration) AS total_validation_seconds,
    SUM(DeltaSaveDuration) AS total_save_seconds
FROM EmbeddingWorkflow
```

---

## Key Insights

### Validation Performance

- **DeltaValidationDuration**: Tracks how long validation takes (typically 1-5 seconds per delta)
- **DeltaSaveDuration**: Includes both upload and validation time (typically 5-15 seconds)
- Compare these to identify if validation is a bottleneck

### Reliability Metrics

- **DeltaValidationSuccess**: Should be close to 100% (validation failures indicate corruption)
- **DeltaValidationRetries**: Should be low (high retries indicate transient issues)
- **ChunkDeltaValidationFailures**: Tracks downstream validation failures (deltas that passed initial validation but failed during processing)

### Workflow Health

- **LinePollingSuccess** / **WordPollingSuccess**: Overall success rate
- **LinePollingTimeouts**: Lambda timeout frequency (indicates need for optimization or timeout increase)
- **LinePollingErrors**: Unexpected error rate

---

## Metric Naming Convention

- **Line metrics**: Prefixed with `LinePolling*`
- **Word metrics**: Prefixed with `WordPolling*`
- **Shared metrics**: No prefix (e.g., `DeltaValidationSuccess`, `BatchStatusChecked`)
- **Duration metrics**: Suffixed with `Duration` (in seconds)
- **Count metrics**: No suffix (count of occurrences)

---

## Notes

1. All metrics use **EMF (Embedded Metric Format)** - they're logged to stdout and automatically parsed by CloudWatch
2. Metrics are **aggregated** in the polling handlers to reduce log volume
3. **Duration metrics** are in seconds (can be converted to milliseconds in CloudWatch if needed)
4. **Properties** are not indexed as metrics but are available in log search for debugging
5. Metrics are **disabled** if `ENABLE_METRICS` environment variable is set to `"false"`


