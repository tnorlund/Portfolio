# Stream Processor Metrics Cost Analysis

## Problem Summary

The `chromadb-dev-stream-processor-4b3f763` Lambda function incurred unexpectedly high CloudWatch custom metrics costs due to excessive metric API calls.

## Root Cause Analysis

### Current Implementation

The stream processor Lambda makes **individual CloudWatch API calls** for each metric:

1. **Per-record metrics**: Each DynamoDB stream record triggers `metrics.count("StreamRecordProcessed", 1)` - **one API call per record**
2. **Per-invocation metrics**: Multiple `metrics.gauge()` and `metrics.count()` calls per Lambda invocation
3. **High invocation rate**: With the current configuration:
   - `batch_size=10` - processes up to 10 records per invocation
   - `parallelization_factor=5` - processes up to 5 shards in parallel
   - `maximum_batching_window_in_seconds=5` - batches records for up to 5 seconds

### Cost Impact

**CloudWatch Custom Metrics Pricing:**
- First 10,000 metrics: **FREE**
- Additional metrics: **$0.30 per metric per month**
- Each `put_metric_data()` API call = 1 metric data point

**Example Cost Calculation:**
- If Lambda processed 10,000 records in one night:
  - 10,000 records × ~10-15 metric calls per record = **100,000-150,000 API calls**
  - Each API call = 1 metric data point
  - Cost: (150,000 - 10,000 free) × $0.30 = **$42,000/month** (if sustained)
  - Actual cost depends on how many unique metric names/dimensions were created

### Why It Happened Last Night

Possible triggers:
1. **Bulk DynamoDB operations**: Large batch updates, migrations, or data corrections
2. **High stream activity**: Many receipt metadata or word label changes
3. **Stream catch-up**: If the stream fell behind and caught up suddenly
4. **Retry storms**: If there were processing errors causing retries

## Current Metrics Usage

From `stream_processor.py`, metrics are called:
- `metrics.count("StreamProcessorTestEvents", 1)` - test events
- `metrics.gauge("StreamRecordsReceived", len(event["Records"]))` - batch size
- `metrics.count("StreamBatchTruncated", 1)` - batch truncation
- `metrics.count("StreamRecordProcessed", 1)` - **PER RECORD** ⚠️
- `metrics.count("StreamRecordSkipped", 1)` - skipped records
- `metrics.count("StreamRecordProcessingError", 1)` - errors
- `metrics.count("StreamProcessingTimeoutExit", 1)` - timeouts
- `metrics.count("StreamProcessingCircuitBreaker", 1)` - circuit breaker
- `metrics.count("MessagesQueuedForCompaction", sent_count)` - messages sent
- `metrics.gauge("StreamProcessingDuration", processing_duration)` - duration
- `metrics.gauge("StreamBatchSize", len(event["Records"]))` - batch size
- `metrics.gauge("StreamProcessorProcessedRecords", processed_records)` - processed count
- `metrics.gauge("StreamProcessorQueuedMessages", sent_count)` - queued count
- `metrics.count("StreamProcessorError", 1)` - general errors

**Total: ~10-15 metric API calls per invocation, potentially 10+ per record**

## Solution: Use AWS Embedded Metrics Format (EMF)

### Benefits of EMF

1. **No API call costs**: Metrics are logged to stdout, CloudWatch automatically parses them
2. **Cost-effective**: Only pays for log ingestion (~$0.50/GB), not per-metric
3. **Batch-friendly**: Can include multiple metrics in a single log line
4. **Already available**: The codebase already has `EmbeddedMetricsFormatter` class

### Implementation Strategy

1. **Collect metrics during processing** instead of sending immediately
2. **Use EMF to log all metrics at once** at the end of each invocation
3. **Keep individual metric calls for critical errors** (optional, can be disabled)

## Recommended Changes

### Option 1: Switch to EMF (Recommended)

Replace individual `metrics.count()` and `metrics.gauge()` calls with:
- Collect metrics in a dictionary during processing
- Use `emf_metrics.log_metrics()` at the end to send all metrics in one log line

**Cost reduction**: From ~10-15 API calls per invocation to **0 API calls** (just log ingestion)

### Option 2: Batch API Calls

Use `put_metric_batch()` to send up to 20 metrics per API call instead of individual calls.

**Cost reduction**: From ~10-15 API calls per invocation to **1 API call** per invocation

### Option 3: Reduce Metric Granularity

- Remove per-record metrics (biggest cost driver)
- Keep only per-invocation summary metrics
- Use CloudWatch Logs Insights for detailed analysis instead

**Cost reduction**: From ~10-15 API calls per invocation to **~5 API calls** per invocation

## Event Source Mapping Configuration

Current configuration in `lambda_functions.py`:
```python
batch_size=10  # Max records per invocation
maximum_batching_window_in_seconds=5  # Batch window
parallelization_factor=5  # Parallel shard processing
```

**Recommendation**: These settings are reasonable for throughput but amplify metric costs. Consider:
- Reducing `parallelization_factor` if cost is a concern
- Increasing `batch_size` to reduce invocations (but increases per-invocation metrics)

## Monitoring Recommendations

1. **Set up CloudWatch billing alerts** for custom metrics
2. **Monitor Lambda invocation count** to detect unusual activity
3. **Review DynamoDB stream activity** to understand what triggered the spike
4. **Consider disabling metrics** (`ENABLE_METRICS=false`) during bulk operations

## Next Steps

1. ✅ Analyze current metrics usage (this document)
2. ⏳ Implement EMF-based metrics (recommended solution)
3. ⏳ Test the changes in dev environment
4. ⏳ Monitor cost reduction
5. ⏳ Review DynamoDB stream logs to understand what caused the spike

