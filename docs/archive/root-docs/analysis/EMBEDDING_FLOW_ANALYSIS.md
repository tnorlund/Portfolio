# Receipt Embedding and ChromaDB Flow Analysis

## Date: October 24, 2025

## Executive Summary

This document traces the complete end-to-end flow for receipt processing, from image upload through OCR, merchant validation, embedding, and ChromaDB compaction with EFS storage. We identified a critical missing piece: the stream processor lacks COMPACTION_RUN completion detection logic that triggers the final compaction step.

---

## Current Architecture Overview

### Infrastructure Components

1. **Upload & OCR Pipeline** (`infra/upload_images/`)
   - Next.js frontend uploads images
   - Upload Receipt Lambda creates OCR jobs
   - Process OCR Results Lambda extracts text (lines, words, letters)
   - Writes to DynamoDB and exports NDJSON to S3

2. **Merchant Validation** (`infra/validate_merchant_step_functions/`)
   - Step Function orchestrates validation
   - Uses ChromaDB similarity search to find similar receipts
   - Queries Google Places API for merchant data
   - Creates `ReceiptMetadata` with canonical merchant information

3. **Embedding Pipeline** (Multiple approaches)
   - **Batch approach** (slow): OpenAI batch API via step functions
   - **Realtime approach** (fast): Direct embedding with merchant context

4. **ChromaDB Compaction** (`infra/chromadb_compaction/`)
   - **Stream Processor Lambda**: Monitors DynamoDB stream for changes
   - **Enhanced Compaction Lambda**: Merges embeddings into ChromaDB
   - **EFS Storage**: Persistent ChromaDB data at `/mnt/chroma`
   - **S3 Snapshots**: Backup and recovery

5. **Chroma ECS Service** (`infra/chroma/`)
   - Scale-to-zero Fargate service
   - HTTP endpoint for queries
   - Orchestrator Step Function manages scaling

---

## Complete End-to-End Flow

### Phase 1: Image Upload & OCR (✅ Working)

```
User uploads image
  ↓
upload-receipt Lambda
  ↓
Creates OCRJob in DynamoDB
  ↓
OCR processing (external)
  ↓
process-ocr-results Lambda
  ├─ Classifies image type (SCAN/PHOTO/NATIVE)
  ├─ Extracts lines, words, letters
  ├─ Writes to DynamoDB (LINE/WORD/LETTER records)
  └─ Exports NDJSON to S3 artifacts bucket
```

**Key Files:**
- `infra/upload_images/process_ocr_results.py` (lines 66-101, 237-240)
- Exports NDJSON: `receipts/{image_id}/receipt-{receipt_id:05d}/lines.ndjson`
- Queues to: `embed-ndjson-queue` (but no consumer!)

**Current Status:** ✅ Working - OCR successfully extracts text and writes to DynamoDB

---

### Phase 2: Merchant Validation (✅ Working)

```
Merchant Validation Step Function triggers
  ↓
validate-single-receipt Lambda
  ├─ Loads receipt context (lines, words from DynamoDB)
  ├─ Uses ChromaDB similarity search (chroma_find_candidates)
  │   ├─ Queries with address lines
  │   ├─ Queries with phone lines
  │   └─ Finds similar receipts with known merchants
  ├─ Queries Google Places API
  └─ Creates ReceiptMetadata with:
      ├─ merchant_name (canonical from Google Places)
      ├─ place_id
      ├─ address
      ├─ phone_number
      └─ merchant_category
```

**Key Files:**
- `receipt_label/receipt_label/merchant_resolution/chroma.py` - ChromaDB similarity search
- `receipt_label/receipt_label/merchant_resolution/resolver.py` - Resolution logic
- `infra/validate_merchant_step_functions/handlers/validate_single_receipt_v2.py`

**Data Created:**
- `ReceiptMetadata` entity in DynamoDB with SK: `RECEIPT#{receipt_id:05d}#METADATA`

**Current Status:** ✅ Working - Merchant validation uses ChromaDB to find similar receipts

---

### Phase 3: Embedding Creation (✅ Working)

```
Embedding process (realtime or batch)
  ↓
Loads ReceiptMetadata from DynamoDB
  ↓
Gets merchant_name for context
  ↓
Creates embeddings with merchant context:
  ├─ Lines: embed_lines_realtime(lines, merchant_name)
  └─ Words: embed_words_realtime(words, merchant_name)
  ↓
Stores embeddings as ChromaDB deltas in S3
  ↓
Updates COMPACTION_RUN in DynamoDB:
  ├─ lines_state: PENDING → PROCESSING → COMPLETED
  ├─ words_state: PENDING → PROCESSING → COMPLETED
  ├─ lines_finished_at: timestamp
  └─ words_finished_at: timestamp
```

**Key Files:**
- `receipt_label/receipt_label/embedding/line/realtime.py` (lines 157-161)
- `receipt_label/receipt_label/embedding/word/realtime.py`
- `receipt_label/receipt_label/merchant_resolution/embeddings.py` (lines 12-35, 69)

**Metadata Structure:**
```python
{
    "image_id": "uuid",
    "receipt_id": "1",
    "line_id": 1,
    "embedding_type": "line",
    "text": "line text",
    "merchant_name": "Starbucks",  # ← From ReceiptMetadata!
    "x": 0.5,
    "y": 0.3,
    # ... geometry and confidence data
}
```

**Current Status:** ✅ Working - Embeddings include merchant context from metadata

---

### Phase 4: Stream Processing (❌ BROKEN - Missing Logic)

```
DynamoDB Stream triggers
  ↓
Stream Processor Lambda receives events
  ↓
Current behavior:
  ├─ ✅ Handles RECEIPT_METADATA changes (MODIFY/REMOVE)
  ├─ ✅ Handles RECEIPT_WORD_LABEL changes (MODIFY/REMOVE)
  ├─ ✅ Handles COMPACTION_RUN INSERT (fast-path)
  └─ ❌ MISSING: COMPACTION_RUN MODIFY with completion detection
  
Missing logic (from commit 606599d3):
  ↓
Should detect when COMPACTION_RUN is updated with:
  - lines_state == "COMPLETED" AND
  - words_state == "COMPLETED"
  ↓
Should queue message to compaction Lambda:
  {
    "entity_type": "COMPACTION_RUN",
    "entity_data": {
      "run_id": "uuid",
      "image_id": "uuid",
      "receipt_id": 1
    },
    "collections": ["lines", "words"],
    "event_name": "MODIFY"
  }
```

**Key Files:**
- `infra/chromadb_compaction/lambdas/stream_processor.py` - Main orchestrator
- `infra/chromadb_compaction/lambdas/processor/message_builder.py` - Message construction
- **Missing from:** `processor/parsers.py` - No COMPACTION_RUN MODIFY detection

**What's Missing:**
The modular stream processor (from PR #394) doesn't include the COMPACTION_RUN completion detection logic that was added in commit `606599d3` on the `feat/efs_in_chroma` branch.

**Original Logic (from 606599d3):**
```python
def _run_embeddings_completed(img: Optional[dict]) -> bool:
    if not img:
        return False
    ls = img.get("lines_state", {}).get("S")
    ws = img.get("words_state", {}).get("S")
    lf = bool(img.get("lines_finished_at") and "S" in img.get("lines_finished_at", {}))
    wf = bool(img.get("words_finished_at") and "S" in img.get("words_finished_at", {}))
    return ((ls == "COMPLETED" and ws == "COMPLETED") or (lf and wf))
```

**Current Status:** ❌ BROKEN - Stream processor doesn't detect embedding completion

---

### Phase 5: ChromaDB Compaction (⏸️ Blocked by Phase 4)

```
Compaction Lambda receives SQS message
  ↓
Loads COMPACTION_RUN from DynamoDB
  ↓
Downloads ChromaDB deltas from S3
  ↓
Mounts EFS at /mnt/chroma
  ↓
Merges deltas into persistent ChromaDB:
  ├─ Lines collection
  └─ Words collection
  ↓
Updates ChromaDB metadata with merchant info
  ↓
Creates S3 snapshot for backup
  ↓
Updates COMPACTION_RUN status
```

**Key Files:**
- `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`
- Uses `receipt_label` package for ChromaDB operations

**EFS Configuration:**
```json
{
  "FileSystemConfigs": [{
    "Arn": "arn:aws:elasticfilesystem:...:access-point/fsap-...",
    "LocalMountPath": "/mnt/chroma"
  }],
  "Environment": {
    "CHROMA_ROOT": "/mnt/chroma",
    "CHROMADB_BUCKET": "chromadb-dev-shared-buckets-vectors-c239843"
  }
}
```

**Current Status:** ⏸️ Blocked - Lambda is configured correctly but never triggered

---

## Git History Analysis

### Key Branches and Commits

1. **`feat/efs_in_chroma`** - Original EFS work
   - `2d8850aa` - Add EFS support for ChromaDB compaction Lambdas ✅ (cherry-picked)
   - `606599d3` - **Enhance ChromaDB compaction infra and snapshot sync** ❌ (NOT on current branch)
     - Added COMPACTION_RUN completion detection
     - Added 66 lines to stream_processor.py
     - This is the missing piece!

2. **`fix/realtime_embedding`** - Realtime embedding work
   - `2b22f68a` - Add NDJSON embedding pipeline for uploaded images
   - Merged into main via PR #389

3. **`main`** - Production branch
   - PR #388 - Better merchant validation (merchant_resolution package)
   - PR #389 - Chroma infrastructure with NAT egress and orchestration
   - PR #394 - Modular ChromaDB Compaction Architecture (our refactor)

4. **`feat/efs_modular_rebase`** - Current branch
   - Only cherry-picked first commit from `feat/efs_in_chroma`
   - Missing commits: `0afeec1f`, `80b9e502`, `57cb8746`, **`606599d3`**, `7ae105f3`

---

## What's Working vs. What's Not

### ✅ Working Components

1. **OCR Pipeline** - Successfully extracts text from images
2. **Merchant Validation** - Uses ChromaDB similarity + Google Places
3. **Embedding Creation** - Includes merchant context from metadata
4. **EFS Infrastructure** - Compaction Lambda has EFS mounted at `/mnt/chroma`
5. **S3 Snapshots** - Backup/recovery infrastructure in place
6. **Chroma ECS Service** - Running and queryable
7. **Stream Processor** - Handles metadata/label updates correctly

### ❌ Broken/Missing Components

1. **COMPACTION_RUN Completion Detection** - Stream processor doesn't detect when embeddings are complete
2. **Initial Embedding Compaction** - New receipts never get their embeddings merged into ChromaDB on EFS
3. **embed-ndjson-queue Consumer** - No Lambda connected to process NDJSON embedding queue

### ⚠️ Architectural Questions

1. **Two Embedding Approaches**:
   - Batch (slow): OpenAI batch API via step functions
   - Realtime (fast): Direct embedding
   - Which one is actually being used?

2. **NDJSON Queue**:
   - `process-ocr-results` queues to `embed-ndjson-queue`
   - But no Lambda is consuming it
   - Is this intentional or a missing piece?

---

## Required Fixes

### Priority 1: Add COMPACTION_RUN Completion Detection

**Location:** `infra/chromadb_compaction/lambdas/processor/`

**Changes Needed:**

1. **Update `processor/parsers.py`:**
   - Add detection for COMPACTION_RUN entity type
   - Parse `lines_state`, `words_state`, `lines_finished_at`, `words_finished_at`

2. **Update `processor/message_builder.py`:**
   - Add logic to detect MODIFY events on COMPACTION_RUN
   - Check if both `lines_state` and `words_state` are `COMPLETED`
   - Build message to trigger compaction for both collections

3. **Update `processor/models.py`:**
   - Add `CompactionRun` entity type if needed
   - Ensure message structure supports compaction run data

**Expected Behavior:**
```
COMPACTION_RUN MODIFY event
  ↓
lines_state: "COMPLETED"
words_state: "COMPLETED"
  ↓
Stream processor queues message:
  - To: lines-queue AND words-queue
  - Message type: COMPACTION_RUN_COMPLETE
  - Data: run_id, image_id, receipt_id
  ↓
Compaction Lambda processes both collections
```

### Priority 2: Test End-to-End Flow

1. Upload a receipt image
2. Wait for OCR completion
3. Trigger merchant validation
4. Wait for embedding completion
5. Verify COMPACTION_RUN is updated
6. Verify stream processor triggers compaction
7. Verify embeddings appear in ChromaDB on EFS

---

## Testing Strategy

### Manual Test Script

Use existing `dev.test_stream_processor_trigger.py` to:
1. Update COMPACTION_RUN with `lines_state=COMPLETED`, `words_state=COMPLETED`
2. Monitor stream processor logs for detection
3. Monitor compaction Lambda logs for processing
4. Query ChromaDB to verify embeddings

### Verification Queries

```bash
# Check stream processor logs
aws logs tail /aws/lambda/chromadb-dev-stream-processor-4b3f763 --since 5m

# Check compaction Lambda logs  
aws logs tail /aws/lambda/chromadb-dev-enhanced-compaction-7ef4b03 --since 5m

# Check SQS queues
aws sqs get-queue-attributes --queue-url <lines-queue-url>
aws sqs get-queue-attributes --queue-url <words-queue-url>

# Check EFS mount
# (Would need to exec into Lambda or check CloudWatch metrics)
```

---

## Next Steps

1. ✅ Document current state (this file)
2. 🔄 Implement COMPACTION_RUN completion detection
3. 🔄 Test with real receipt upload
4. 🔄 Verify embeddings in ChromaDB on EFS
5. 🔄 Cherry-pick remaining commits from `feat/efs_in_chroma`
6. 🔄 Create PR to merge into main

---

## References

### Key Source Files

- **Stream Processor:** `infra/chromadb_compaction/lambdas/stream_processor.py`
- **Processor Package:** `infra/chromadb_compaction/lambdas/processor/`
- **Compaction Handler:** `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`
- **Merchant Resolution:** `receipt_label/receipt_label/merchant_resolution/`
- **Embedding (Realtime):** `receipt_label/receipt_label/embedding/line/realtime.py`
- **OCR Processing:** `infra/upload_images/process_ocr_results.py`

### Related PRs

- PR #388: Better merchant validation
- PR #389: Chroma infrastructure with NAT egress and orchestration
- PR #394: Modular ChromaDB Compaction Architecture

### Git Commits

- `606599d3`: Enhance ChromaDB compaction infra and snapshot sync (MISSING LOGIC)
- `2d8850aa`: Add EFS support for ChromaDB compaction Lambdas (APPLIED)
- `8f0d8db2`: Better merchant validation (#388)
- `aaec062a`: Add Chroma infrastructure with NAT egress and orchestration (#389)

---

## Appendix: Data Flow Diagram

```
┌─────────────┐
│ Next.js UI  │
└──────┬──────┘
       │ Upload Image
       ↓
┌─────────────────┐
│ Upload Receipt  │
│     Lambda      │
└────────┬────────┘
         │ Create OCRJob
         ↓
┌─────────────────┐
│  OCR Service    │
│   (External)    │
└────────┬────────┘
         │ Extract Text
         ↓
┌─────────────────────┐
│ Process OCR Results │
│      Lambda         │
└──────┬──────┬───────┘
       │      │
       │      └─────────────────┐
       │                        │
       ↓                        ↓
┌──────────────┐      ┌─────────────────┐
│  DynamoDB    │      │  S3 Artifacts   │
│ LINE/WORD/   │      │ (NDJSON files)  │
│ LETTER       │      └─────────────────┘
└──────┬───────┘
       │
       ↓
┌──────────────────────┐
│ Merchant Validation  │
│   Step Function      │
└──────┬───────────────┘
       │
       ├─────────────────────┐
       │                     │
       ↓                     ↓
┌──────────────┐    ┌────────────────┐
│  ChromaDB    │    │ Google Places  │
│  Similarity  │    │      API       │
│   Search     │    └────────────────┘
└──────┬───────┘
       │
       ↓
┌──────────────────┐
│ ReceiptMetadata  │
│   (DynamoDB)     │
└──────┬───────────┘
       │
       ↓
┌──────────────────┐
│ Embedding Process│
│ (with merchant)  │
└──────┬───────────┘
       │
       ├─────────────────────┐
       │                     │
       ↓                     ↓
┌──────────────┐    ┌────────────────┐
│ ChromaDB     │    │ COMPACTION_RUN │
│  Deltas      │    │   (DynamoDB)   │
│   (S3)       │    │ lines_state:   │
└──────────────┘    │   COMPLETED    │
                    │ words_state:   │
                    │   COMPLETED    │
                    └────────┬───────┘
                             │
                             ↓
                    ┌────────────────┐
                    │ DynamoDB Stream│
                    └────────┬───────┘
                             │
                             ↓
                    ┌────────────────────┐
                    │ Stream Processor   │
                    │     Lambda         │
                    │                    │
                    │ ❌ MISSING LOGIC:  │
                    │ Detect completion  │
                    │ & queue compaction │
                    └────────┬───────────┘
                             │
                             ↓
                    ┌────────────────────┐
                    │   SQS Queues       │
                    │ (lines & words)    │
                    └────────┬───────────┘
                             │
                             ↓
                    ┌────────────────────┐
                    │ Enhanced Compaction│
                    │      Lambda        │
                    │                    │
                    │ ✅ Has EFS mounted │
                    │ ⏸️ Never triggered │
                    └────────┬───────────┘
                             │
                             ├─────────────────┐
                             │                 │
                             ↓                 ↓
                    ┌────────────────┐ ┌──────────────┐
                    │  ChromaDB      │ │  S3 Snapshot │
                    │  on EFS        │ │   Backup     │
                    │ /mnt/chroma    │ └──────────────┘
                    └────────────────┘
```

---

**Document Status:** Complete  
**Last Updated:** October 24, 2025  
**Next Action:** Implement COMPACTION_RUN completion detection in stream processor

