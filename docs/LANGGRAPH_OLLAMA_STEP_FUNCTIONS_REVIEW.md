# LangGraph/Ollama Step Functions & Dev Scripts Review

## Executive Summary

This document reviews all Step Functions and dev scripts that use LangGraph/Ollama, distinguishing what runs in AWS vs. locally, and suggests additional Step Functions that could be added.

---

## Step Functions Using LangGraph/Ollama (AWS)

### 1. **Currency Validation Step Function** ✅
**Location**: `infra/currency_validation_step_functions/`

**Purpose**: Validates currency-related labels (GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL) for receipts using LangGraph workflow.

**Workflow**:
```
ListReceipts (Lambda)
  → HasReceipts? (Choice)
  → ForEachReceipt (Map, MaxConcurrency: 10)
    → ValidateReceipt (Lambda) - Runs LangGraph currency validation
  → Done
```

**Lambda Functions**:
- `list_receipts.handler`: Lists receipts from DynamoDB
- `process_receipt.handler`: Runs `analyze_receipt_simple()` with LangGraph/Ollama

**Configuration**:
- **Max Concurrency**: 10 receipts in parallel
- **Timeout**: 600 seconds per receipt (10 minutes)
- **Memory**: 1536 MB
- **Environment**:
  - `OLLAMA_API_KEY` (from Pulumi secrets)
  - `LANGCHAIN_API_KEY` (for LangSmith tracing)
  - `LANGCHAIN_PROJECT`: "currency-validation"

**What it does**:
- Runs full LangGraph workflow (`analyze_receipt_simple`)
- Creates/updates `ReceiptWordLabel` entities
- Validates currency-related labels using Ollama Cloud (gpt-oss:120b-cloud)

**Status**: ✅ Deployed and active

---

### 2. **Validate Pending Labels Step Function** ✅
**Location**: `infra/validate_pending_labels/`

**Purpose**: Validates PENDING `ReceiptWordLabel` entities using LangGraph + CoVe + ChromaDB.

**Workflow**:
```
ListPendingLabels (Lambda)
  → CheckReceipts (Choice)
  → ProcessReceipts (Map, MaxConcurrency: 3)
    → ValidateReceipt (Container Lambda)
      - Downloads ChromaDB from S3
      - Runs LangGraph + CoVe validation
      - Updates validation_status (VALID/INVALID/NEEDS_REVIEW)
  → Done
```

**Lambda Functions**:
- `list_pending_labels.handler`: Queries DynamoDB for PENDING labels, creates manifest
- `validate_receipt.handler` (container-based): Validates labels using LangGraph + CoVe

**Configuration**:
- **Max Concurrency**: 3 receipts (reduced from 10 to avoid Ollama rate limiting)
- **Timeout**: 900 seconds per receipt (15 minutes)
- **Memory**: 2048 MB
- **Ephemeral Storage**: 10 GB (for ChromaDB snapshot)
- **Container-based**: Uses Docker image with LangGraph dependencies
- **Environment**:
  - `OLLAMA_API_KEY`
  - `LANGCHAIN_API_KEY`
  - `GOOGLE_PLACES_API_KEY`
  - `CHROMADB_BUCKET` (for downloading ChromaDB snapshot)

**What it does**:
- Two-tier validation:
  1. **ChromaDB similarity search** (fast, cheap) - pre-filters high-confidence cases
  2. **LangGraph + CoVe** (accurate, slower) - validates remaining PENDING labels
- Updates `validation_status` to VALID, INVALID, or NEEDS_REVIEW
- Uses `validate_pending_labels_cove_simple()` from `receipt_label.langchain.validate_pending_labels_cove`

**Status**: ✅ Deployed and active

---

### 3. **Validate Metadata Step Function** ✅
**Location**: `infra/validate_metadata/`

**Purpose**: Validates `ReceiptMetadata` entities against receipt text using LangGraph + CoVe.

**Workflow**:
```
ListMetadata (Lambda)
  → CheckMetadata (Choice)
  → ProcessMetadata (Map, MaxConcurrency: 3)
    → ValidateMetadata (Container Lambda)
      - Downloads ChromaDB from S3
      - Runs LangGraph + CoVe validation
      - Validates ReceiptMetadata against receipt content
  → Done
```

**Lambda Functions**:
- `list_metadata.handler`: Lists all ReceiptMetadata entities, creates manifest
- `validate_metadata.handler` (container-based): Validates metadata using LangGraph + CoVe

**Configuration**:
- **Max Concurrency**: 3 receipts (reduced from 10 to avoid Ollama rate limiting)
- **Timeout**: 900 seconds per receipt (15 minutes)
- **Memory**: 2048 MB
- **Ephemeral Storage**: 10 GB (for ChromaDB snapshot)
- **Container-based**: Uses Docker image with LangGraph dependencies
- **Environment**:
  - `OLLAMA_API_KEY`
  - `LANGCHAIN_API_KEY`
  - `GOOGLE_PLACES_API_KEY`
  - `CHROMADB_BUCKET`

**What it does**:
- Validates `ReceiptMetadata` entities against actual receipt text
- Uses Chain of Verification (CoVe) to ensure metadata accuracy
- Can update metadata if mismatches are found

**Status**: ✅ Deployed and active

---

### 4. **Validate Merchant Step Function** ✅
**Location**: `infra/validate_merchant_step_functions/`

**Purpose**: Validates merchant information and creates `ReceiptMetadata` using LangGraph + Google Places API.

**Workflow**:
```
ListReceipts (Lambda)
  → ForEachReceipt (Map, MaxConcurrency: 5)
    → ValidateReceipt (Container Lambda)
      - Runs create_receipt_metadata_simple() (LangGraph)
      - Creates ReceiptMetadata using Ollama Cloud
      - Upserts embeddings to ChromaDB
  → ConsolidateMetadata (Lambda)
```

**Lambda Functions**:
- `list_receipts.handler`: Lists receipts needing merchant validation
- `validate_single_receipt_v2.handler` (zip-based): Alternative handler using Google Places API
- `container/handler.py` (container-based): **Uses LangGraph** for metadata creation
- `consolidate_new_metadata.handler`: Consolidates merchant data
- `batch_clean_merchants.handler`: Weekly batch cleaning

**Configuration**:
- **Max Concurrency**: 5 receipts in parallel
- **Container-based**: Uses Docker image with LangGraph dependencies
- **Environment**:
  - `OLLAMA_API_KEY` (required for LangGraph)
  - `LANGCHAIN_API_KEY` (required for LangGraph)
  - `GOOGLE_PLACES_API_KEY`
  - `OPENAI_API_KEY` (for embeddings)
  - `CHROMADB_BUCKET`

**What it does**:
- Creates `ReceiptMetadata` using LangGraph workflow (`create_receipt_metadata_simple`)
- Uses Ollama Cloud (gpt-oss:120b-cloud) for merchant name/address extraction
- Validates merchant information with Google Places API
- Upserts embeddings to ChromaDB for similarity matching
- Consolidates merchant data for canonical representations

**Status**: ✅ Deployed and active

**Note**: The container-based handler uses LangGraph/Ollama, while the zip-based handler (`validate_single_receipt_v2`) uses Google Places API only.

---

### 5. **Upload Images Lambda (Real-time Validation)** ✅
**Location**: `infra/upload_images/container_ocr/handler/handler.py`

**Purpose**: Runs LangGraph validation **asynchronously** after OCR processing completes.

**Workflow**:
```
OCR Processing (Container Lambda)
  → _run_validation_async() (Background thread)
    → analyze_receipt_simple() (LangGraph)
      - Creates/updates ReceiptWordLabels
      - Validates ReceiptMetadata
```

**Configuration**:
- **Trigger**: After OCR results are processed (SQS queue)
- **Execution**: Background thread (non-blocking)
- **Environment**:
  - `OLLAMA_API_KEY`
  - `LANGCHAIN_API_KEY`
  - `GOOGLE_PLACES_API_KEY`

**What it does**:
- Runs LangGraph validation immediately after OCR processing
- Creates/updates `ReceiptWordLabel` entities
- Validates and auto-corrects `ReceiptMetadata` if merchant name doesn't match
- Runs in background thread so it doesn't delay Lambda response

**Status**: ✅ Deployed and active (integrated into upload pipeline)

---

## Dev Scripts Using LangGraph/Ollama (Local)

### 1. **dev.process_all_receipts_cove.py** 🔧
**Purpose**: Process all receipts end-to-end with LangGraph + CoVe (creates/updates labels and validates metadata).

**What it does**:
1. Downloads ChromaDB snapshot from S3 (with caching)
2. Lists all receipts from DynamoDB
3. For each receipt:
   - Runs `analyze_receipt_simple()` with CoVe
   - Creates/updates `ReceiptWordLabels` (with `validation_status` based on CoVe)
   - Validates and updates `ReceiptMetadata` (with Google Places API if needed)

**Usage**:
```bash
python dev.process_all_receipts_cove.py [--stack dev] [--limit N] [--dry-run] [--force-download]
```

**When to run**:
- Initial setup: Once to process all existing receipts
- After adding new receipts: When new receipt images are uploaded
- After major changes: When CoVe prompts or validation logic changes
- Periodic refresh: Monthly to catch any missed receipts

**Cost**: High (LLM API calls for every receipt)
**Duration**: ~2-3 hours for 400 receipts

**Status**: ✅ Available for local execution

---

### 2. **dev.validate_pending_labels_chromadb.py** 🔧
**Purpose**: Batch validate PENDING labels using LangGraph + CoVe (with optional ChromaDB pre-filtering).

**What it does**:
1. Queries all PENDING labels from DynamoDB
2. Groups labels by receipt (image_id, receipt_id)
3. For each receipt with PENDING labels:
   - Optionally: Pre-filter with ChromaDB (quick wins for high-confidence cases)
   - Re-runs LangGraph workflow with CoVe for remaining PENDING labels
   - Updates `validation_status` based on CoVe verification

**Usage**:
```bash
python dev.validate_pending_labels_chromadb.py [--stack dev] [--limit N] [--dry-run] [--use-chromadb-prefilter]
```

**When to run**:
- After initial processing: To validate labels that were marked PENDING
- After new receipts processed: To catch PENDING labels from new uploads
- Periodic validation: To re-validate labels that couldn't be verified initially

**Cost**: Medium-High (LLM API calls, but only for receipts with PENDING labels)

**Status**: ✅ Available for local execution

**Note**: This is the **local equivalent** of the `validate_pending_labels` Step Function.

---

### 3. **dev.validate_receipt_metadata_cove.py** 🔧
**Purpose**: Validate `ReceiptMetadata` entities using Chain of Verification with LangGraph.

**What it does**:
1. Downloads ChromaDB snapshot from S3
2. Lists all `ReceiptMetadata` entities from DynamoDB
3. Runs LangGraph workflow with CoVe for each receipt
4. Validates that `ReceiptMetadata` matches the actual receipt content

**Usage**:
```bash
python dev.validate_receipt_metadata_cove.py [--stack dev] [--limit N] [--dry-run]
```

**When to run**:
- After metadata creation: To validate all ReceiptMetadata entities
- After major changes: When metadata extraction logic changes
- Periodic validation: To catch any metadata drift

**Cost**: Medium (LLM API calls for every receipt with metadata)

**Status**: ✅ Available for local execution

**Note**: This is the **local equivalent** of the `validate_metadata` Step Function.

---

### 4. **dev.revalidate_invalid_core_labels_cove.py** 🔧
**Purpose**: Re-validate INVALID CORE_LABELS using CoVe.

**What it does**:
1. Queries DynamoDB for all INVALID labels
2. Filters to only CORE_LABELS
3. Groups them by receipt
4. Runs CoVe validation on each receipt's labels
5. Updates DynamoDB with results (VALID, INVALID, or NEEDS_REVIEW)

**Usage**:
```bash
python dev.revalidate_invalid_core_labels_cove.py [--stack dev] [--limit N] [--dry-run]
```

**When to run**:
- After validation changes: When validation logic improves
- Periodic review: To catch false negatives (labels incorrectly marked INVALID)

**Cost**: Medium (LLM API calls for receipts with INVALID core labels)

**Status**: ✅ Available for local execution

**Note**: **No Step Function equivalent** - could be a candidate for productionization!

---

### 5. **dev.revalidate_needs_review_labels_cove.py** 🔧
**Purpose**: Re-validate NEEDS_REVIEW labels using CoVe.

**What it does**:
- Similar to `revalidate_invalid_core_labels_cove.py` but for NEEDS_REVIEW labels
- Uses CoVe to determine if labels should be VALID or INVALID

**Status**: ✅ Available for local execution

**Note**: **No Step Function equivalent** - could be a candidate for productionization!

---

## AWS vs. Local Comparison

| Aspect | AWS Step Functions | Local Dev Scripts |
|--------|-------------------|-------------------|
| **Execution** | Automated, scheduled | Manual, on-demand |
| **Environment** | AWS Lambda (container or zip) | Local machine |
| **ChromaDB** | Downloads from S3 to `/tmp` (10 GB ephemeral storage) | Downloads from S3 to local disk (with caching) |
| **API Keys** | Pulumi secrets (environment variables) | Pulumi secrets (loaded at runtime) |
| **Monitoring** | CloudWatch Logs + Metrics | Local logs |
| **Error Handling** | Automatic retry + DLQ | Manual retry |
| **Cost** | AWS Lambda pricing | Local compute |
| **Scalability** | Auto-scaling (parallel processing) | Single machine |
| **Timeout** | 15 minutes max (Lambda) | No limit |
| **Concurrency** | Configurable (3-10 receipts in parallel) | Sequential (unless manually parallelized) |
| **Observability** | CloudWatch, LangSmith traces | Local logs, LangSmith traces |

---

## Missing Step Functions (Opportunities)

### 1. **Re-validate INVALID Core Labels Step Function** 🚀
**Rationale**:
- `dev.revalidate_invalid_core_labels_cove.py` exists but has no Step Function equivalent
- Useful for catching false negatives (labels incorrectly marked INVALID)
- Should run periodically (weekly/monthly)

**Workflow**:
```
ListInvalidCoreLabels (Lambda)
  → CheckLabels (Choice)
  → ProcessReceipts (Map, MaxConcurrency: 3)
    → RevalidateReceipt (Container Lambda)
      - Runs LangGraph + CoVe
      - Updates validation_status (VALID/INVALID/NEEDS_REVIEW)
  → Done
```

**Benefits**:
- Automated re-validation of INVALID labels
- Catches false negatives from improved validation logic
- Parallel processing for efficiency

---

### 2. **Re-validate NEEDS_REVIEW Labels Step Function** 🚀
**Rationale**:
- `dev.revalidate_needs_review_labels_cove.py` exists but has no Step Function equivalent
- NEEDS_REVIEW labels need periodic re-validation
- Should run weekly to clear the backlog

**Workflow**:
```
ListNeedsReviewLabels (Lambda)
  → CheckLabels (Choice)
  → ProcessReceipts (Map, MaxConcurrency: 3)
    → RevalidateReceipt (Container Lambda)
      - Runs LangGraph + CoVe
      - Updates validation_status (VALID/INVALID/NEEDS_REVIEW)
  → Done
```

**Benefits**:
- Automated re-validation of NEEDS_REVIEW labels
- Clears backlog of uncertain labels
- Parallel processing for efficiency

---

### 3. **Process All Receipts Step Function** 🚀
**Rationale**:
- `dev.process_all_receipts_cove.py` exists but has no Step Function equivalent
- Useful for initial setup or periodic full refresh
- Should run on-demand or monthly

**Workflow**:
```
ListAllReceipts (Lambda)
  → CheckReceipts (Choice)
  → ProcessReceipts (Map, MaxConcurrency: 3)
    → ProcessReceipt (Container Lambda)
      - Downloads ChromaDB from S3
      - Runs analyze_receipt_simple() with CoVe
      - Creates/updates ReceiptWordLabels
      - Validates/updates ReceiptMetadata
  → Done
```

**Benefits**:
- Automated full receipt processing
- Useful for initial setup or periodic refresh
- Parallel processing for efficiency

**Note**: This is a long-running workflow (2-3 hours for 400 receipts), so it needs STANDARD Step Function (not EXPRESS).

---

### 4. **Merchant Validation Step Function** (Already exists, but review needed) ✅
**Location**: `infra/validate_merchant_step_functions/`

**Status**: ✅ Exists but **doesn't use LangGraph/Ollama** - uses Google Places API only

**Potential Enhancement**: Could integrate LangGraph for merchant name extraction/validation if needed.

---

## Recommendations

### High Priority
1. **Create "Re-validate INVALID Core Labels" Step Function**
   - Productionize `dev.revalidate_invalid_core_labels_cove.py`
   - Schedule weekly to catch false negatives

2. **Create "Re-validate NEEDS_REVIEW Labels" Step Function**
   - Productionize `dev.revalidate_needs_review_labels_cove.py`
   - Schedule weekly to clear backlog

### Medium Priority
3. **Create "Process All Receipts" Step Function**
   - Productionize `dev.process_all_receipts_cove.py`
   - Run on-demand or monthly for full refresh
   - Use STANDARD Step Function (long-running)

### Low Priority
4. **Merchant Validation Step Function** ✅
   - Already uses LangGraph/Ollama in container handler
   - No changes needed

---

## Summary

### Step Functions Using LangGraph/Ollama (AWS)
1. ✅ **Currency Validation** - Validates currency labels
2. ✅ **Validate Pending Labels** - Validates PENDING labels with CoVe
3. ✅ **Validate Metadata** - Validates ReceiptMetadata with CoVe
4. ✅ **Validate Merchant** - Creates ReceiptMetadata using LangGraph (container handler)
5. ✅ **Upload Images Lambda** - Real-time validation after OCR

### Dev Scripts Using LangGraph/Ollama (Local)
1. ✅ **dev.process_all_receipts_cove.py** - Full receipt processing
2. ✅ **dev.validate_pending_labels_chromadb.py** - Validate PENDING labels
3. ✅ **dev.validate_receipt_metadata_cove.py** - Validate metadata
4. ✅ **dev.revalidate_invalid_core_labels_cove.py** - Re-validate INVALID labels
5. ✅ **dev.revalidate_needs_review_labels_cove.py** - Re-validate NEEDS_REVIEW labels

### Missing Step Functions (Opportunities)
1. 🚀 **Re-validate INVALID Core Labels** - High priority
2. 🚀 **Re-validate NEEDS_REVIEW Labels** - High priority
3. 🚀 **Process All Receipts** - Medium priority

---

## Next Steps

1. Review this document and prioritize which Step Functions to create
2. Create infrastructure for high-priority Step Functions
3. Set up EventBridge schedules for periodic execution
4. Monitor costs and performance
5. Consider consolidating similar workflows if patterns emerge

