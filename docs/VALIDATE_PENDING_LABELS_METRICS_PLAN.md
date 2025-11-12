# Validate Pending Labels Step Function - Metrics Plan

## Overview

This document outlines the CloudWatch metrics to track for the `validate_pending_labels` Step Function, using EMF (Embedded Metrics Format) to avoid expensive API calls.

## Metrics Strategy

**Key Principle**: Use EMF to batch all metrics into a single log line per Lambda invocation, avoiding per-metric API call costs.

---

## 1. ListPendingLabels Lambda Metrics

### Operational Metrics
- **`PendingLabelsListed`** (Count)
  - Total number of PENDING labels found across all receipts
  - **Why**: Track backlog size and growth trends
  - **Dimensions**: None (global metric)

- **`ReceiptsWithPendingLabels`** (Count)
  - Number of unique receipts that have PENDING labels
  - **Why**: Understand distribution (many receipts with few labels vs few receipts with many labels)
  - **Dimensions**: None

- **`ListPendingLabelsDuration`** (Milliseconds)
  - Time to query DynamoDB and create manifest
  - **Why**: Monitor performance, detect slow queries
  - **Dimensions**: None

- **`DynamoDBQueries`** (Count)
  - Number of DynamoDB paginated queries made
  - **Why**: Track query efficiency and cost
  - **Dimensions**: None

- **`ManifestSize`** (Count)
  - Number of receipts in the manifest
  - **Why**: Understand batch sizes
  - **Dimensions**: None

### Error Metrics
- **`ListPendingLabelsError`** (Count)
  - Number of errors during listing
  - **Why**: Track failures
  - **Dimensions**: `error_type` (DynamoDBError, S3Error, etc.)

### Properties (for detailed analysis)
- `total_pending_labels`: Total labels found
- `total_receipts`: Total receipts with pending labels
- `avg_labels_per_receipt`: Average labels per receipt
- `max_labels_per_receipt`: Maximum labels in a single receipt
- `execution_id`: Step Function execution ID

---

## 2. ValidateReceipt Lambda Metrics

### Validation Results (Most Important)
- **`LabelsValidatedChromaDB`** (Count)
  - Labels marked as VALID by ChromaDB similarity search
  - **Why**: Track ChromaDB effectiveness (cost-efficient validation)
  - **Dimensions**: None (aggregate across all receipts)

- **`LabelsInvalidatedChromaDB`** (Count)
  - Labels marked as INVALID by ChromaDB (conflicts detected)
  - **Why**: Track false positives caught by ChromaDB
  - **Dimensions**: None

- **`LabelsValidatedCoVe`** (Count)
  - Labels marked as VALID by CoVe fallback (LangGraph + LLM)
  - **Why**: Track CoVe usage (more expensive but more accurate)
  - **Dimensions**: None

- **`LabelsInvalidatedCoVe`** (Count)
  - Labels marked as INVALID by CoVe fallback
  - **Why**: Track CoVe invalidation rate
  - **Dimensions**: None

- **`LabelsStillPending`** (Count)
  - Labels that remain PENDING after both validation methods
  - **Why**: Track labels that need manual review or more data
  - **Dimensions**: None

- **`TotalLabelsProcessed`** (Count)
  - Total PENDING labels processed for this receipt
  - **Why**: Calculate validation success rate
  - **Dimensions**: None

### Performance Metrics
- **`ValidateReceiptDuration`** (Milliseconds)
  - Total time to validate all labels for a receipt
  - **Why**: Monitor performance, detect slow receipts
  - **Dimensions**: None

- **`ChromaDBDownloadDuration`** (Milliseconds)
  - Time to download ChromaDB snapshot from S3
  - **Why**: Track S3 download performance
  - **Dimensions**: None

- **`ChromaDBValidationDuration`** (Milliseconds)
  - Time spent in ChromaDB similarity search validation
  - **Why**: Track ChromaDB query performance
  - **Dimensions**: None

- **`CoVeFallbackDuration`** (Milliseconds)
  - Time spent in LangGraph + CoVe fallback (if used)
  - **Why**: Track expensive CoVe operations, optimize cost
  - **Dimensions**: None

- **`LangGraphExecutionDuration`** (Milliseconds)
  - Time for full LangGraph workflow execution (within CoVe fallback)
  - **Why**: Track LLM processing time
  - **Dimensions**: None

### Cost Efficiency Metrics
- **`CoVeFallbackTriggered`** (Count)
  - Number of receipts that required CoVe fallback
  - **Why**: Track when ChromaDB isn't sufficient (cost indicator)
  - **Dimensions**: None

- **`ChromaDBOnlyValidation`** (Count)
  - Number of receipts validated entirely by ChromaDB (no CoVe needed)
  - **Why**: Track cost-efficient validations
  - **Dimensions**: None

- **`ValidationEfficiencyRatio`** (Gauge)
  - Ratio: ChromaDB validations / Total validations
  - **Why**: Monitor cost efficiency over time (higher = cheaper)
  - **Dimensions**: None

### Error Metrics
- **`ValidateReceiptError`** (Count)
  - Number of errors during validation
  - **Why**: Track failures
  - **Dimensions**: `error_type` (ChromaDBError, LangGraphError, DynamoDBError, etc.)

- **`ChromaDBDownloadFailed`** (Count)
  - Number of ChromaDB download failures
  - **Why**: Track S3 access issues
  - **Dimensions**: None

- **`ChromaDBValidationSkipped`** (Count)
  - Number of receipts where ChromaDB validation was skipped (no client)
  - **Why**: Track configuration issues
  - **Dimensions**: None

### Properties (for detailed analysis)
- `image_id`: Receipt image ID
- `receipt_id`: Receipt ID
- `pending_labels_count`: Number of pending labels for this receipt
- `chromadb_validated`: Count validated by ChromaDB
- `chromadb_invalidated`: Count invalidated by ChromaDB
- `cove_validated`: Count validated by CoVe
- `cove_invalidated`: Count invalidated by CoVe
- `still_pending`: Count still pending
- `chromadb_download_time_ms`: ChromaDB download duration
- `chromadb_validation_time_ms`: ChromaDB validation duration
- `cove_fallback_time_ms`: CoVe fallback duration
- `total_duration_ms`: Total validation duration
- `used_cove_fallback`: Boolean (true if CoVe was used)

---

## 3. Step Function Level Metrics

### Execution Metrics (Automatically tracked by AWS)
- **Executions Started** (AWS managed)
- **Executions Succeeded** (AWS managed)
- **Executions Failed** (AWS managed)
- **Execution Duration** (AWS managed)

### Custom Aggregated Metrics (via CloudWatch Insights or Lambda)
- **`TotalReceiptsProcessed`** (Count)
  - Total receipts processed in this execution
  - **Why**: Track throughput
  - **Dimensions**: `execution_id`

- **`TotalLabelsValidated`** (Count)
  - Sum of all labels validated (ChromaDB + CoVe)
  - **Why**: Track overall validation success
  - **Dimensions**: `execution_id`

- **`TotalLabelsInvalidated`** (Count)
  - Sum of all labels invalidated
  - **Dimensions**: `execution_id`

- **`TotalLabelsStillPending`** (Count)
  - Sum of all labels that remain PENDING
  - **Why**: Track backlog reduction
  - **Dimensions**: `execution_id`

- **`AverageValidationDuration`** (Milliseconds)
  - Average time per receipt validation
  - **Why**: Monitor performance trends
  - **Dimensions**: `execution_id`

- **`CoVeFallbackRate`** (Gauge)
  - Percentage of receipts that required CoVe fallback
  - **Why**: Track cost trends (lower = cheaper)
  - **Dimensions**: `execution_id`

---

## 4. Business Intelligence Metrics

### Validation Success Rate
- **`ValidationSuccessRate`** (Gauge, 0-100)
  - Percentage: (Validated + Invalidated) / Total PENDING
  - **Why**: Track how many labels we can definitively validate
  - **Calculation**: `(LabelsValidatedChromaDB + LabelsInvalidatedChromaDB + LabelsValidatedCoVe + LabelsInvalidatedCoVe) / TotalLabelsProcessed * 100`

### Cost Efficiency
- **`ChromaDBValidationRate`** (Gauge, 0-100)
  - Percentage: ChromaDB validations / Total validations
  - **Why**: Monitor cost efficiency (higher = cheaper)
  - **Calculation**: `(LabelsValidatedChromaDB + LabelsInvalidatedChromaDB) / (LabelsValidatedChromaDB + LabelsInvalidatedChromaDB + LabelsValidatedCoVe + LabelsInvalidatedCoVe) * 100`

### Label Distribution
- **`AverageLabelsPerReceipt`** (Gauge)
  - Average number of PENDING labels per receipt
  - **Why**: Understand workload distribution
  - **Calculation**: `TotalLabelsProcessed / ReceiptsWithPendingLabels`

---

## 5. Important Trends to Monitor Over Time

### Short-Term (Daily/Weekly)
1. **Backlog Growth**: Is the number of PENDING labels increasing or decreasing?
   - Metric: `PendingLabelsListed` over time
   - Alert: If backlog grows > 20% week-over-week

2. **Validation Success Rate**: Are we able to validate most labels?
   - Metric: `ValidationSuccessRate`
   - Alert: If success rate drops below 70%

3. **CoVe Fallback Rate**: Is ChromaDB becoming less effective?
   - Metric: `CoVeFallbackRate`
   - Alert: If CoVe fallback rate increases > 10% week-over-week (cost concern)

4. **Performance Degradation**: Are validations getting slower?
   - Metric: `AverageValidationDuration`
   - Alert: If average duration increases > 50% week-over-week

### Medium-Term (Monthly)
1. **Cost Efficiency**: Is ChromaDB handling more validations over time?
   - Metric: `ChromaDBValidationRate` trend
   - Goal: Increase over time as more VALID labels are added to ChromaDB

2. **Label Quality**: Are we invalidating more labels over time?
   - Metric: `LabelsInvalidatedChromaDB + LabelsInvalidatedCoVe` trend
   - Insight: May indicate initial labeling quality issues

3. **Backlog Reduction**: Are we making progress on the backlog?
   - Metric: `TotalLabelsStillPending` trend
   - Goal: Decrease over time

### Long-Term (Quarterly)
1. **System Maturity**: As ChromaDB accumulates more VALID labels, validation should become more efficient
   - Metrics: `ChromaDBValidationRate`, `CoVeFallbackRate`
   - Expected: ChromaDB rate should increase, CoVe rate should decrease

2. **Data Quality**: Are labels being validated correctly?
   - Metrics: Manual review of invalidated labels
   - Insight: High invalidation rate may indicate labeling issues

---

## 6. Implementation Pattern

### Example: ValidateReceipt Lambda

```python
import time
from typing import Dict, Any

# Collect metrics during processing
collected_metrics: Dict[str, float] = {}
start_time = time.time()

# ... processing ...

# Track validation results
collected_metrics["LabelsValidatedChromaDB"] = labels_validated_chromadb
collected_metrics["LabelsInvalidatedChromaDB"] = labels_invalidated_chromadb
collected_metrics["LabelsValidatedCoVe"] = labels_validated_cove
collected_metrics["LabelsInvalidatedCoVe"] = labels_invalidated_cove
collected_metrics["LabelsStillPending"] = labels_kept_pending
collected_metrics["TotalLabelsProcessed"] = len(pending_labels)

# Track performance
total_duration = (time.time() - start_time) * 1000  # milliseconds
collected_metrics["ValidateReceiptDuration"] = total_duration
collected_metrics["ChromaDBDownloadDuration"] = chromadb_download_time_ms
collected_metrics["ChromaDBValidationDuration"] = chromadb_validation_time_ms
collected_metrics["CoVeFallbackDuration"] = cove_fallback_time_ms

# Track cost efficiency
if still_pending_after_chromadb:
    collected_metrics["CoVeFallbackTriggered"] = 1
else:
    collected_metrics["ChromaDBOnlyValidation"] = 1

# Calculate efficiency ratio
total_validated = labels_validated_chromadb + labels_validated_cove
if total_validated > 0:
    efficiency = (labels_validated_chromadb + labels_invalidated_chromadb) / total_validated
    collected_metrics["ValidationEfficiencyRatio"] = efficiency

# Properties for detailed analysis
properties = {
    "image_id": image_id,
    "receipt_id": receipt_id,
    "pending_labels_count": len(pending_labels),
    "chromadb_validated": labels_validated_chromadb,
    "chromadb_invalidated": labels_invalidated_chromadb,
    "cove_validated": labels_validated_cove,
    "cove_invalidated": labels_invalidated_cove,
    "still_pending": labels_kept_pending,
    "used_cove_fallback": len(still_pending_after_chromadb) > 0,
}

# Log all metrics via EMF (single log line, no API calls)
emf_metrics.log_metrics(
    collected_metrics,
    dimensions=None,  # Or add dimensions if needed
    properties=properties,
)
```

---

## 7. CloudWatch Alarms to Consider

### Critical Alarms
1. **High Error Rate**
   - Metric: `ValidateReceiptError` / `TotalReceiptsProcessed`
   - Threshold: > 5% error rate
   - Action: Alert to SNS topic

2. **Step Function Failures**
   - Metric: AWS managed `ExecutionsFailed` / `ExecutionsStarted`
   - Threshold: > 10% failure rate
   - Action: Alert to SNS topic

### Warning Alarms
1. **Slow Performance**
   - Metric: `AverageValidationDuration`
   - Threshold: > 60 seconds per receipt
   - Action: Log warning

2. **High CoVe Fallback Rate**
   - Metric: `CoVeFallbackRate`
   - Threshold: > 50% of receipts require CoVe
   - Action: Log warning (cost concern)

3. **Low Validation Success Rate**
   - Metric: `ValidationSuccessRate`
   - Threshold: < 70%
   - Action: Log warning

---

## 8. Dashboard Recommendations

### Primary Dashboard: Validation Overview
- **Total PENDING Labels** (gauge)
- **Validation Success Rate** (gauge)
- **Labels Validated Today** (counter)
- **Labels Invalidated Today** (counter)
- **Labels Still Pending** (gauge)

### Secondary Dashboard: Performance & Cost
- **Average Validation Duration** (line chart)
- **ChromaDB vs CoVe Validation Rate** (pie chart)
- **CoVe Fallback Rate** (line chart)
- **ChromaDB Download Duration** (line chart)

### Tertiary Dashboard: Error Tracking
- **Error Rate** (line chart)
- **Error Types** (bar chart)
- **Failed Executions** (counter)

---

## 9. Cost Considerations

### Metrics Cost
- **EMF Approach**: ~$0.50/GB for log ingestion
- **Estimated**: ~1000 receipts × 1 log line = ~1MB logs = **$0.0005 per execution**
- **vs API Calls**: Would be ~$0.30 per metric × 20 metrics = **$6 per execution** ❌

### Savings
- **99.99% cost reduction** by using EMF instead of API calls

---

## 10. Next Steps

1. ✅ Review this metrics plan
2. ⏳ Implement EMF metrics in `list_pending_labels.py`
3. ⏳ Implement EMF metrics in `validate_receipt_handler.py`
4. ⏳ Create CloudWatch dashboard
5. ⏳ Set up CloudWatch alarms
6. ⏳ Document metrics in README

