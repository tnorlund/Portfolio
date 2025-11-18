# Upload Integration: Self-Correcting Validation

## Overview

This document shows how to integrate the self-correcting validation system into the upload process, so that receipts uploaded individually can have their PENDING labels validated immediately or in batch.

---

## Current Upload Flow

### Existing Process

```
1. Upload Image
   └── OCR Processing
       └── Merchant Validation + Embeddings
           └── LangGraph Label Creation (optional)
               └── Labels saved with validation_status="PENDING"
```

### Current Label Creation

**File**: `infra/upload_images/container_ocr/handler/handler.py`

```python
# During upload, labels are created via LangGraph
result = await analyze_receipt_simple(
    client=dynamo,
    image_id=image_id,
    receipt_id=receipt_id,
    ollama_api_key=ollama_api_key,
    save_labels=True,  # Saves labels immediately
    ...
)

# Labels are saved with validation_status="PENDING" by default
# (set in receipt_label/langchain/services/label_mapping.py)
```

---

## Integration Options

### Option A: Immediate Validation (Recommended for Real-Time)

**Trigger self-correcting validation immediately after labels are created during upload.**

**Pros:**
- ✅ Labels validated immediately
- ✅ No separate batch job needed
- ✅ User sees validated results right away

**Cons:**
- ⚠️ Slower upload process (adds validation time)
- ⚠️ More expensive (runs validation for every upload)
- ⚠️ May hit rate limits if many uploads happen simultaneously

**Structure:**

```python
# In infra/upload_images/container_ocr/handler/handler.py

async def _run_validation_async(
    image_id: str,
    receipt_id: int,
    receipt_lines: Optional[list],
    receipt_words: Optional[list],
    receipt_metadata: Optional[Any],
    ollama_api_key: Optional[str],
    langsmith_api_key: Optional[str],
) -> None:
    """Run LangGraph validation and then self-correcting validation."""

    # ... existing LangGraph label creation ...
    result = await analyze_receipt_simple(
        client=dynamo,
        image_id=image_id,
        receipt_id=receipt_id,
        ollama_api_key=ollama_api_key,
        save_labels=True,  # Save labels with PENDING status
        ...
    )

    _log(f"✅ LangGraph completed, labels saved with PENDING status")

    # NEW: Run self-correcting validation immediately
    if os.environ.get("ENABLE_SELF_CORRECTING_VALIDATION", "false").lower() == "true":
        _log("Starting self-correcting validation...")

        from receipt_label.langchain.self_correcting_validation import (
            validate_receipt_self_correcting
        )

        validation_result = await validate_receipt_self_correcting(
            image_id=image_id,
            receipt_id=receipt_id,
            dynamo_client=dynamo,
            chroma_client=await _init_chromadb_client(),  # Download snapshot
            llm=ChatOllama(...),
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
            receipt_metadata=receipt_metadata,
        )

        _log(f"✅ Self-correcting validation completed: "
             f"{validation_result['labels_validated']} validated, "
             f"{validation_result['labels_invalidated']} invalidated")
```

**Configuration:**

```python
# Environment variable to enable/disable
ENABLE_SELF_CORRECTING_VALIDATION=true  # Enable immediate validation
ENABLE_SELF_CORRECTING_VALIDATION=false # Disable (use batch validation)
```

---

### Option B: Async Trigger (Recommended for High Volume)

**Trigger self-correcting validation asynchronously via SQS/Step Function after upload completes.**

**Pros:**
- ✅ Upload process stays fast
- ✅ Can handle high volume uploads
- ✅ Can batch multiple receipts together
- ✅ Better rate limit management

**Cons:**
- ⚠️ Labels remain PENDING until batch runs
- ⚠️ Requires separate infrastructure (SQS queue or Step Function)

**Structure:**

#### B1: SQS Queue Trigger

```python
# In infra/upload_images/container_ocr/handler/handler.py

async def _run_validation_async(...):
    """Run LangGraph validation and queue for self-correcting validation."""

    # ... existing LangGraph label creation ...
    result = await analyze_receipt_simple(...)
    _log(f"✅ LangGraph completed, labels saved with PENDING status")

    # NEW: Send message to SQS queue for self-correcting validation
    sqs_client = boto3.client("sqs")
    queue_url = os.environ.get("SELF_CORRECTING_VALIDATION_QUEUE_URL")

    if queue_url:
        sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({
                "image_id": image_id,
                "receipt_id": receipt_id,
                "triggered_by": "upload",
                "timestamp": datetime.utcnow().isoformat(),
            })
        )
        _log(f"✅ Queued receipt for self-correcting validation")
```

**SQS Consumer Lambda:**

```python
# infra/self_correcting_validation/lambdas/process_receipt_from_queue.py

async def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process receipt from SQS queue for self-correcting validation."""

    for record in event["Records"]:
        body = json.loads(record["body"])
        image_id = body["image_id"]
        receipt_id = body["receipt_id"]

        # Run self-correcting validation
        result = await validate_receipt_self_correcting(
            image_id=image_id,
            receipt_id=receipt_id,
            ...
        )

        logger.info(f"Validation completed: {result}")
```

#### B2: Step Function Trigger

```python
# In infra/upload_images/container_ocr/handler/handler.py

async def _run_validation_async(...):
    """Run LangGraph validation and trigger Step Function for self-correcting validation."""

    # ... existing LangGraph label creation ...
    result = await analyze_receipt_simple(...)
    _log(f"✅ LangGraph completed, labels saved with PENDING status")

    # NEW: Trigger Step Function execution
    sfn_client = boto3.client("stepfunctions")
    state_machine_arn = os.environ.get("SELF_CORRECTING_VALIDATION_SF_ARN")

    if state_machine_arn:
        sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=f"upload-{image_id}-{receipt_id}-{int(time.time())}",
            input=json.dumps({
                "image_id": image_id,
                "receipt_id": receipt_id,
                "triggered_by": "upload",
            })
        )
        _log(f"✅ Triggered Step Function for self-correcting validation")
```

**Step Function Definition:**

```json
{
  "Comment": "Self-correcting validation triggered from upload",
  "StartAt": "ValidateReceipt",
  "States": {
    "ValidateReceipt": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:function:validate-receipt-self-correcting",
      "End": true
    }
  }
}
```

---

### Option C: Batch Validation (Recommended for Cost Optimization)

**Use existing batch Step Function to process all PENDING receipts periodically.**

**Pros:**
- ✅ Most cost-effective (batches multiple receipts)
- ✅ Better rate limit management
- ✅ Can run during off-peak hours
- ✅ No changes to upload process needed

**Cons:**
- ⚠️ Labels remain PENDING until batch runs
- ⚠️ Delayed validation (not real-time)

**Structure:**

**No changes needed to upload process!** Just run the batch Step Function:

```bash
# Run batch validation Step Function
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:...:stateMachine:self-correcting-validation-sf \
  --input '{}'
```

**Or schedule it:**

```python
# CloudWatch Events Rule (EventBridge)
{
  "ScheduleExpression": "rate(1 hour)",  # Run every hour
  "Target": {
    "Arn": "arn:aws:states:...:stateMachine:self-correcting-validation-sf",
    "RoleArn": "arn:aws:iam:...:role/StepFunctionExecutionRole"
  }
}
```

---

## Recommended Approach: Hybrid

**Use Option B (Async Trigger) for immediate validation, with Option C (Batch) as fallback.**

### Architecture

```
Upload Process
  └── LangGraph creates labels (PENDING)
      └── Send to SQS queue (async)
          └── SQS Consumer Lambda
              └── Self-Correcting Validation
                  └── Update labels (VALID/INVALID)

+ Batch Step Function (runs hourly)
  └── Processes any remaining PENDING labels
      └── Self-Correcting Validation
          └── Update labels (VALID/INVALID)
```

### Benefits

1. **Immediate validation** for new uploads (via SQS)
2. **Batch processing** for any missed receipts (via Step Function)
3. **Cost optimization** (SQS is cheap, batch is efficient)
4. **Rate limit management** (SQS can throttle, batch can limit concurrency)

---

## Implementation Steps

### Step 1: Create Self-Correcting Validation Lambda

**File**: `infra/self_correcting_validation/lambdas/validate_receipt_handler.py`

```python
async def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Validate ALL PENDING/NEEDS_REVIEW labels for a single receipt.

    Can be triggered by:
    - SQS message (from upload)
    - Step Function (batch processing)
    - Direct invocation (testing)
    """
    # Extract receipt info from event
    image_id = event.get("image_id") or event.get("image_id")
    receipt_id = event.get("receipt_id") or int(event.get("receipt_id"))

    # Run self-correcting validation (Steps 1-5)
    result = await validate_receipt_self_correcting(
        image_id=image_id,
        receipt_id=receipt_id,
        ...
    )

    return result
```

### Step 2: Add SQS Queue Integration

**File**: `infra/upload_images/container_ocr/handler/handler.py`

```python
# After LangGraph label creation
if os.environ.get("SELF_CORRECTING_VALIDATION_QUEUE_URL"):
    sqs_client.send_message(
        QueueUrl=os.environ["SELF_CORRECTING_VALIDATION_QUEUE_URL"],
        MessageBody=json.dumps({
            "image_id": image_id,
            "receipt_id": receipt_id,
        })
    )
```

### Step 3: Create SQS Consumer Lambda

**File**: `infra/self_correcting_validation/lambdas/sqs_consumer.py`

```python
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process SQS messages and trigger validation."""

    for record in event["Records"]:
        body = json.loads(record["body"])

        # Invoke validation Lambda directly or via Step Function
        lambda_client.invoke(
            FunctionName="validate-receipt-self-correcting",
            InvocationType="Event",  # Async
            Payload=json.dumps(body)
        )
```

### Step 4: Create Batch Step Function

**File**: `infra/self_correcting_validation/infrastructure.py`

```python
# Step Function that:
# 1. Lists receipts with PENDING labels
# 2. Processes them in parallel (MaxConcurrency: 3)
# 3. Runs self-correcting validation for each
```

### Step 5: Schedule Batch Step Function

**File**: `infra/self_correcting_validation/infrastructure.py`

```python
# CloudWatch Events Rule to run hourly
rule = aws.cloudwatch.EventRule(
    "self-correcting-validation-schedule",
    schedule_expression="rate(1 hour)",
)

rule.add_targets([
    aws.cloudwatch.EventTarget(
        arn=step_function.arn,
        role_arn=execution_role.arn,
    )
])
```

---

## Configuration

### Environment Variables

```bash
# Upload Lambda
SELF_CORRECTING_VALIDATION_QUEUE_URL=sqs://...  # Optional: Enable async validation

# Validation Lambda
DYNAMODB_TABLE_NAME=...
CHROMADB_BUCKET=...
OLLAMA_API_KEY=...
LANGCHAIN_API_KEY=...
```

### Feature Flags

```python
# Enable/disable self-correcting validation
ENABLE_SELF_CORRECTING_VALIDATION=true  # Enable
ENABLE_SELF_CORRECTING_VALIDATION=false # Disable (use existing validation)
```

---

## Testing

### Test Immediate Validation

```python
# Test upload with immediate validation
event = {
    "Records": [{
        "body": json.dumps({
            "job_id": "test-job-id",
            "image_id": "test-image-id"
        })
    }]
}

# Should:
# 1. Create labels with PENDING status
# 2. Trigger self-correcting validation
# 3. Update labels to VALID/INVALID
```

### Test Batch Validation

```bash
# Trigger batch Step Function
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:...:stateMachine:self-correcting-validation-sf \
  --input '{}'

# Check results
aws stepfunctions describe-execution \
  --execution-arn <execution-arn>
```

---

## Monitoring

### Metrics to Track

1. **Upload → Validation Latency**: Time from upload to validation complete
2. **Validation Success Rate**: % of receipts successfully validated
3. **Label Accuracy**: % of labels marked VALID vs INVALID
4. **Learning Progress**: Track accuracy improvement over time

### CloudWatch Alarms

- **High PENDING Label Count**: Alert if too many labels remain PENDING
- **Validation Failure Rate**: Alert if validation fails frequently
- **Queue Depth**: Alert if SQS queue is backing up

---

## Migration Path

### Phase 1: Deploy Infrastructure (No Changes to Upload)

1. Deploy self-correcting validation Lambda
2. Deploy batch Step Function
3. Test with existing PENDING labels

### Phase 2: Enable Async Validation (Optional)

1. Add SQS queue
2. Update upload Lambda to send messages
3. Deploy SQS consumer Lambda
4. Monitor performance

### Phase 3: Optimize

1. Tune batch size and concurrency
2. Optimize ChromaDB queries
3. Fine-tune CoVe parameters
4. Monitor and adjust

---

## Summary

**Yes, you can use this process to validate all labels on a PENDING receipt!**

**Recommended Structure:**
- **Upload**: Creates labels with `validation_status="PENDING"`
- **Async Trigger**: Sends receipt to SQS queue (optional, for immediate validation)
- **Batch Step Function**: Processes all PENDING receipts periodically (required, for cost optimization)
- **Self-Correcting Validation**: Runs Steps 1-5 for all labels in the receipt

**Benefits:**
- ✅ Validates all labels together (enables contradiction detection)
- ✅ Learns from past mistakes (Step 1)
- ✅ Detects logical inconsistencies (Step 2)
- ✅ Uses LLM verification (Step 3)
- ✅ Resolves conflicts intelligently (Step 4)
- ✅ Stores invalid examples for future learning (Step 5)

