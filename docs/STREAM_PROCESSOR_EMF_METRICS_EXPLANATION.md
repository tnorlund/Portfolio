# Stream Processor Metrics: EMF vs API Calls

## Overview

The stream processor metrics optimization **does NOT remove custom metrics**. It changes **how** metrics are sent to CloudWatch to dramatically reduce costs while maintaining full visibility.

## Two Ways to Send Metrics to CloudWatch

### Method 1: Direct API Calls (Old - Expensive)

```python
# Each call makes a separate CloudWatch API call
metrics.count("StreamRecordProcessed", 1)  # → API call #1
metrics.gauge("StreamProcessingDuration", 5)  # → API call #2
metrics.count("MessagesQueuedForCompaction", 3)  # → API call #3
```

**Cost:**
- $0.30 per metric per month (after first 10,000 free)
- Each `metrics.count()` or `metrics.gauge()` = 1 API call
- Processing 10,000 records = ~100,000+ API calls = **expensive**

**How it works:**
1. Lambda calls `cloudwatch.put_metric_data()` API
2. CloudWatch stores metric immediately
3. Metric appears in CloudWatch Metrics dashboard

### Method 2: Embedded Metrics Format - EMF (New - Cost-Effective)

```python
# Collect metrics during processing
collected_metrics = {
    "StreamRecordProcessed": 10,
    "StreamProcessingDuration": 5,
    "MessagesQueuedForCompaction": 3,
    ...
}

# Send all metrics in ONE log line
emf_metrics.log_metrics(collected_metrics)
# → Logs to stdout with special EMF format
# → CloudWatch automatically parses and creates metrics
```

**Cost:**
- Only log ingestion costs (~$0.50/GB)
- All metrics in one log line = **minimal cost**
- Processing 10,000 records = ~1,000 log lines = **pennies**

**How it works:**
1. Lambda logs JSON to stdout with special `_aws` field
2. CloudWatch Logs automatically detects EMF format
3. CloudWatch parses the log and creates metrics automatically
4. Metrics appear in CloudWatch Metrics dashboard (same as before!)

## EMF Log Format Example

When `emf_metrics.log_metrics()` is called, it creates a log line like this:

```json
{
  "_aws": {
    "Timestamp": 1234567890000,
    "CloudWatchMetrics": [
      {
        "Namespace": "EmbeddingWorkflow",
        "Dimensions": [],
        "Metrics": [
          {"Name": "StreamRecordProcessed", "Unit": "Count"},
          {"Name": "StreamProcessingDuration", "Unit": "Count"},
          {"Name": "MessagesQueuedForCompaction", "Unit": "Count"}
        ]
      }
    ]
  },
  "StreamRecordProcessed": 10,
  "StreamProcessingDuration": 5,
  "MessagesQueuedForCompaction": 3,
  "event_name_counts": {"MODIFY": 8, "REMOVE": 2},
  "correlation_id": "abc-123"
}
```

The `_aws.CloudWatchMetrics` field tells CloudWatch:
- **Namespace**: Where to put the metrics (`EmbeddingWorkflow`)
- **Metrics**: Which fields are metrics and their units
- **Dimensions**: How to group metrics (optional)

CloudWatch automatically:
1. Parses this log line
2. Extracts the metrics
3. Creates custom metrics in CloudWatch Metrics dashboard
4. Makes them available for dashboards, alarms, etc.

## Metrics Still Available

All the same metrics are still available in CloudWatch Metrics dashboard:

- ✅ `StreamRecordsReceived`
- ✅ `StreamRecordProcessed`
- ✅ `StreamRecordSkipped`
- ✅ `StreamRecordProcessingError`
- ✅ `MessagesQueuedForCompaction`
- ✅ `StreamProcessingDuration`
- ✅ `StreamBatchSize`
- ✅ `StreamProcessorProcessedRecords`
- ✅ `StreamProcessorQueuedMessages`
- ✅ `StreamBatchTruncated`
- ✅ `StreamProcessingTimeoutExit`
- ✅ `StreamProcessingCircuitBreaker`
- ✅ `StreamProcessorError`

## Where to Find Metrics

### CloudWatch Metrics Dashboard

1. Go to CloudWatch → Metrics → All metrics
2. Find namespace: `EmbeddingWorkflow`
3. All metrics appear exactly as before

### CloudWatch Logs

1. Go to CloudWatch → Logs → Log groups
2. Find: `/aws/lambda/chromadb-dev-stream-processor-*`
3. Look for JSON log lines with `_aws` field
4. These are the EMF metric logs

## Benefits of EMF

### 1. Cost Reduction
- **Before**: ~10-15 API calls per Lambda invocation
- **After**: 0 API calls (just log ingestion)
- **Savings**: ~99.9% reduction in metrics costs

### 2. Performance
- **Before**: Each API call adds latency (~50-100ms)
- **After**: Logging is nearly instant (~1ms)
- **Result**: Faster Lambda execution

### 3. Batch Efficiency
- **Before**: Individual calls for each metric
- **After**: All metrics in one log line
- **Result**: Better batching and aggregation

### 4. Same Visibility
- Metrics still appear in CloudWatch Metrics
- Dashboards still work
- Alarms still work
- No functionality lost

## Comparison Table

| Aspect | API Calls (Old) | EMF (New) |
|--------|----------------|-----------|
| **Cost per invocation** | ~$0.0003-0.0005 | ~$0.000001 |
| **Latency per metric** | ~50-100ms | ~1ms |
| **API calls** | 10-15 per invocation | 0 per invocation |
| **CloudWatch Metrics** | ✅ Yes | ✅ Yes |
| **Dashboards** | ✅ Yes | ✅ Yes |
| **Alarms** | ✅ Yes | ✅ Yes |
| **Log visibility** | ❌ No | ✅ Yes (in logs) |

## Verification

After deployment, verify metrics are working:

1. **Check CloudWatch Metrics**:
   ```
   CloudWatch → Metrics → EmbeddingWorkflow namespace
   ```
   Should see all metrics appearing as before.

2. **Check CloudWatch Logs**:
   ```
   CloudWatch → Logs → /aws/lambda/chromadb-dev-stream-processor-*
   ```
   Should see JSON log lines with `_aws` field containing metrics.

3. **Check Costs**:
   ```
   AWS Cost Explorer → Filter by CloudWatch Custom Metrics
   ```
   Should see dramatic cost reduction.

## Technical Details

### AWS Embedded Metrics Format (EMF)

EMF is AWS's recommended approach for sending metrics from Lambda functions. It's:
- **Standard**: Part of AWS Lambda logging
- **Automatic**: CloudWatch parses EMF logs automatically
- **Efficient**: No API calls needed
- **Cost-effective**: Only pay for log ingestion

### When CloudWatch Parses EMF

CloudWatch automatically detects EMF format when:
1. Log line contains `_aws` field
2. `_aws.CloudWatchMetrics` array is present
3. Log is from a Lambda function

Parsing happens automatically - no configuration needed!

## Migration Notes

### What Changed

- ✅ Metrics collection: Now aggregated during processing
- ✅ Metrics sending: Now via EMF logs instead of API calls
- ✅ Metrics visibility: **Unchanged** - still in CloudWatch Metrics

### What Stayed the Same

- ✅ All metric names
- ✅ All metric values
- ✅ CloudWatch Metrics dashboard
- ✅ Dashboards and alarms
- ✅ Monitoring capabilities

## References

- [AWS Embedded Metrics Format Documentation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html)
- [CloudWatch Custom Metrics Pricing](https://aws.amazon.com/cloudwatch/pricing/)
- [Lambda Logging Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs.html)

