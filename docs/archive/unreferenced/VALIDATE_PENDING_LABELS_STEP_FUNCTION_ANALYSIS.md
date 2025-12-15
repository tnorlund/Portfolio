# Validate Pending Labels: Step Function vs Lambda Analysis

## Executive Summary

**Recommendation: Use Step Functions** ✅

A Step Function is the better choice for `validate_pending_labels` because:
1. **Parallel processing**: Process 10+ receipts simultaneously (vs sequential in Lambda)
2. **No timeout issues**: Step Functions can run for 1 year (vs 15-minute Lambda limit)
3. **Better error handling**: Built-in retry and catch logic
4. **Consistent pattern**: Matches existing infrastructure (`validate_merchant_step_functions`, `realtime_embedding_workflow`)
5. **Better observability**: Visual workflow in AWS Console

---

## Comparison: Lambda vs Step Functions

### Lambda Approach

**Workflow**:
```
EventBridge Schedule (Weekly)
  ↓
Lambda Function (15 min timeout)
  ↓
1. List all PENDING labels
2. Group by receipt
3. Process receipts sequentially (one at a time)
4. Update DynamoDB
```

**Limitations**:
- ❌ **15-minute timeout**: Can only process ~10-15 receipts per invocation
- ❌ **Sequential processing**: Each receipt takes 30-60 seconds (LangGraph + CoVe)
- ❌ **Batch management**: Need to implement pagination/continuation tokens
- ❌ **Error handling**: Manual retry logic required
- ❌ **Observability**: Limited to CloudWatch Logs

**Example Timeline** (100 receipts with PENDING labels):
- Sequential processing: 100 receipts × 45 seconds = **75 minutes**
- Lambda timeout: 15 minutes
- **Solution**: Need 5+ Lambda invocations with continuation tokens
- **Complexity**: High (pagination, state management, error recovery)

---

### Step Functions Approach

**Workflow**:
```
EventBridge Schedule (Weekly)
  ↓
Step Function
  ↓
1. ListPendingLabels (Lambda)
  ↓
2. CheckReceipts (Choice state)
  ↓
3. ProcessReceipts (Map state, MaxConcurrency: 10)
   ├─→ ProcessReceipt (Lambda) - Receipt 1
   ├─→ ProcessReceipt (Lambda) - Receipt 2
   ├─→ ProcessReceipt (Lambda) - Receipt 3
   └─→ ... (10 in parallel)
  ↓
4. ReportResults (Lambda) - Optional
```

**Advantages**:
- ✅ **No timeout**: Can run for up to 1 year
- ✅ **Parallel processing**: 10 receipts simultaneously
- ✅ **Built-in retry**: Automatic retry with exponential backoff
- ✅ **Error handling**: Catch blocks for failed receipts
- ✅ **Observability**: Visual workflow in AWS Console
- ✅ **State management**: Automatic state tracking

**Example Timeline** (100 receipts with PENDING labels):
- Parallel processing: 100 receipts ÷ 10 concurrency × 45 seconds = **7.5 minutes**
- Step Function duration: ~10 minutes (including overhead)
- **Solution**: Single Step Function execution
- **Complexity**: Low (AWS handles orchestration)

---

## Architecture Design

### Step Function State Machine

```json
{
  "Comment": "Validate PENDING labels using LangGraph + CoVe",
  "StartAt": "ListPendingLabels",
  "States": {
    "ListPendingLabels": {
      "Type": "Task",
      "Resource": "${ListPendingLabelsLambdaArn}",
      "ResultPath": "$.list_result",
      "Next": "CheckReceipts",
      "Retry": [
        {
          "ErrorEquals": [
            "Lambda.ServiceException",
            "Lambda.AWSLambdaException",
            "Runtime.ExitError"
          ],
          "IntervalSeconds": 2,
          "MaxAttempts": 3,
          "BackoffRate": 2.0
        }
      ]
    },
    "CheckReceipts": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.list_result.receipts[0]",
          "IsPresent": true,
          "Next": "ProcessReceipts"
        }
      ],
      "Default": "NoReceipts"
    },
    "NoReceipts": {
      "Type": "Pass",
      "Result": {
        "message": "No receipts with PENDING labels found",
        "count": 0
      },
      "End": true
    },
    "ProcessReceipts": {
      "Type": "Map",
      "ItemsPath": "$.list_result.receipts",
      "MaxConcurrency": 10,
      "Retry": [
        {
          "ErrorEquals": ["States.ALL"],
          "IntervalSeconds": 5,
          "MaxAttempts": 2,
          "BackoffRate": 2.0
        }
      ],
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "Next": "HandleReceiptError",
          "ResultPath": "$.error"
        }
      ],
      "Iterator": {
        "StartAt": "ValidateReceipt",
        "States": {
          "ValidateReceipt": {
            "Type": "Task",
            "Resource": "${ValidateReceiptLambdaArn}",
            "End": true
          }
        }
      },
      "Next": "ReportResults"
    },
    "HandleReceiptError": {
      "Type": "Pass",
      "Result": {
        "error": "Failed to validate receipt",
        "receipt": "$.error"
      },
      "Next": "ReportResults"
    },
    "ReportResults": {
      "Type": "Task",
      "Resource": "${ReportResultsLambdaArn}",
      "End": true
    }
  }
}
```

---

## Lambda Functions

### 1. `list_pending_labels` Lambda

**Purpose**: List all receipts that have PENDING labels

**Input**:
```json
{
  "limit": null  // Optional, null = all receipts
}
```

**Output**:
```json
{
  "receipts": [
    {
      "image_id": "abc123",
      "receipt_id": 1,
      "pending_label_count": 5
    },
    {
      "image_id": "def456",
      "receipt_id": 2,
      "pending_label_count": 3
    }
  ],
  "total_receipts": 2,
  "total_pending_labels": 8
}
```

**Implementation**:
```python
def list_handler(event, context):
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.entities import ReceiptWordLabel

    dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    limit = event.get("limit")

    # Query all PENDING labels
    pending_labels = list_all_pending_labels(dynamo, limit=limit)

    # Group by receipt
    receipts_dict = group_labels_by_receipt(pending_labels)

    # Format for Step Function
    receipts = [
        {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "pending_label_count": len(labels)
        }
        for (image_id, receipt_id), labels in receipts_dict.items()
    ]

    return {
        "receipts": receipts,
        "total_receipts": len(receipts),
        "total_pending_labels": len(pending_labels)
    }
```

---

### 2. `validate_receipt` Lambda

**Purpose**: Validate PENDING labels for a single receipt using LangGraph + CoVe

**Input** (from Map state):
```json
{
  "image_id": "abc123",
  "receipt_id": 1,
  "pending_label_count": 5
}
```

**Output**:
```json
{
  "success": true,
  "image_id": "abc123",
  "receipt_id": 1,
  "labels_validated": 5,
  "labels_updated": 3,
  "labels_added": 2,
  "errors": []
}
```

**Implementation**:
```python
def validate_handler(event, context):
    import asyncio
    from receipt_dynamo import DynamoClient
    from receipt_label.langchain.currency_validation import analyze_receipt_simple

    async def run():
        dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
        image_id = event["image_id"]
        receipt_id = event["receipt_id"]

        # Fetch existing PENDING labels
        pending_labels = list_pending_labels_for_receipt(dynamo, image_id, receipt_id)

        # Run LangGraph + CoVe
        result = await analyze_receipt_simple(
            client=dynamo,
            image_id=image_id,
            receipt_id=receipt_id,
            ollama_api_key=os.environ["OLLAMA_API_KEY"],
            langsmith_api_key=os.environ.get("LANGSMITH_API_KEY"),
            save_labels=False,  # We'll update manually
            dry_run=False,
        )

        # Compare and update labels
        comparison = compare_labels(
            existing_labels=pending_labels,
            new_labels=result.receipt_word_labels_to_add + result.receipt_word_labels_to_update
        )

        # Update DynamoDB
        if comparison["labels_to_update"]:
            dynamo.update_receipt_word_labels(comparison["labels_to_update"])

        if comparison["labels_to_add"]:
            dynamo.add_receipt_word_labels(comparison["labels_to_add"])

        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "labels_validated": len(pending_labels),
            "labels_updated": len(comparison["labels_to_update"]),
            "labels_added": len(comparison["labels_to_add"]),
        }

    return asyncio.run(run())
```

---

### 3. `report_results` Lambda (Optional)

**Purpose**: Aggregate and report validation results

**Input** (from Map state results):
```json
{
  "results": [
    {
      "success": true,
      "image_id": "abc123",
      "receipt_id": 1,
      "labels_validated": 5,
      "labels_updated": 3
    },
    ...
  ]
}
```

**Output**:
```json
{
  "summary": {
    "total_receipts": 100,
    "receipts_succeeded": 95,
    "receipts_failed": 5,
    "total_labels_validated": 450,
    "total_labels_updated": 320
  }
}
```

---

## Infrastructure Structure

```
infra/validate_pending_labels_step_functions/
├── __init__.py
├── validate_pending_labels_step_functions.py  # Pulumi component
├── handlers/
│   ├── __init__.py
│   ├── common.py                              # Shared utilities
│   ├── list_pending_labels.py                 # List Lambda handler
│   ├── validate_receipt.py                    # Validate Lambda handler
│   └── report_results.py                      # Report Lambda handler (optional)
└── README.md
```

---

## Cost Comparison

### Lambda Approach (Sequential)

**Scenario**: 100 receipts, 45 seconds per receipt

- **Executions**: 5 Lambda invocations (15 min timeout each)
- **Duration**: 15 minutes × 5 = 75 minutes total
- **Memory**: 3008 MB
- **Cost**: ~$X per week

### Step Functions Approach (Parallel)

**Scenario**: 100 receipts, 10 concurrent, 45 seconds per receipt

- **Executions**: 1 Step Function execution
- **Duration**: ~10 minutes total
- **Lambda invocations**: 1 (list) + 100 (validate) + 1 (report) = 102
- **Memory**: 3008 MB per validate Lambda
- **Step Function cost**: ~$Y per week
- **Total cost**: ~$Z per week

**Note**: Step Functions pricing is based on state transitions, not execution time. For 100 receipts with 10 concurrency, this is very cost-effective.

---

## Performance Comparison

| Metric | Lambda (Sequential) | Step Functions (Parallel) |
|--------|-------------------|-------------------------|
| **100 receipts** | 75 minutes | 10 minutes |
| **1000 receipts** | 12.5 hours (multiple invocations) | 100 minutes |
| **Timeout risk** | High (15 min limit) | None (1 year limit) |
| **Error recovery** | Manual | Automatic retry |
| **Observability** | CloudWatch Logs | Visual workflow + logs |

---

## Implementation Plan

### Phase 1: Create Lambda Functions

1. **`list_pending_labels` Lambda**
   - Query DynamoDB for PENDING labels
   - Group by receipt
   - Return formatted list

2. **`validate_receipt` Lambda**
   - Fetch receipt data (lines, words, metadata)
   - Run LangGraph + CoVe
   - Compare and update labels
   - Return results

3. **`report_results` Lambda** (Optional)
   - Aggregate results from Map state
   - Log summary to CloudWatch
   - Optionally send SNS notification

### Phase 2: Create Step Function

1. **State Machine Definition**
   - ListPendingLabels → CheckReceipts → ProcessReceipts (Map) → ReportResults
   - Configure retry and error handling
   - Set MaxConcurrency: 10

2. **IAM Roles and Policies**
   - Step Function execution role
   - Lambda invoke permissions
   - DynamoDB access permissions

### Phase 3: Schedule Execution

1. **EventBridge Rule**
   - Schedule: `cron(0 2 ? * SUN *)` (Sunday 2 AM UTC)
   - Target: Step Function

2. **IAM Permission**
   - EventBridge → Step Function invoke permission

---

## Migration from Dev Script

### Key Changes

1. **Remove ChromaDB download**: Not needed in Lambda (or use EFS if needed)
2. **Split into 3 Lambdas**: List, Validate, Report
3. **Add Step Function orchestration**: Map state for parallel processing
4. **Environment variables**: API keys from Pulumi secrets
5. **Error handling**: Use Step Function retry/catch instead of manual logic

### Code Reuse

- ✅ Reuse `list_all_pending_labels()` from dev script
- ✅ Reuse `group_labels_by_receipt()` from dev script
- ✅ Reuse `validate_receipt_with_cove()` from dev script
- ✅ Reuse `compare_labels()` from dev script

---

## Monitoring and Alerts

### CloudWatch Metrics

1. **Step Function Metrics**:
   - Executions started
   - Executions succeeded
   - Executions failed
   - Execution duration

2. **Lambda Metrics**:
   - Invocations
   - Duration
   - Errors
   - Throttles

3. **Custom Metrics** (from report_results Lambda):
   - Receipts processed
   - Labels validated
   - Labels updated
   - Success rate

### CloudWatch Alarms

1. **Step Function failures**: Alert if > 10% failure rate
2. **Lambda errors**: Alert if error rate > 5%
3. **Long duration**: Alert if execution > 30 minutes (indicates issues)

---

## Conclusion

**Step Functions is the clear winner** for `validate_pending_labels` because:

1. ✅ **10x faster**: Parallel processing vs sequential
2. ✅ **No timeout issues**: Can handle any number of receipts
3. ✅ **Better error handling**: Built-in retry and catch
4. ✅ **Consistent pattern**: Matches existing infrastructure
5. ✅ **Better observability**: Visual workflow in AWS Console
6. ✅ **Cost-effective**: Pay per state transition, not execution time

**Next Steps**:
1. Create `validate_pending_labels_step_functions` Pulumi component
2. Implement 3 Lambda handlers (list, validate, report)
3. Create Step Function state machine
4. Set up EventBridge schedule
5. Test in dev stack
6. Deploy to production

