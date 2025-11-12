# CoVe Scripts: Productionization Strategy

## Overview

This document categorizes the CoVe-enabled dev scripts and recommends which should be productionized as AWS Lambda functions with scheduled execution.

## Script Categorization

### ✅ Already Productionized (Upload Lambda)

**`dev.process_all_receipts_cove.py`** - **PARTIALLY HANDLED**

**Status**: Upload Lambda already processes new receipts with CoVe

**What Upload Lambda does**:
- Processes new receipts as they're uploaded
- Creates metadata with CoVe (via `create_receipt_metadata_simple`)
- Creates labels with CoVe (via `analyze_receipt_simple`)
- Validates metadata with CoVe (via `phase1_validate_metadata_llm`)

**What's missing**:
- Processing **existing** receipts (one-time migration)
- Periodic refresh of all receipts (monthly)

**Recommendation**:
- **One-off**: Keep `dev.process_all_receipts_cove.py` for initial processing and ad-hoc runs
- **Production**: Create a scheduled Lambda for monthly refresh (optional, low priority)

---

### 🚀 Should Be Productionized

#### 1. `dev.validate_pending_labels_chromadb.py`

**Purpose**: Validate PENDING labels using LangGraph + CoVe

**Why productionize**:
- **Critical for validation pipeline**: Ensures PENDING labels eventually become VALID/INVALID
- **Recurring need**: Should run weekly to catch PENDING labels from new uploads
- **Automated operation**: No manual intervention needed

**Recommended Schedule**: Weekly (Sunday 2 AM UTC)

**Implementation**:
- Create Lambda function: `validate_pending_labels_lambda`
- EventBridge schedule: `cron(0 2 ? * SUN *)`
- Memory: 3008 MB (for LangGraph + CoVe)
- Timeout: 15 minutes (processes receipts in batches)
- Layers: `dynamo_layer`, `label_layer`

**Cost**: Medium (only processes receipts with PENDING labels)

**Priority**: **HIGH** ⭐⭐⭐

---

#### 2. `dev.validate_receipt_metadata_cove.py`

**Purpose**: Validate ReceiptMetadata using CoVe

**Why productionize**:
- **Quality assurance**: Catches metadata errors early
- **Recurring need**: Should run weekly to validate metadata quality
- **Automated operation**: No manual intervention needed

**Recommended Schedule**: Weekly (Wednesday 2 AM UTC)

**Implementation**:
- Create Lambda function: `validate_metadata_lambda`
- EventBridge schedule: `cron(0 2 ? * WED *)`
- Memory: 2048 MB (for CoVe + Google Places API)
- Timeout: 10 minutes
- Layers: `dynamo_layer`, `label_layer`

**Cost**: Medium (metadata validation only, no label processing)

**Priority**: **MEDIUM** ⭐⭐

---

#### 3. `dev.reverify_word_labels_cove.py`

**Purpose**: Re-verify existing ReceiptWordLabels using CoVe

**Why productionize**:
- **Quality assurance**: Ensures label quality over time
- **Recurring need**: Should run quarterly to re-verify all labels
- **Automated operation**: No manual intervention needed

**Recommended Schedule**: Quarterly (First Sunday of quarter at 2 AM UTC)

**Implementation**:
- Create Lambda function: `reverify_labels_lambda`
- EventBridge schedule: `cron(0 2 1 */3 ? *)` (first day of quarter)
- Memory: 3008 MB (for LangGraph + CoVe)
- Timeout: 15 minutes (processes receipts in batches)
- Layers: `dynamo_layer`, `label_layer`

**Cost**: High (processes all receipts with labels)

**Priority**: **LOW** ⭐ (can be done manually or on-demand)

---

### 🔧 One-Off / Dev Only Scripts

#### 1. `dev.process_all_receipts_cove.py`

**Purpose**: Process all receipts end-to-end with CoVe

**Why keep as dev script**:
- **One-time migrations**: Initial processing of existing receipts
- **Ad-hoc runs**: After major CoVe improvements or model updates
- **Testing**: Validate changes before deploying to production
- **Flexibility**: Manual control over which receipts to process

**When to run**:
- Initial setup (one-time)
- After major CoVe changes (ad-hoc)
- Monthly refresh (optional, can be productionized if needed)

**Recommendation**: **Keep as dev script** ✅

---

## Productionization Implementation Plan

### Phase 1: High Priority (Week 1)

#### 1.1 Create `validate_pending_labels_lambda`

**Location**: `infra/lambda_functions/validate_pending_labels/`

**Files**:
- `infra.py` - Pulumi infrastructure
- `handler/index.py` - Lambda handler (adapted from `dev.validate_pending_labels_chromadb.py`)

**Key Changes from Dev Script**:
- Remove ChromaDB download (not needed in Lambda)
- Process receipts in batches (to stay within timeout)
- Use environment variables for API keys (from Pulumi secrets)
- Add error handling and retry logic
- Emit CloudWatch metrics

**Schedule**: `cron(0 2 ? * SUN *)` (Sunday 2 AM UTC)

---

### Phase 2: Medium Priority (Week 2)

#### 2.1 Create `validate_metadata_lambda`

**Location**: `infra/lambda_functions/validate_metadata/`

**Files**:
- `infra.py` - Pulumi infrastructure
- `handler/index.py` - Lambda handler (adapted from `dev.validate_receipt_metadata_cove.py`)

**Key Changes from Dev Script**:
- Remove ChromaDB download (not needed for metadata validation)
- Process receipts in batches
- Use environment variables for API keys
- Add error handling and retry logic
- Emit CloudWatch metrics

**Schedule**: `cron(0 2 ? * WED *)` (Wednesday 2 AM UTC)

---

### Phase 3: Low Priority (Future)

#### 3.1 Create `reverify_labels_lambda` (Optional)

**Location**: `infra/lambda_functions/reverify_labels/`

**Files**:
- `infra.py` - Pulumi infrastructure
- `handler/index.py` - Lambda handler (adapted from `dev.reverify_word_labels_cove.py`)

**Key Changes from Dev Script**:
- Remove ChromaDB download
- Process receipts in batches
- Use environment variables for API keys
- Add error handling and retry logic
- Emit CloudWatch metrics

**Schedule**: `cron(0 2 1 */3 ? *)` (First day of quarter at 2 AM UTC)

**Note**: This can be kept as a dev script and run manually/on-demand if preferred.

---

## Infrastructure Pattern

### Lambda Function Structure

```python
# infra/lambda_functions/validate_pending_labels/infra.py
import pulumi
import pulumi_aws as aws
from lambda_layer import dynamo_layer, label_layer
from dynamo_db import dynamodb_table

# 1. IAM Role
lambda_role = aws.iam.Role(...)

# 2. IAM Policy (DynamoDB access)
lambda_policy = aws.iam.Policy(...)

# 3. Lambda Function
lambda_func = aws.lambda_.Function(
    runtime="python3.12",
    architectures=["arm64"],
    memory_size=3008,
    timeout=900,  # 15 minutes
    layers=[dynamo_layer.arn, label_layer.arn],
    environment={
        "variables": {
            "DYNAMODB_TABLE_NAME": dynamodb_table.name,
            "OLLAMA_API_KEY": config.require_secret("OLLAMA_API_KEY"),
            "LANGSMITH_API_KEY": config.require_secret("LANGSMITH_API_KEY"),
            "GOOGLE_PLACES_API_KEY": config.require_secret("GOOGLE_PLACES_API_KEY"),
        }
    },
)

# 4. EventBridge Schedule
schedule = aws.cloudwatch.EventRule(
    schedule_expression="cron(0 2 ? * SUN *)",
)

# 5. EventBridge Target
target = aws.cloudwatch.EventTarget(
    rule=schedule.name,
    arn=lambda_func.arn,
)

# 6. Lambda Permission
permission = aws.lambda_.Permission(
    action="lambda:InvokeFunction",
    function=lambda_func.name,
    principal="events.amazonaws.com",
    source_arn=schedule.arn,
)
```

### Handler Pattern

```python
# infra/lambda_functions/validate_pending_labels/handler/index.py
import os
import asyncio
from receipt_dynamo import DynamoClient
from receipt_label.langchain.currency_validation import analyze_receipt_simple

def handler(event, context):
    """Lambda handler for validating PENDING labels."""
    async def run():
        dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
        ollama_api_key = os.environ["OLLAMA_API_KEY"]
        langsmith_api_key = os.environ.get("LANGSMITH_API_KEY")

        # 1. List all PENDING labels
        pending_labels = list_all_pending_labels(dynamo)

        # 2. Group by receipt
        receipts = group_labels_by_receipt(pending_labels)

        # 3. Process in batches (to stay within timeout)
        for batch in chunk_receipts(receipts, batch_size=10):
            for image_id, receipt_id in batch:
                # Run LangGraph + CoVe
                await analyze_receipt_simple(...)
                # Update labels
                # ...

    asyncio.run(run())
```

---

## Comparison: Dev Scripts vs Production Lambdas

| Aspect | Dev Scripts | Production Lambdas |
|--------|-------------|-------------------|
| **Execution** | Manual, local | Automated, scheduled |
| **Environment** | Local machine | AWS Lambda |
| **ChromaDB** | Downloads from S3 | Not needed (or uses EFS) |
| **API Keys** | Pulumi secrets (local) | Environment variables |
| **Monitoring** | Local logs | CloudWatch Logs + Metrics |
| **Error Handling** | Manual retry | Automatic retry + DLQ |
| **Cost** | Local compute | AWS Lambda pricing |
| **Scalability** | Single machine | Auto-scaling |
| **Timeout** | No limit | 15 minutes max |

---

## Migration Checklist

### For Each Script to Productionize:

- [ ] Create Lambda function infrastructure (`infra.py`)
- [ ] Create Lambda handler (`handler/index.py`)
- [ ] Remove ChromaDB download logic (if not needed)
- [ ] Add batch processing (to stay within timeout)
- [ ] Add error handling and retry logic
- [ ] Add CloudWatch metrics
- [ ] Set up EventBridge schedule
- [ ] Configure IAM roles and policies
- [ ] Add environment variables for API keys
- [ ] Test in dev stack
- [ ] Deploy to production
- [ ] Monitor first few runs
- [ ] Update documentation

---

## Cost Analysis

### Estimated Monthly Costs

**`validate_pending_labels_lambda`** (Weekly):
- Executions: ~4 per month
- Duration: ~10 minutes per execution (assuming 100 receipts with PENDING labels)
- Memory: 3008 MB
- Cost: ~$X per month

**`validate_metadata_lambda`** (Weekly):
- Executions: ~4 per month
- Duration: ~5 minutes per execution (assuming 400 receipts)
- Memory: 2048 MB
- Cost: ~$Y per month

**`reverify_labels_lambda`** (Quarterly):
- Executions: ~1 per quarter
- Duration: ~15 minutes per execution (assuming 400 receipts)
- Memory: 3008 MB
- Cost: ~$Z per quarter

**Total**: ~$X + $Y + $Z per month

---

## Monitoring and Alerts

### CloudWatch Metrics to Track

1. **Execution Count**: Number of Lambda invocations
2. **Duration**: Lambda execution time
3. **Error Rate**: Percentage of failed executions
4. **Labels Validated**: Number of labels processed
5. **Labels Updated**: Number of labels updated to VALID/INVALID

### CloudWatch Alarms

1. **High Error Rate**: Alert if error rate > 10%
2. **Long Duration**: Alert if duration > 80% of timeout
3. **No Executions**: Alert if no executions in expected time window

---

## Summary

### Productionize (High Priority)
1. ✅ **`validate_pending_labels_lambda`** - Weekly schedule
2. ⚠️ **`validate_metadata_lambda`** - Weekly schedule (optional)

### Productionize (Low Priority)
3. ⚠️ **`reverify_labels_lambda`** - Quarterly schedule (optional, can stay as dev script)

### Keep as Dev Scripts
- ✅ **`dev.process_all_receipts_cove.py`** - One-time migrations, ad-hoc runs, testing

### Already Handled
- ✅ **Upload Lambda** - Processes new receipts with CoVe automatically

---

## Next Steps

1. **Create `validate_pending_labels_lambda`** (Phase 1)
2. **Test in dev stack**
3. **Deploy to production**
4. **Monitor and adjust**
5. **Create `validate_metadata_lambda`** (Phase 2, optional)
6. **Document run results and metrics**

