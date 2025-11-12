# Validate Pending Labels: S3-Based Payload Architecture

## Overview

This document describes the S3-based payload architecture for the `validate_pending_labels` Step Function, using NDJSON files and a manifest to avoid Step Functions' 256KB payload limit.

## ChromaDB Usage

**ChromaDB is always used** for this workflow. The validation uses a two-tier approach:

1. **CoVe (LLM-based)**: Primary validation method - always used
   - Validates labels by having the LLM verify its own outputs
   - High accuracy, slower, costs API calls
   - Marks labels as VALID if verification passes

2. **ChromaDB (Similarity-based)**: Secondary validation - always used
   - Validates labels by comparing against similar validated words
   - Fast, free, uses historical data
   - Can validate PENDING labels that CoVe couldn't verify
   - **Downloads "words" collection snapshot from S3** (used for label validation)

**Implementation**: Each Lambda downloads the ChromaDB "words" collection snapshot from S3 using `download_snapshot_atomic()`, then initializes ChromaDBClient for similarity search validation.

## Architecture Pattern

Following the existing pattern from `embedding_step_functions`:
- **S3 offloading**: Use S3 for manifest file only (receipt data fetched from DynamoDB in parallel)
- **Manifest file**: Single S3 file containing receipt identifiers (image_id, receipt_id)
- **Index-based lookup**: Map state passes index, Lambda uses index to look up receipt from manifest

---

## Data Flow

```
1. ListPendingLabels Lambda
   ├─→ Query DynamoDB for PENDING labels
   ├─→ Group by receipt (image_id, receipt_id)
   ├─→ Create manifest file: manifest.json
   │   └─→ Contains array of {image_id, receipt_id, pending_label_count}
   ├─→ Upload manifest to S3: pending_labels/{execution_id}/manifest.json
   └─→ Create receipt_indices array: [0, 1, 2, ..., total_receipts-1]

2. Step Function Map State
   ├─→ Receives: {manifest_s3_key, manifest_s3_bucket, execution_id, total_receipts, receipt_indices}
   ├─→ Iterates over receipt_indices: [0, 1, 2, ..., total_receipts-1]
   └─→ Each parallel execution receives: {index, manifest_s3_key, manifest_s3_bucket, execution_id}

3. ValidateReceipt Lambda (per receipt)
   ├─→ Downloads manifest from S3
   ├─→ Uses index to look up receipt info: manifest["receipts"][index]
   ├─→ Fetches receipt data from DynamoDB:
   │   ├─→ list_receipt_lines_from_receipt(image_id, receipt_id)
   │   ├─→ list_receipt_words_from_receipt(image_id, receipt_id)
   │   ├─→ get_receipt_metadata(image_id, receipt_id)
   │   └─→ list_receipt_word_labels_with_status(image_id, receipt_id, "PENDING")
   ├─→ Downloads ChromaDB snapshot (words collection)
   ├─→ Runs LangGraph + CoVe + ChromaDB validation
   ├─→ Updates labels in DynamoDB
   └─→ Returns: {success, labels_validated, labels_updated, labels_added}
```

---

## S3 Structure

```
s3://{bucket}/pending_labels/{execution_id}/
└── manifest.json                    # Manifest with receipt identifiers only
```

**Note**: Receipt data (lines, words, metadata) is fetched directly from DynamoDB in each parallel Lambda execution. Only the manifest is stored in S3.

### Manifest Format

```json
{
  "execution_id": "abc123-def456-...",
  "created_at": "2024-01-15T10:30:00Z",
  "total_receipts": 100,
  "receipts": [
    {
      "index": 0,
      "image_id": "abc123",
      "receipt_id": 1,
      "pending_label_count": 5
    },
    {
      "index": 1,
      "image_id": "def456",
      "receipt_id": 2,
      "pending_label_count": 3
    }
  ]
}
```

**Note**: The manifest only contains receipt identifiers (image_id, receipt_id). Receipt data (lines, words, metadata) is fetched from DynamoDB in each parallel Lambda execution.

---

## Lambda Functions

### 1. `list_pending_labels` Lambda

**Purpose**: List all receipts with PENDING labels, create NDJSON files, upload to S3, create manifest

**Input**:
```json
{
  "limit": null  // Optional, null = all receipts
}
```

**Processing**:
1. Query DynamoDB for all PENDING labels
2. Group by receipt (image_id, receipt_id)
3. Create manifest file with receipt identifiers only (no data fetching):
   - For each receipt: `{index, image_id, receipt_id, pending_label_count}`
4. Upload manifest to S3: `pending_labels/{execution_id}/manifest.json`
5. Create receipt_indices array: `[0, 1, 2, ..., total_receipts-1]`

**Output**:
```json
{
  "manifest_s3_key": "pending_labels/abc123-def456-.../manifest.json",
  "manifest_s3_bucket": "bucket-name",
  "execution_id": "abc123-def456-...",
  "total_receipts": 100,
  "total_pending_labels": 450,
  "receipt_indices": [0, 1, 2, ..., 99]  // Array for Map state
}
```

**Implementation**:
```python
def list_handler(event, context):
    import json
    import boto3
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.entities import ReceiptWordLabel

    dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    s3_client = boto3.client("s3")
    bucket = os.environ["S3_BUCKET"]

    # Get execution ID from context (or generate UUID)
    execution_id = context.aws_request_id if context else str(uuid.uuid4())

    # Query all PENDING labels
    pending_labels = list_all_pending_labels(dynamo, limit=event.get("limit"))

    # Group by receipt (image_id, receipt_id)
    receipts_dict = group_labels_by_receipt(pending_labels)

    # Create manifest with receipt identifiers only (no data fetching)
    manifest_receipts = []
    for index, ((image_id, receipt_id), labels) in enumerate(receipts_dict.items()):
        manifest_receipts.append({
            "index": index,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "pending_label_count": len(labels),
        })

    # Create and upload manifest
    manifest = {
        "execution_id": execution_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "total_receipts": len(manifest_receipts),
        "receipts": manifest_receipts,
    }

    manifest_key = f"pending_labels/{execution_id}/manifest.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(manifest).encode("utf-8"),
        ContentType="application/json",
    )

    # Create receipt_indices array for Map state
    receipt_indices = list(range(len(manifest_receipts)))  # [0, 1, 2, ..., N-1]

    return {
        "manifest_s3_key": manifest_key,
        "manifest_s3_bucket": bucket,
        "execution_id": execution_id,
        "total_receipts": len(manifest_receipts),
        "total_pending_labels": len(pending_labels),
        "receipt_indices": receipt_indices,  # Array for Map state: [0, 1, 2, ..., N-1]
    }
```

---

### 2. Step Function Map State

**Purpose**: Create array of indices for parallel processing

**Input** (from ListPendingLabels):
```json
{
  "manifest_s3_key": "pending_labels/abc123-def456-.../manifest.json",
  "manifest_s3_bucket": "bucket-name",
  "execution_id": "abc123-def456-...",
  "total_receipts": 100
}
```

**Map State Configuration**:
```json
{
  "Type": "Map",
  "ItemsPath": "$.receipt_indices",  // Array of indices [0, 1, 2, ..., 99]
  "MaxConcurrency": 10,
  "Iterator": {
    "StartAt": "ValidateReceipt",
    "States": {
      "ValidateReceipt": {
        "Type": "Task",
        "Resource": "${ValidateReceiptLambdaArn}",
        "Parameters": {
          "index.$": "$",
          "manifest_s3_key.$": "$.manifest_s3_key",
          "manifest_s3_bucket.$": "$.manifest_s3_bucket",
          "execution_id.$": "$.execution_id"
        },
        "End": true
      }
    }
  }
}
```

**Note**: The `receipt_indices` array is created by the `list_pending_labels` Lambda (using `list(range(len(manifest_receipts)))`) and returned in the output. The Map state uses this array directly via `ItemsPath: "$.list_result.receipt_indices"`.

---

### 3. `validate_receipt` Lambda

**Purpose**: Validate PENDING labels for a single receipt using LangGraph + CoVe

**Input** (from Map state):
```json
{
  "index": 0,
  "manifest_s3_key": "pending_labels/abc123-def456-.../manifest.json",
  "manifest_s3_bucket": "bucket-name",
  "execution_id": "abc123-def456-..."
}
```

**Processing**:
1. Download manifest from S3
2. Use `index` to look up receipt info: `manifest["receipts"][index]`
   - Gets: `{image_id, receipt_id, pending_label_count}`
3. Fetch receipt data from DynamoDB:
   - `list_receipt_lines_from_receipt(image_id, receipt_id)`
   - `list_receipt_words_from_receipt(image_id, receipt_id)`
   - `get_receipt_metadata(image_id, receipt_id)`
   - `list_receipt_word_labels_with_status(image_id, receipt_id, status="PENDING")`
4. Download ChromaDB snapshot (words collection) from S3
5. Run LangGraph + CoVe + ChromaDB validation
6. Compare and update labels in DynamoDB
7. Return results

**Output**:
```json
{
  "success": true,
  "index": 0,
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
    import json
    import boto3
    import asyncio
    import tempfile
    from receipt_dynamo import DynamoClient
    from receipt_label.langchain.currency_validation import analyze_receipt_simple

    async def run():
        s3_client = boto3.client("s3")
        dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

        index = event["index"]
        manifest_s3_key = event["manifest_s3_key"]
        manifest_s3_bucket = event["manifest_s3_bucket"]

        # Download manifest
        with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp_manifest:
            s3_client.download_file(manifest_s3_bucket, manifest_s3_key, tmp_manifest.name)
            with open(tmp_manifest.name, "r") as f:
                manifest = json.load(f)

        # Look up receipt info using index
        receipt_info = manifest["receipts"][index]
        image_id = receipt_info["image_id"]
        receipt_id = receipt_info["receipt_id"]

        # Fetch receipt data from DynamoDB (moved from ListPendingLabels)
        from receipt_dynamo.constants import ValidationStatus

        lines = dynamo.list_receipt_lines_from_receipt(image_id, receipt_id)
        words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
        metadata = dynamo.get_receipt_metadata(image_id, receipt_id)
        pending_labels = dynamo.list_receipt_word_labels_with_status(
            image_id=image_id,
            receipt_id=receipt_id,
            status=ValidationStatus.PENDING
        )

        # Download ChromaDB snapshot from S3 ("words" collection for label validation)
        chroma_client = None
        chromadb_temp_dir = None
        chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
        if chromadb_bucket:
            try:
                import tempfile
                from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic
                from receipt_label.vector_store.client.chromadb_client import ChromaDBClient

                # Create temporary directory for ChromaDB snapshots
                chromadb_temp_dir = tempfile.mkdtemp(prefix="chromadb_")

                # Download "words" collection snapshot (used for label validation)
                words_download = download_snapshot_atomic(
                    bucket=chromadb_bucket,
                    collection="words",
                    local_path=os.path.join(chromadb_temp_dir, "words"),
                    verify_integrity=True,
                )

                if words_download.get("status") == "downloaded":
                    # Initialize ChromaDB client with words collection
                    chroma_client = ChromaDBClient(
                        persist_directory=os.path.join(chromadb_temp_dir, "words"),
                        mode="read",
                    )
                    logger.info(f"ChromaDB client initialized from S3 snapshot (words collection)")
                else:
                    logger.warning(f"Failed to download ChromaDB words snapshot: {words_download.get('error')}")

            except Exception as e:
                logger.warning(f"Failed to initialize ChromaDB client: {e}, continuing without ChromaDB validation")
        else:
            logger.warning("CHROMADB_BUCKET not set, skipping ChromaDB validation")

        # Run LangGraph + CoVe (with ChromaDB validation if available)
        result = await analyze_receipt_simple(
            client=dynamo,
            image_id=image_id,
            receipt_id=receipt_id,
            ollama_api_key=os.environ["OLLAMA_API_KEY"],
            langsmith_api_key=os.environ.get("LANGSMITH_API_KEY"),
            save_labels=False,  # We'll update manually
            dry_run=False,
            receipt_lines=lines,  # Pass pre-fetched data
            receipt_words=words,
            receipt_metadata=metadata,
            chroma_client=chroma_client,  # ChromaDB client for similarity search validation
        )

        # Cleanup ChromaDB temp directory
        if chromadb_temp_dir and os.path.exists(chromadb_temp_dir):
            try:
                import shutil
                shutil.rmtree(chromadb_temp_dir, ignore_errors=True)
            except Exception:
                pass

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
            "index": index,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "labels_validated": len(pending_labels),
            "labels_updated": len(comparison["labels_to_update"]),
            "labels_added": len(comparison["labels_to_add"]),
        }

    return asyncio.run(run())
```

---

## Step Function Definition

```json
{
  "Comment": "Validate PENDING labels using LangGraph + CoVe with S3 offloading",
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
          "Variable": "$.list_result.total_receipts",
          "NumericGreaterThan": 0,
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
      "ItemsPath": "$.list_result.receipt_indices",
      "MaxConcurrency": 10,
      "Parameters": {
        "index.$": "$$.Map.Item.Value",
        "manifest_s3_key.$": "$.list_result.manifest_s3_key",
        "manifest_s3_bucket.$": "$.list_result.manifest_s3_bucket",
        "execution_id.$": "$.list_result.execution_id"
      },
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

## Payload Size Analysis

### With S3 Offloading (Manifest Only)

**ListPendingLabels output**:
- manifest_s3_key: ~100 bytes
- manifest_s3_bucket: ~50 bytes
- execution_id: ~50 bytes
- total_receipts: 4 bytes
- receipt_indices: ~400 bytes (100 integers)
- **Total**: ~600 bytes ✅

**Map state input** (per receipt):
- index: 4 bytes
- manifest_s3_key: ~100 bytes
- manifest_s3_bucket: ~50 bytes
- execution_id: ~50 bytes
- **Total**: ~200 bytes ✅

**Manifest file** (100 receipts):
- Each receipt entry: ~100 bytes (image_id, receipt_id, pending_label_count)
- 100 receipts: ~10KB
- **Total**: ~12KB (well within S3 limits)

**DynamoDB queries** (per receipt, in parallel):
- Each Lambda fetches its own receipt data from DynamoDB
- No payload size concerns (queries are distributed)

---

## Benefits

1. ✅ **No payload size limits**: Only manifest stored in S3 (very small)
2. ✅ **Scalable**: Can handle thousands of receipts
3. ✅ **Efficient**: DynamoDB queries distributed across parallel executions
4. ✅ **Faster ListPendingLabels**: Only queries labels, no heavy data fetching
5. ✅ **Simpler architecture**: No need to upload/download receipt NDJSON files
6. ✅ **Lower S3 costs**: Only manifest file stored in S3
7. ✅ **Better parallelization**: Each Lambda fetches its own receipt data independently

---

## Cleanup Strategy

**Option 1: TTL-based cleanup**
- Set S3 lifecycle policy to delete files older than 7 days
- Path: `pending_labels/*/`

**Option 2: Manual cleanup**
- Delete S3 files after successful execution
- Add cleanup step in `report_results` Lambda

**Option 3: Keep for debugging**
- Keep S3 files for 30 days
- Useful for troubleshooting failed validations

**Recommendation**: Option 1 (TTL-based cleanup) - automatic and simple.

---

## Error Handling

### Manifest Download Failure
- Retry in `validate_receipt` Lambda
- If retry fails, return error (Step Function will catch)

### DynamoDB Query Failure
- Retry in `validate_receipt` Lambda
- If retry fails, return error (Step Function will catch)

### Receipt Validation Failure
- Step Function catch block handles it
- Error logged, execution continues with other receipts

---

## Monitoring

### CloudWatch Metrics

1. **S3 Operations**:
   - `s3_put_object` count (manifest file)
   - `s3_get_object` count (manifest downloads)
   - S3 request latency

2. **Lambda Metrics**:
   - `list_pending_labels`: Duration, memory usage
   - `validate_receipt`: Duration, memory usage, errors
   - `report_results`: Duration, memory usage

3. **Step Function Metrics**:
   - Execution duration
   - Success/failure rate
   - Receipts processed per execution

---

## ChromaDB Configuration

### ChromaDB Snapshot Download (Always Used)

**Setup**: Download ChromaDB snapshots from S3 in each Lambda

**Implementation**:
- Each `validate_receipt` Lambda downloads the "words" collection snapshot from S3
- Uses `download_snapshot_atomic()` helper function
- Initializes `ChromaDBClient` in read mode
- ChromaDB validation runs after CoVe validation in the LangGraph workflow

**Environment Variables**:
- `CHROMADB_BUCKET`: S3 bucket name containing ChromaDB snapshots

**Performance**:
- Download happens once per Lambda invocation
- Snapshot is cached in `/tmp` (Lambda ephemeral storage)
- Download time: ~10-30 seconds depending on snapshot size
- **Free**: No additional AWS costs (S3 downloads are free within same region)

**Benefits**:
- Consistent validation approach across all pending labels
- Uses historical validated data for similarity search
- Fast similarity queries once snapshot is loaded
- No external service dependencies

---

## Next Steps

1. Implement `list_pending_labels` Lambda with S3 upload
2. Implement `validate_receipt` Lambda with S3 download and ChromaDB snapshot download
3. Create Step Function definition with Map state
4. Set up S3 lifecycle policy for cleanup
5. Configure `CHROMADB_BUCKET` environment variable in Lambda
6. Test with small number of receipts
7. Monitor payload sizes, S3 usage, and ChromaDB download times
8. Deploy to production

