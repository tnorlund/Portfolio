# Actual Receipt Processing Flow Analysis

## Current Flow (As-Is)

### Step 1: Image Upload via Swift/Mac Client

```
┌─────────────────────────────────────────────────────────────┐
│ Mac Client (Swift OCR Script)                               │
│                                                              │
│ 1. User selects images                                      │
│ 2. Swift script runs Apple Vision OCR                       │
│ 3. Generates JSON files locally                             │
│ 4. Uploads JSON to S3: s3://raw-receipts/ocr-results/...    │
│ 5. Sends message to SQS: ocr_results_queue                  │
└─────────────────────────────────────────────────────────────┘
```

### Step 2: Process OCR Results Lambda (Triggered by SQS)

```
┌─────────────────────────────────────────────────────────────┐
│ process_ocr_results Lambda                                  │
│ (Triggered by: ocr_results_queue)                           │
│                                                              │
│ 1. Download OCR JSON from S3                                │
│ 2. Parse JSON into Line/Word/Letter objects                 │
│ 3. Classify image type:                                     │
│    - NATIVE (single receipt photo)                          │
│    - SCAN (multiple receipts scanned)                       │
│    - PHOTO (receipt photo needing refinement)               │
│                                                              │
│ 4. For NATIVE receipts:                                     │
│    - Create LINE/WORD/LETTER records in DynamoDB            │
│    - Create COMPACTION_RUN (state: PENDING)                 │
│    - Receipt ID = 1                                         │
│                                                              │
│ 5. For SCAN/PHOTO:                                          │
│    - Create refinement jobs                                 │
│    - Queue back to ocr_queue for re-processing              │
└─────────────────────────────────────────────────────────────┘
```

**Key Point:** The `process_ocr_results` Lambda creates LINE/WORD/LETTER records and COMPACTION_RUN, but **does NOT trigger merchant validation automatically**. Merchant validation is currently **triggered manually** via Step Function.

### Step 3: Merchant Validation (MANUAL TRIGGER - Current Gap)

```
┌─────────────────────────────────────────────────────────────┐
│ validate-merchant-dev-merchant-validation-sm Step Function  │
│ (Triggered: MANUALLY via AWS Console or CLI)                │
│                                                              │
│ 1. ListReceipts Lambda                                      │
│    - Queries DynamoDB for receipts without ReceiptMetadata  │
│                                                              │
│ 2. ForEachReceipt (Map State)                               │
│    - Parallel processing of receipts                        │
│                                                              │
│ 3. ValidateReceipt Lambda (zip-based, uses HTTP Chroma)     │
│    - Queries Fargate ECS Chroma service (HTTP)              │
│    - Queries Google Places API                              │
│    - Creates ReceiptMetadata with merchant_name             │
│                                                              │
│ 4. ConsolidateMetadata Lambda                               │
│    - Merges validated merchant data                         │
└─────────────────────────────────────────────────────────────┘
```

**Problem:** This step is **manual** and requires human intervention!

### Step 4: Embedding Process (Also Manual)

After merchant validation completes, embeddings must also be triggered manually (another gap).

---

## Proposed Flow (To-Be)

### Step 1-2: Same (Image Upload → OCR Processing)

No changes to the Swift client or `process_ocr_results` Lambda.

### Step 3: Automatic Merchant Validation Trigger

**Option A: Trigger from process_ocr_results Lambda**

Add to `process_ocr_results.py` after creating LINE/WORD/LETTER records:

```python
# After creating COMPACTION_RUN in process_native()
if image_type == ImageType.NATIVE:
    process_native(...)  # Creates LINE/WORD/LETTER, COMPACTION_RUN
    
    # 🆕 NEW: Trigger merchant validation Lambda
    lambda_client = boto3.client('lambda')
    lambda_client.invoke(
        FunctionName=os.environ['MERCHANT_VALIDATION_FUNCTION_NAME'],
        InvocationType='Event',  # Async
        Payload=json.dumps({
            'image_id': ocr_job.image_id,
            'receipt_id': 1
        })
    )
```

**Option B: Trigger from DynamoDB Stream**

Add logic to the existing `stream_processor` Lambda to detect new receipts without metadata:

```python
# In stream_processor.py
if event_name == "INSERT" and entity_type == "RECEIPT_LINE":
    # Check if this is the first line of a new receipt
    if line_number == 1:
        # Check if ReceiptMetadata exists
        metadata = dynamo.get_receipt_metadata(image_id, receipt_id)
        if not metadata:
            # Trigger merchant validation
            lambda_client.invoke(
                FunctionName=os.environ['MERCHANT_VALIDATION_FUNCTION_NAME'],
                InvocationType='Event',
                Payload=json.dumps({
                    'image_id': image_id,
                    'receipt_id': receipt_id
                })
            )
```

**Option C: EventBridge Rule**

Create an EventBridge rule that triggers on COMPACTION_RUN creation:

```python
# EventBridge rule
{
    "source": ["aws.dynamodb"],
    "detail-type": ["DynamoDB Stream Record"],
    "detail": {
        "eventName": ["INSERT"],
        "dynamodb": {
            "NewImage": {
                "SK": {
                    "S": [{"prefix": "COMPACTION_RUN#"}]
                }
            }
        }
    }
}
```

### Step 4: Merchant Validation Container Lambda (NEW)

```
┌─────────────────────────────────────────────────────────────┐
│ merchant-validation-dev-function Lambda                     │
│ (Triggered: Automatically from Step 3)                      │
│ (Container + EFS)                                            │
│                                                              │
│ 1. Query ChromaDB (EFS) for similar receipts                │
│    - Direct file system access (no HTTP)                    │
│    - ~100ms vs 200ms over HTTP                              │
│                                                              │
│ 2. Query Google Places API                                  │
│    - Validate merchant location                             │
│                                                              │
│ 3. Create ReceiptMetadata with merchant_name                │
│    - Write to DynamoDB                                      │
│                                                              │
│ 4. 🆕 Export NDJSON to S3                                   │
│    - s3://chromadb-bucket/receipts/{image_id}/lines.ndjson  │
│    - s3://chromadb-bucket/receipts/{image_id}/words.ndjson  │
│                                                              │
│ 5. 🆕 Update COMPACTION_RUN                                 │
│    - lines_state: PENDING → PROCESSING                      │
│    - words_state: PENDING → PROCESSING                      │
│                                                              │
│ 6. 🆕 Queue embedding job to embed-ndjson-queue             │
│    - Message includes merchant_name for context             │
└─────────────────────────────────────────────────────────────┘
```

### Step 5: Embedding Lambda (Needs to be Created)

```
┌─────────────────────────────────────────────────────────────┐
│ embed-from-ndjson Lambda (NEW - NEEDS IMPLEMENTATION)       │
│ (Triggered: embed-ndjson-queue SQS)                         │
│                                                              │
│ 1. Receive message from embed-ndjson-queue                  │
│ 2. Download NDJSON files from S3                            │
│ 3. Load ReceiptMetadata (merchant_name)                     │
│ 4. Create embeddings with merchant context                  │
│    - OpenAI text-embedding-3-small                          │
│    - Include merchant_name in metadata                      │
│ 5. Write ChromaDB deltas to S3                              │
│    - s3://chromadb-bucket/deltas/{run_id}/lines/...         │
│    - s3://chromadb-bucket/deltas/{run_id}/words/...         │
│ 6. Update COMPACTION_RUN                                    │
│    - lines_state: PROCESSING → COMPLETED                    │
│    - words_state: PROCESSING → COMPLETED                    │
│    - Set lines_finished_at, words_finished_at timestamps    │
└─────────────────────────────────────────────────────────────┘
```

### Step 6: Stream Processor Detects Completion (Existing)

```
┌─────────────────────────────────────────────────────────────┐
│ chromadb-dev-stream-processor Lambda (ALREADY EXISTS)       │
│ (Triggered: DynamoDB Stream on COMPACTION_RUN MODIFY)       │
│                                                              │
│ 1. Detect COMPACTION_RUN MODIFY event                       │
│ 2. Check: lines_state == COMPLETED && words_state == COMPLETED │
│ 3. Queue messages to:                                       │
│    - chromadb-dev-lines-queue                               │
│    - chromadb-dev-words-queue                               │
└─────────────────────────────────────────────────────────────┘
```

### Step 7: Compaction Lambda (Existing)

```
┌─────────────────────────────────────────────────────────────┐
│ chromadb-dev-enhanced-compaction Lambda (ALREADY EXISTS)    │
│ (Triggered: chromadb-dev-lines-queue, chromadb-dev-words-queue) │
│ (Container + EFS)                                            │
│                                                              │
│ 1. Read ChromaDB deltas from S3                             │
│ 2. Mount EFS at /mnt/chroma                                 │
│ 3. Merge deltas into persistent ChromaDB                    │
│ 4. Update metadata with merchant_name                       │
│ 5. Create S3 snapshot for backup                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Summary: What Needs to Happen

### ✅ Already Implemented

1. **Merchant validation container Lambda** - Created with NDJSON trigger
2. **embed-ndjson-queue** - SQS queue for embedding jobs
3. **Stream processor completion detection** - Detects COMPACTION_RUN completion
4. **Compaction Lambda** - Merges ChromaDB deltas

### ❌ Still Needed

1. **Automatic trigger for merchant validation**
   - Choose Option A, B, or C above
   - Recommended: **Option A** (trigger from `process_ocr_results`)
   - Simplest and most direct

2. **embed-from-ndjson Lambda**
   - Reads NDJSON from S3
   - Creates embeddings with merchant context
   - Writes ChromaDB deltas
   - Updates COMPACTION_RUN to COMPLETED

3. **Event source mapping for embed-from-ndjson Lambda**
   - Connect `embed-ndjson-queue` to the Lambda

---

## Recommended Implementation Order

### Phase 1: Add Automatic Trigger (Easiest)

Update `infra/upload_images/process_ocr_results.py`:

```python
# Add to the top
MERCHANT_VALIDATION_FUNCTION_NAME = os.environ.get('MERCHANT_VALIDATION_FUNCTION_NAME')

# Add after process_native() creates the receipt
if image_type == ImageType.NATIVE:
    process_native(...)
    
    # Trigger merchant validation automatically
    if MERCHANT_VALIDATION_FUNCTION_NAME:
        try:
            lambda_client = boto3.client('lambda')
            lambda_client.invoke(
                FunctionName=MERCHANT_VALIDATION_FUNCTION_NAME,
                InvocationType='Event',  # Async
                Payload=json.dumps({
                    'image_id': ocr_job.image_id,
                    'receipt_id': 1
                })
            )
            logger.info(f"Triggered merchant validation for {ocr_job.image_id}")
        except Exception as e:
            logger.error(f"Failed to trigger merchant validation: {e}")
            # Don't fail the main processing
```

Update `infra/upload_images/infra.py` to pass the function name as an environment variable.

### Phase 2: Create embed-from-ndjson Lambda

This is the missing piece that processes the NDJSON files and creates embeddings.

### Phase 3: Test End-to-End

Upload an image and verify the complete flow works automatically.

---

## Current State

- ✅ Swift OCR upload works
- ✅ process_ocr_results creates LINE/WORD/LETTER records
- ❌ Merchant validation is **manual** (Step Function)
- ❌ Embedding is **manual**
- ✅ Stream processor detects completion
- ✅ Compaction Lambda merges to ChromaDB

## Target State

- ✅ Swift OCR upload works
- ✅ process_ocr_results creates LINE/WORD/LETTER records
- ✅ Merchant validation is **automatic** (Lambda invocation)
- ✅ Embedding is **automatic** (SQS trigger)
- ✅ Stream processor detects completion
- ✅ Compaction Lambda merges to ChromaDB

**Result:** Fully automated from image upload to ChromaDB!

