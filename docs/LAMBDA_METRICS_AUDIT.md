# Lambda Functions Metrics Usage Audit

## Overview

This document audits all Lambda functions that use CloudWatch custom metrics via `metrics.count()` and `metrics.gauge()` calls to assess cost impact and identify candidates for EMF migration.

**Date**: 2025-01-07
**Status**: Audit Complete - Migration Plan Needed

---

## Summary

| Category | Count | Status |
|----------|-------|--------|
| **Lambda Functions Using Metrics** | 19 files | ⚠️ Needs Review |
| **High-Priority (High Frequency)** | 3 | 🔴 Critical |
| **Medium-Priority (Moderate Frequency)** | 8 | 🟡 Medium |
| **Low-Priority (Low Frequency)** | 8 | 🟢 Low |
| **Already Using EMF** | 1 | ✅ Fixed |

---

## High-Priority Functions (Critical - Migrate First)

### 1. Stream Processor ✅ **FIXED**
- **File**: `infra/chromadb_compaction/lambdas/stream_processor.py`
- **Status**: ✅ **Already migrated to EMF**
- **Metrics Calls**: 0 (was ~10-15 per invocation)
- **Invocation Frequency**: High (DynamoDB stream events)
- **Cost Impact**: Was high, now minimal
- **Action**: None needed

### 2. Word Polling Handler 🔴 **HIGH PRIORITY**
- **File**: `infra/embedding_step_functions/unified_embedding/handlers/word_polling.py`
- **Metrics Calls**: ~15-20 per invocation
- **Invocation Frequency**: High (polling every few minutes)
- **Metrics Used**:
  - `WordPollingCircuitBreakerBlocked` (1)
  - `WordPollingTimeouts` (multiple, with dimensions)
  - `WordPollingErrors` (multiple, with dimensions)
  - `WordPollingInvocations` (1)
  - `BatchStatusChecked` (1, with dimensions)
  - `DownloadedResults` (1 gauge)
  - `ProcessedDescriptions` (1 gauge)
  - `SavedEmbeddings` (1 gauge)
  - `DeltasSaved` (1, with dimensions)
  - `WordPollingSuccess` (1)
- **Estimated Cost Impact**: Medium-High
  - If invoked 1000x/month: ~15,000-20,000 metric calls
  - Cost: ~$4,500-6,000/month (after free tier)
- **Action**: 🔴 **Migrate to EMF**

### 3. Line Polling Handler 🔴 **HIGH PRIORITY**
- **File**: `infra/embedding_step_functions/unified_embedding/handlers/line_polling.py`
- **Metrics Calls**: ~15-20 per invocation
- **Invocation Frequency**: High (polling every few minutes)
- **Metrics Used**:
  - `LinePollingCircuitBreakerBlocked` (1)
  - `LinePollingTimeouts` (multiple, with dimensions)
  - `LinePollingErrors` (multiple, with dimensions)
  - `LinePollingInvocations` (1)
  - `BatchStatusChecked` (1, with dimensions)
  - `DownloadedResults` (1 gauge)
  - `ProcessedDescriptions` (1 gauge)
  - `SavedEmbeddings` (1 gauge)
  - `DeltasSaved` (1, with dimensions)
  - `LinePollingSuccess` (1)
- **Estimated Cost Impact**: Medium-High
  - If invoked 1000x/month: ~15,000-20,000 metric calls
  - Cost: ~$4,500-6,000/month (after free tier)
- **Action**: 🔴 **Migrate to EMF**

### 4. Upload Images Handler 🔴 **HIGH PRIORITY**
- **File**: `infra/upload_images/container_ocr/handler/handler.py`
- **Metrics Calls**: ~8-12 per record processed
- **Invocation Frequency**: High (per image upload)
- **Metrics Used**:
  - `UploadLambdaRecordsReceived` (1 gauge per invocation)
  - `UploadLambdaSuccess` (1 per record)
  - `UploadLambdaError` (1 per error)
  - `UploadLambdaEmbeddingsCreated` (1 per embedding)
  - `UploadLambdaOCRFailed` (1 per failure)
  - `UploadLambdaOCRSuccess` (1 per success, with dimensions)
  - `UploadLambdaEmbeddingSuccess` (1 per success, with dimensions)
  - `UploadLambdaMerchantResolved` (1 per resolution, with dimensions)
  - `UploadLambdaEmbeddingFailed` (1 per failure, with dimensions)
  - `UploadLambdaEmbeddingSkipped` (1 per skip, with dimensions)
- **Estimated Cost Impact**: High
  - If processing 1000 images/month: ~8,000-12,000 metric calls
  - Cost: ~$2,400-3,600/month (after free tier)
- **Action**: 🔴 **Migrate to EMF**

---

## Medium-Priority Functions (Moderate Frequency)

### 5. Enhanced Compaction Handler 🟡 **MEDIUM PRIORITY**
- **File**: `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`
- **Metrics Calls**: ~3-5 per invocation
- **Invocation Frequency**: Moderate (SQS-triggered)
- **Metrics Used**:
  - `CompactionRecordsReceived` (1 gauge)
  - `CompactionLambdaSuccess` (1)
  - `CompactionDirectInvocationAttempt` (1)
  - `CompactionLambdaError` (1, with dimensions)
- **Estimated Cost Impact**: Low-Medium
  - If invoked 500x/month: ~1,500-2,500 metric calls
  - Cost: ~$450-750/month (after free tier)
- **Action**: 🟡 **Consider EMF migration**

### 6. Circuit Breaker Utilities 🟡 **MEDIUM PRIORITY**
- **Files**:
  - `infra/embedding_step_functions/unified_embedding/utils/circuit_breaker.py`
  - `infra/chromadb_compaction/lambdas/utils/circuit_breaker.py`
- **Metrics Calls**: ~2-4 per circuit breaker state change
- **Invocation Frequency**: Moderate (only on state changes)
- **Metrics Used**:
  - `CircuitBreakerStateChange` (1 per state change, with dimensions)
  - `CircuitBreakerBlocked` (1 per blocked call)
  - `CircuitBreakerCallDuration` (1 gauge per call, with dimensions)
  - `CircuitBreakerFailure` (1 per failure, with dimensions)
- **Estimated Cost Impact**: Low-Medium
  - State changes are infrequent, but each call can generate metrics
  - Cost depends on circuit breaker usage
- **Action**: 🟡 **Consider EMF migration for batch operations**

### 7. Timeout Handler Utilities 🟡 **MEDIUM PRIORITY**
- **Files**:
  - `infra/embedding_step_functions/unified_embedding/utils/timeout_handler.py`
  - `infra/chromadb_compaction/lambdas/utils/timeout_handler.py`
- **Metrics Calls**: ~1-2 per heartbeat + 1 on timeout
- **Invocation Frequency**: Moderate (heartbeat every 30 seconds during execution)
- **Metrics Used**:
  - `LambdaRemainingTime` (1 gauge per heartbeat)
  - `LambdaTimeoutHandled` (1 per timeout)
- **Estimated Cost Impact**: Low-Medium
  - Heartbeat metrics can accumulate during long-running Lambdas
  - Cost depends on Lambda execution duration
- **Action**: 🟡 **Consider EMF migration for heartbeat metrics**

### 8. Graceful Shutdown Utilities 🟡 **MEDIUM PRIORITY**
- **Files**:
  - `infra/embedding_step_functions/unified_embedding/utils/graceful_shutdown.py`
  - `infra/chromadb_compaction/lambdas/utils/graceful_shutdown.py`
- **Metrics Calls**: ~1-2 per shutdown event
- **Invocation Frequency**: Low (only on shutdown/timeout)
- **Metrics Used**:
  - `GracefulShutdownInitiated` (1, with dimensions)
  - `GracefulShutdownTimeout` (1, with dimensions)
- **Estimated Cost Impact**: Low
  - Only triggered on shutdown events
- **Action**: 🟡 **Low priority - consider EMF if migrating other utilities**

### 9. SQS Publisher 🟡 **MEDIUM PRIORITY**
- **File**: `infra/chromadb_compaction/lambdas/processor/sqs_publisher.py`
- **Metrics Calls**: ~2-3 per batch
- **Invocation Frequency**: Moderate (per SQS batch)
- **Metrics Used**:
  - `SQSMessagesSuccessful` (1 per batch)
  - `SQSMessagesFailed` (1 per batch, with dimensions)
  - `SQSBatchError` (1 per error)
- **Estimated Cost Impact**: Low-Medium
  - Depends on SQS batch frequency
- **Action**: 🟡 **Consider EMF migration**

### 10. Message Builder 🟡 **MEDIUM PRIORITY**
- **File**: `infra/chromadb_compaction/lambdas/processor/message_builder.py`
- **Metrics Calls**: ~1-3 per message building operation
- **Invocation Frequency**: Moderate (per stream record)
- **Metrics Used**:
  - `CompactionRunMessageBuildError` (1 per error)
  - `CompactionRunCompletionDetected` (1)
  - `CompactionRunCompletionMessageBuildError` (1 per error)
  - `ChromaDBRelevantChanges` (1 per change set)
  - `StreamMessageCreated` (1 per message)
  - `EntityMessageBuildError` (1 per error)
- **Estimated Cost Impact**: Low-Medium
  - Depends on stream activity
- **Action**: 🟡 **Consider EMF migration**

### 11. Parsers 🟡 **MEDIUM PRIORITY**
- **File**: `infra/chromadb_compaction/lambdas/processor/parsers.py`
- **Metrics Calls**: ~1-2 per parsing error
- **Invocation Frequency**: Low (only on errors)
- **Metrics Used**:
  - `EntityParsingError` (1 per error)
  - `EntityParsingUnexpectedError` (1 per error)
  - `StreamRecordParsingError` (1 per error)
- **Estimated Cost Impact**: Low
  - Only on errors
- **Action**: 🟡 **Low priority**

### 12. Compaction Operations 🟡 **MEDIUM PRIORITY**
- **Files**:
  - `infra/chromadb_compaction/lambdas/compaction/operations.py`
  - `infra/chromadb_compaction/lambdas/compaction/metadata_handler.py`
  - `infra/chromadb_compaction/lambdas/compaction/label_handler.py`
  - `infra/chromadb_compaction/lambdas/compaction/message_builder.py`
  - `infra/chromadb_compaction/lambdas/compaction/efs_snapshot_manager.py`
  - `infra/chromadb_compaction/lambdas/compaction/compaction_run.py`
- **Metrics Calls**: Varies (1-5 per operation)
- **Invocation Frequency**: Moderate (compaction operations)
- **Metrics Used**: Various compaction-specific metrics
- **Estimated Cost Impact**: Low-Medium
  - Depends on compaction frequency
- **Action**: 🟡 **Review and consider EMF migration**

---

## Cost Impact Analysis

### Total Estimated Monthly Cost (Before EMF Migration)

Assuming moderate usage:
- **Word Polling**: ~$5,000/month
- **Line Polling**: ~$5,000/month
- **Upload Images**: ~$3,000/month
- **Enhanced Compaction**: ~$600/month
- **Other Functions**: ~$1,000/month

**Total Estimated**: ~$14,600/month (after free tier)

### After EMF Migration

All metrics via logs: ~$0.10-1.00/month (log ingestion only)

**Potential Savings**: ~$14,599/month (99.9% reduction)

---

## Migration Priority

### Phase 1: Critical (Immediate)
1. ✅ Stream Processor (Already done)
2. 🔴 Word Polling Handler
3. 🔴 Line Polling Handler
4. 🔴 Upload Images Handler

### Phase 2: High Impact (Next Sprint)
5. 🟡 Enhanced Compaction Handler
6. 🟡 SQS Publisher
7. 🟡 Message Builder

### Phase 3: Utilities (Future)
8. 🟡 Circuit Breaker Utilities
9. 🟡 Timeout Handler Utilities
10. 🟡 Graceful Shutdown Utilities

### Phase 4: Low Priority (As Needed)
11. 🟡 Parsers
12. 🟡 Compaction Operations

---

## Migration Strategy

### For Each Function:

1. **Collect Metrics During Processing**
   ```python
   collected_metrics = {}
   # Instead of: metrics.count("MetricName", 1)
   # Do: collected_metrics["MetricName"] = collected_metrics.get("MetricName", 0) + 1
   ```

2. **Log All Metrics at Once via EMF**
   ```python
   from utils import emf_metrics
   emf_metrics.log_metrics(
       collected_metrics,
       dimensions=metric_dimensions,
       properties=additional_properties
   )
   ```

3. **Aggregate Per-Record Metrics**
   - Count records during processing
   - Send aggregated count in one metric

4. **Test and Verify**
   - Verify metrics appear in CloudWatch Metrics dashboard
   - Check CloudWatch Logs for EMF format
   - Monitor cost reduction

---

## Recommendations

1. **Immediate Action**: Migrate high-priority functions (Word/Line Polling, Upload Images)
2. **Set Up Billing Alerts**: ✅ Done (see `billing_alerts.py`)
3. **Monitor Costs**: Track CloudWatch custom metrics costs monthly
4. **Code Review**: Add checklist item to prevent new functions from using old pattern
5. **Documentation**: Update development guidelines to prefer EMF

---

## References

- [STREAM_PROCESSOR_METRICS_COST_REVIEW.md](./STREAM_PROCESSOR_METRICS_COST_REVIEW.md) - Cost review
- [STREAM_PROCESSOR_EMF_METRICS_EXPLANATION.md](./STREAM_PROCESSOR_EMF_METRICS_EXPLANATION.md) - EMF technical details
- [AWS Embedded Metrics Format](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html)



