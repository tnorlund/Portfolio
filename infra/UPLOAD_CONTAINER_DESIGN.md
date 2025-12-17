# Unified Upload Container Design

## Overview

Single Lambda container that orchestrates the complete receipt upload & embedding pipeline, using the enhanced compactor's snapshots + deltas for real-time merchant resolution.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Mac OCR Script (local)                       │
│  - Polls SQS for OCR jobs                                       │
│  - Processes OCR locally (Vision/Tesseract)                    │
│  - Returns OCR JSON to SQS (ocr_results_queue)                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│          Lambda: Upload Container (Single Handler)              │
│                                                                 │
│  INPUT: SQS message with OCR results                           │
│                                                                 │
│  STEP 1: Receipt Type Decision                                 │
│  ├─ Parse OCR data                                             │
│  ├─ Classify: NATIVE | PHOTO | SCAN | REFINEMENT              │
│  └─ Store OCRJob + entities in DynamoDB                        │
│                                                                 │
│  STEP 2: If NATIVE or REFINEMENT → Synchronous Processing     │
│  ├─ Read Receipt-level entities from DynamoDB                  │
│  │  (lines, words, letters)                                    │
│  │                                                              │
│  ├─ Generate Line & Word Embeddings                            │
│  │  (using OpenAI)                                             │
│  │                                                              │
│  ├─ **MERCHANT RESOLUTION (Sync)**                             │
│  │  ├─ Fetch current ChromaDB snapshot (from S3)               │
│  │  ├─ Query snapshot + freshly generated deltas               │
│  │  │  to find similar merchants                               │
│  │  └─ Enrich receipt with merchant metadata                   │
│  │     (place_id, confirmed_name, etc.)                        │
│  │                                                              │
│  └─ OUTPUT: Receipt with merchant data (immediate)             │
│                                                                 │
│  STEP 3: Asynchronous Embedding Storage                        │
│  ├─ Generate embedding delta (lines + words + merchant context)│
│  ├─ Push to enhanced compactor (SQS or direct invoke)          │
│  │  - Compactor merges delta into Chroma snapshots             │
│  │  - Creates new snapshot version                             │
│  │  - Records in DynamoDB compaction_runs                      │
│  └─ Lambda returns immediately (doesn't wait)                  │
│                                                                 │
│  STEP 4: If PHOTO or SCAN                                      │
│  └─ No embedding, wait for REFINEMENT job later                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
    DynamoDB          Chroma          Enhanced
    Entities          Snapshot        Compactor
    (enriched)        (updated)       (async delta)
```

## Key Design Principles

### 1. Synchronous Merchant Resolution
- Happens **within the container invocation**
- Uses current ChromaDB snapshot + fresh embeddings + delta
- Returns immediately with merchant context
- Ensures receipts are fully enriched at first write to DynamoDB

### 2. Asynchronous Embedding Storage
- Embeddings pushed to enhanced compactor asynchronously
- Compactor handles merging deltas into Chroma snapshots
- Lambda doesn't wait for compactor to finish
- Reduces Lambda timeout risk

### 3. Single Source of Truth
- All receipt data lives in DynamoDB
- Snapshots + deltas in Chroma for vector search
- No separate NDJSON artifacts needed
- Compactor owns snapshot lifecycle

## Data Flow

### Current State (in container_ocr/handler/)
1. **Input**: SQS message with `image_id` and `job_id`
2. **OCRProcessor** fetches OCRJob and OCRRoutingDecision from DynamoDB
3. **OCRRoutingDecision** contains S3 bucket/key of OCR JSON (written by Mac script)
4. Downloads OCR JSON from S3, parses into lines/words/letters
5. Stores entities in DynamoDB
6. **EmbeddingProcessor** generates embeddings and creates deltas
7. Pushes deltas to SQS for async compaction

### Proposed New Flow (Single Unified Container)

1. **Input**: SQS message with `image_id` and `job_id`
   ```
   Mac OCR Script → S3 (writes OCR JSON) → SQS (triggers Lambda with image_id, job_id)
   ```

2. **Step 1: OCR Processing & Classification** (OCRProcessor)
   - Fetch OCRJob and OCRRoutingDecision from DynamoDB
   - Download OCR JSON from S3 via S3 key in routing decision
   - Parse OCR → lines, words, letters
   - Classify: NATIVE | PHOTO | SCAN | REFINEMENT
   - Store entities in DynamoDB
   - Determine receipt_id based on type

3. **Step 2: Conditional Embedding & Merchant Resolution** (New MerchantResolvingEmbeddingProcessor)

   **If NATIVE or REFINEMENT** (has receipt_id):

   ```python
   # REPLACE current EmbeddingProcessor with:
   from receipt_chroma import create_embeddings_and_compaction_run

   result = create_embeddings_and_compaction_run(
       image_id=image_id,
       receipt_id=receipt_id,
       lines=lines,
       words=words,
       chromadb_bucket=chromadb_bucket,
       s3_client=s3,
       dynamo_client=dynamo,
       openai_api_key=...,
   )

   # result.lines_client and result.words_client have SNAPSHOT + DELTA pre-merged
   # Query for merchant resolution NOW, while data is in memory
   merchant_data = resolve_merchant_from_snapshot(
       result.lines_client,
       result.words_client,
       lines=lines,
   )

   # Enrich receipt in DynamoDB with merchant data
   enrich_receipt_with_merchant(
       image_id=image_id,
       receipt_id=receipt_id,
       merchant_data=merchant_data,
   )

   # result.compaction_run already pushed deltas to S3 and queued for compaction
   # Lambda returns immediately (compaction happens async)
   result.close()
   ```

   **If PHOTO or SCAN** (no receipt_id):
   - No embeddings, wait for REFINEMENT job later
   - Return immediately

4. **Output**: Receipt record in DynamoDB with:
   - OCR data (lines, words, letters)
   - Merchant information (place_id, name, confidence) ← **NEW, happens synchronously**
   - Compaction run tracking (deltas already queued for async merge)
   - Metadata (image_type, receipt_id, timestamps)

## Components Needed

### 1. OCR Processor ✅ (Already Exists)
**Location**: `infra/upload_images/container_ocr/handler/ocr_processor.py`

Existing implementation is solid:
- Fetches OCRJob from DynamoDB
- Downloads OCR JSON from S3 (via OCRRoutingDecision key)
- Parses OCR data into lines/words/letters
- Classifies image type (NATIVE/PHOTO/SCAN/REFINEMENT)
- Stores entities in DynamoDB
- Returns receipt_id

✅ **No changes needed** - reuse as-is

### 2. Merchant Resolving Embedding Processor 🆕 (NEW - Replace EmbeddingProcessor)
**Location**: `infra/upload_images/container_ocr/handler/merchant_resolving_embedding_processor.py`

New implementation needed to:
1. Call `receipt_chroma.create_embeddings_and_compaction_run()`
   - This generates embeddings
   - Creates deltas and pushes to S3 (async)
   - Returns `EmbeddingResult` with merged snapshot+delta clients

2. Query the merged clients for merchant resolution
   ```python
   result = create_embeddings_and_compaction_run(
       image_id=image_id,
       receipt_id=receipt_id,
       lines=lines,
       words=words,
       chromadb_bucket=chromadb_bucket,
       s3_client=s3,
       dynamo_client=dynamo,
   )

   # Lines + words clients have snapshot + delta pre-merged
   merchant_data = query_for_merchant(
       result.lines_client,
       result.words_client,
       lines,
   )
   ```

3. Enrich receipt with merchant data in DynamoDB
   ```python
   dynamo.update_receipt_metadata(
       image_id=image_id,
       receipt_id=receipt_id,
       merchant_place_id=merchant_data['place_id'],
       merchant_name=merchant_data['name'],
   )
   ```

4. Return compaction tracking info

### 3. Merchant Resolution Query Logic
**Location**: TBD (part of merchant_resolving_embedding_processor)

Interface needed:
```python
def query_for_merchant(
    lines_client: ChromaClient,  # snapshot + delta pre-merged
    words_client: ChromaClient,  # snapshot + delta pre-merged
    current_lines: List[ReceiptLine],
) -> Dict[str, Any]:
    """
    Query snapshot+delta clients for merchant name/place_id.

    Returns:
        {
            "place_id": str,
            "name": str,
            "confidence": float,
            "query_method": "lines" | "words",
        }
    """
```

Implementation options:
- A) Query by merchant line (if structured data)
- B) Query by all lines, find best merchant match
- C) Call receipt_places API after narrowing candidates from Chroma

## Implementation Details (Based on Research)

### Key Findings About receipt_chroma

✅ `create_embeddings_and_compaction_run()` returns `EmbeddingResult` containing:
- `lines_client` and `words_client`: ChromaClients with **snapshot + delta pre-merged**
- `compaction_run`: CompactionRun entity for tracking
- Deltas automatically pushed to S3 and queued for async compaction
- Ready for immediate queries within the Lambda invocation

✅ The workflow is:
1. Download snapshot from S3
2. Generate embeddings via OpenAI
3. Create delta files locally
4. Upsert to snapshot client (snapshot + delta merged in memory)
5. Upsert to delta client (separate files)
6. Upload delta tarballs to S3
7. Send SQS notification for async compaction
8. Return clients with merged data ready for queries

### Remaining Implementation Questions

1. **Merchant Query Strategy**: How should we query for merchants?
   - Query by specific merchant line (if structured)?
   - Query all lines and find best similarity match?
   - Query words and aggregate?
   - Use line vectors only or also word vectors?

2. **Merchant Confidence Threshold**:
   - If confidence below threshold, should we:
     - Try receipt_places API for validation?
     - Store receipt without merchant?
     - Fail the invocation?

3. **PHOTO/SCAN REFINEMENT**: When split receipt is refined:
   - Re-generate embeddings from scratch?
   - Reuse original OCR embeddings with new receipt_id?
   - Mix of both?

4. **DynamoDB Schema**: Where to store merchant data?
   - Receipt metadata table?
   - Separate merchant table?
   - Part of receipt entity?

5. **Merchant Update Later**: If merchant resolution fails initially, can DynamoDB stream processor add it later?
   - Or should we ensure it happens in the container?

## Implementation Roadmap

### Phase 1: Consolidate into Single Container ✅ (Mostly done)
- ✅ Keep `container_ocr/handler/ocr_processor.py` (already great)
- ✅ Keep `container_ocr/handler/handler.py` (main orchestrator)
- 🔄 Replace `container_ocr/handler/embedding_processor.py` with new merchant-resolving version

### Phase 2: Implement MerchantResolvingEmbeddingProcessor 🆕 (NEW WORK)
**File**: `infra/upload_images/container_ocr/handler/merchant_resolving_embedding_processor.py`

```python
from receipt_chroma import create_embeddings_and_compaction_run

class MerchantResolvingEmbeddingProcessor:
    def process_embeddings_with_merchant(
        self,
        image_id: str,
        receipt_id: int,
        lines: List[ReceiptLine],
        words: List[ReceiptWord],
    ) -> Dict[str, Any]:
        """
        1. Call create_embeddings_and_compaction_run()
           - Returns EmbeddingResult with merged snapshot+delta clients
           - Automatically queues deltas for async compaction

        2. Query merged clients for merchant
           - lines_client/words_client have snapshot + delta pre-merged
           - Query to find similar merchants

        3. Enrich receipt with merchant data
           - Call dynamo.update_receipt_metadata()
           - Store merchant place_id and name

        4. Close clients and return
        """
```

### Phase 3: Merchant Query Implementation
**File**: `infra/upload_images/container_ocr/handler/merchant_query.py`

Implement merchant resolution logic. Key decisions needed:
- Query strategy (by specific merchant line vs. all lines vs. word-level)
- Confidence thresholds
- Fallback to receipt_places API if needed
- Error handling

### Phase 4: Update Dockerfile & Infra Config
- `container_ocr/Dockerfile`: Already has correct dependencies
- Update `infra.py` to remove references to:
  - Old `process_ocr_results.py`
  - Old `container/` embedding-only Lambda
  - Old `embed_from_ndjson` queue
  - Unused environment variables

### Phase 5: Cleanup Removal
- Delete `infra/upload_images/process_ocr_results.py` (obsolete)
- Delete `infra/upload_images/container/` (obsolete, functionality moved to container_ocr)
- Delete `infra/upload_images/container_ocr/handler/embedding_processor.py` (replaced)
- Rename `container_ocr/` to just `container/` (now the unified handler)

### Phase 6: Testing & Monitoring
- Test all three image types: NATIVE, PHOTO, SCAN, REFINEMENT
- Verify merchant resolution triggers correctly
- Monitor CloudWatch metrics for:
  - Embedding generation latency
  - Merchant query performance
  - Compaction async completion time
  - DynamoDB write success rates
