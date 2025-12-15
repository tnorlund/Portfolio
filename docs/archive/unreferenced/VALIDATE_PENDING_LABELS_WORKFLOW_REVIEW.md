# Validate Pending Labels: Step Function Workflow Review (Updated)

## Complete Workflow Flow

### Step 1: ListPendingLabels Lambda

**Input**: `{}` (empty event, or optional `{"limit": 100}`)

**What it does** (SIMPLIFIED - only listing):
1. ✅ **Query DynamoDB** for all `ReceiptWordLabel` entities with `validation_status="PENDING"`
2. ✅ **Group by receipt**: `(image_id, receipt_id)` - creates a dictionary mapping `(image_id, receipt_id) -> [list of PENDING labels]`
3. ✅ **Create manifest file** with array of receipt metadata (NO receipt data fetching):
   ```json
   {
     "execution_id": "...",
     "total_receipts": 100,
     "receipts": [
       {"index": 0, "image_id": "abc123", "receipt_id": 1, "pending_label_count": 5},
       {"index": 1, "image_id": "def456", "receipt_id": 2, "pending_label_count": 3},
       ...
     ]
   }
   ```
4. ✅ **Upload manifest** to S3: `pending_labels/{execution_id}/manifest.json`
5. ✅ **Create receipt_indices array**: `[0, 1, 2, ..., total_receipts-1]` for Map state

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

**Key Change**: No longer fetches lines, words, or metadata. Only lists PENDING labels and groups by receipt.

---

### Step 2: CheckReceipts (Choice State)

**Input**: Result from `ListPendingLabels`

**What it does**:
- Checks if `total_receipts > 0`
- If yes → Go to `ProcessReceipts` (Map state)
- If no → Go to `NoReceipts` (end state)

---

### Step 3: ProcessReceipts (Map State)

**Input**: Result from `ListPendingLabels` (contains `receipt_indices` array)

**What it does**:
- **Iterates over `receipt_indices` array**: `[0, 1, 2, ..., 99]`
- **MaxConcurrency**: 10 (processes 10 receipts in parallel)
- **For each index** (e.g., `0`):
  - Passes to `ValidateReceipt` Lambda:
    ```json
    {
      "index": 0,
      "manifest_s3_key": "pending_labels/abc123-def456-.../manifest.json",
      "manifest_s3_bucket": "bucket-name",
      "execution_id": "abc123-def456-..."
    }
    ```

---

### Step 4: ValidateReceipt Lambda (per receipt, parallel)

**Input**: `{index, manifest_s3_key, manifest_s3_bucket, execution_id}`

**What it does**:
1. ✅ **Download manifest from S3** using `manifest_s3_key` and `manifest_s3_bucket`
2. ✅ **Use index to look up receipt info**: `manifest["receipts"][index]`
   - Gets: `{image_id, receipt_id, pending_label_count}`
3. ✅ **Fetch receipt data from DynamoDB** (NEW - moved from ListPendingLabels):
   - `list_receipt_lines_from_receipt(image_id, receipt_id)`
   - `list_receipt_words_from_receipt(image_id, receipt_id)`
   - `get_receipt_metadata(image_id, receipt_id)`
   - Query PENDING labels for this receipt: `list_receipt_word_labels_with_status(image_id, receipt_id, status="PENDING")`
4. ✅ **Download ChromaDB snapshot** from S3:
   - Downloads "words" collection snapshot
   - Initializes `ChromaDBClient` in read mode
5. ✅ **Run LangGraph workflow** with CoVe + ChromaDB:
   - `analyze_receipt_simple()` with:
     - Fetched `receipt_lines`, `receipt_words`, `receipt_metadata`
     - `chroma_client` for similarity search validation
     - `save_labels=False` (we'll update manually)
   - This runs: Phase 1 (currency) → Phase 2 (line items) → Combine → ChromaDB validation
   - Returns: `ReceiptAnalysis` with `receipt_word_labels_to_add` and `receipt_word_labels_to_update`
6. ✅ **Compare existing PENDING labels with new CoVe-verified labels**:
   - Match by: `(line_id, word_id, label)`
   - If match found and existing is PENDING → update to VALID
   - If new label doesn't exist → add it
7. ✅ **Update DynamoDB**:
   - `update_receipt_word_labels()` for existing labels
   - `add_receipt_word_labels()` for new labels
8. ✅ **Cleanup**: Remove ChromaDB temp directory
9. ✅ **Return results**:
    ```json
    {
      "success": true,
      "index": 0,
      "image_id": "abc123",
      "receipt_id": 1,
      "labels_validated": 5,
      "labels_updated": 3,
      "labels_added": 2
    }
    ```

---

### Step 5: ReportResults Lambda (Optional)

**Input**: Array of results from Map state

**What it does**:
- Aggregates results from all parallel executions
- Logs summary statistics
- Returns final summary

---

## Key Changes from Previous Design

### ✅ What Changed

1. **ListPendingLabels is now lightweight**:
   - Only queries PENDING labels
   - Only groups by receipt
   - Creates manifest with receipt identifiers (image_id, receipt_id)
   - **No longer fetches lines, words, or metadata**

2. **ValidateReceipt now fetches receipt data**:
   - Fetches lines, words, metadata from DynamoDB
   - Fetches PENDING labels for the specific receipt
   - This distributes DynamoDB queries across parallel executions

3. **No S3 NDJSON files for receipt data**:
   - Receipt data is fetched directly from DynamoDB in parallel
   - Only manifest is stored in S3 (very small)
   - Reduces S3 operations and complexity

### ✅ Benefits

1. **Faster ListPendingLabels**: Only queries labels, no heavy data fetching
2. **Better parallelization**: DynamoDB queries distributed across 10 parallel Lambdas
3. **Simpler architecture**: No need to upload/download receipt NDJSON files
4. **Lower S3 costs**: Only manifest file stored in S3
5. **More efficient**: Each Lambda only fetches data for its specific receipt

---

## Updated Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ Step 1: ListPendingLabels Lambda                            │
│                                                              │
│ 1. Query DynamoDB: SELECT * WHERE validation_status=PENDING │
│ 2. Group by (image_id, receipt_id)                          │
│    → {(img1, rec1): [label1, label2],                       │
│       (img2, rec2): [label3], ...}                          │
│ 3. Create manifest:                                          │
│    [{index: 0, image_id: "img1", receipt_id: 1, ...},       │
│     {index: 1, image_id: "img2", receipt_id: 2, ...}]       │
│ 4. Upload manifest to S3                                    │
│ 5. Create receipt_indices: [0, 1, 2, ..., N-1]             │
│                                                              │
│ Output: {manifest_s3_key, receipt_indices: [0,1,2,...]}     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 2: CheckReceipts (Choice)                              │
│                                                              │
│ IF total_receipts > 0:                                      │
│   → ProcessReceipts                                         │
│ ELSE:                                                        │
│   → NoReceipts (end)                                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 3: ProcessReceipts (Map State, MaxConcurrency: 10)     │
│                                                              │
│ Iterate over receipt_indices: [0, 1, 2, ..., 99]           │
│                                                              │
│ Parallel execution (10 at a time):                          │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│   │ Index: 0     │  │ Index: 1     │  │ Index: 2     │     │
│   │ → Lambda     │  │ → Lambda     │  │ → Lambda     │     │
│   └──────────────┘  └──────────────┘  └──────────────┘     │
│   ... (10 parallel)                                         │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 4: ValidateReceipt Lambda (per receipt)                │
│                                                              │
│ 1. Download manifest from S3                                │
│ 2. Look up receipt: manifest["receipts"][index]             │
│    → Gets: {image_id, receipt_id, pending_label_count}      │
│ 3. Fetch from DynamoDB (NEW):                               │
│    - list_receipt_lines_from_receipt(image_id, receipt_id)  │
│    - list_receipt_words_from_receipt(image_id, receipt_id)  │
│    - get_receipt_metadata(image_id, receipt_id)             │
│    - list_receipt_word_labels_with_status(..., "PENDING")   │
│ 4. Download ChromaDB snapshot (words collection)            │
│ 5. Run LangGraph + CoVe + ChromaDB:                         │
│    analyze_receipt_simple(...)                              │
│ 6. Compare: existing PENDING vs new CoVe-verified           │
│ 7. Update DynamoDB:                                         │
│    - update_receipt_word_labels() (PENDING → VALID)         │
│    - add_receipt_word_labels() (new labels)                 │
│ 8. Return: {success, labels_validated, labels_updated, ...} │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 5: ReportResults Lambda (Optional)                     │
│                                                              │
│ Aggregate all results, log summary                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Updated Manifest Format

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

**Note**: No `s3_key` field needed since we're not storing receipt NDJSON files in S3.

---

## Updated Implementation

### ListPendingLabels Lambda (Simplified)

```python
def list_handler(event, context):
    import json
    import boto3
    import uuid
    from datetime import datetime
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.constants import ValidationStatus

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
        "receipt_indices": receipt_indices,
    }
```

### ValidateReceipt Lambda (Updated - Fetches Data)

```python
def validate_handler(event, context):
    import json
    import boto3
    import asyncio
    import tempfile
    import os
    from receipt_dynamo import DynamoClient
    from receipt_dynamo.constants import ValidationStatus
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

        # Fetch receipt data from DynamoDB (NEW - moved from ListPendingLabels)
        lines = dynamo.list_receipt_lines_from_receipt(image_id, receipt_id)
        words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
        metadata = dynamo.get_receipt_metadata(image_id, receipt_id)
        pending_labels = dynamo.list_receipt_word_labels_with_status(
            image_id=image_id,
            receipt_id=receipt_id,
            status=ValidationStatus.PENDING
        )

        # Download ChromaDB snapshot from S3
        chroma_client = None
        chromadb_temp_dir = None
        chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
        if chromadb_bucket:
            try:
                from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic
                from receipt_label.vector_store.client.chromadb_client import ChromaDBClient

                chromadb_temp_dir = tempfile.mkdtemp(prefix="chromadb_")

                words_download = download_snapshot_atomic(
                    bucket=chromadb_bucket,
                    collection="words",
                    local_path=os.path.join(chromadb_temp_dir, "words"),
                    verify_integrity=True,
                )

                if words_download.get("status") == "downloaded":
                    chroma_client = ChromaDBClient(
                        persist_directory=os.path.join(chromadb_temp_dir, "words"),
                        mode="read",
                    )
            except Exception as e:
                logger.warning(f"Failed to initialize ChromaDB client: {e}")

        # Run LangGraph + CoVe + ChromaDB
        result = await analyze_receipt_simple(
            client=dynamo,
            image_id=image_id,
            receipt_id=receipt_id,
            ollama_api_key=os.environ["OLLAMA_API_KEY"],
            langsmith_api_key=os.environ.get("LANGSMITH_API_KEY"),
            save_labels=False,
            dry_run=False,
            receipt_lines=lines,  # Pass pre-fetched data
            receipt_words=words,
            receipt_metadata=metadata,
            chroma_client=chroma_client,
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

        # Cleanup ChromaDB temp directory
        if chromadb_temp_dir and os.path.exists(chromadb_temp_dir):
            try:
                import shutil
                shutil.rmtree(chromadb_temp_dir, ignore_errors=True)
            except Exception:
                pass

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

## Summary

**The updated workflow**:
1. ✅ `ListPendingLabels` is lightweight - only lists and groups PENDING labels
2. ✅ Each `ValidateReceipt` Lambda fetches its own receipt data from DynamoDB
3. ✅ Better parallelization - DynamoDB queries distributed across 10 parallel executions
4. ✅ Simpler architecture - no S3 NDJSON files for receipt data
5. ✅ Lower S3 costs - only manifest file stored in S3

The flow is: **List → Group → Process in Parallel → Fetch Data → Validate → Update**
