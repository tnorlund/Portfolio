# LangGraph Metadata Creation - Usage Review

## Overview
All `receipt_metadata` creation now uses the unified LangGraph + Ollama Cloud workflow (`create_receipt_metadata_simple`). This document reviews where it's used and how each Lambda handles ChromaDB access and entity loading.

## Lambda Handlers Using LangGraph

### 1. Upload OCR Handler (`infra/upload_images/container_ocr/handler/embedding_processor.py`)

**Status:** ✅ Uses LangGraph

**Entity Loading:**
- **Uses provided entities** (lines/words passed as parameters)
- If not provided, **reads from DynamoDB** as fallback
- Passes entities directly to LangGraph to avoid re-reading

**ChromaDB Access:**
- **EFS-first with S3/HTTP fallback** (storage mode: "auto")
- Tries EFS snapshot via `UploadEFSSnapshotManager`
- Falls back to S3 download if EFS fails
- Falls back to HTTP endpoint if S3 download fails
- If all fail, proceeds without ChromaDB fast-path

**Code Location:**
```python
# Lines 84-111: Uses provided entities or fetches from DynamoDB
# Lines 190-361: ChromaDB setup (EFS → S3 → HTTP fallback)
# Lines 347-360: Calls create_receipt_metadata_simple with entities
```

---

### 2. Upload Container Handler (`infra/upload_images/container/handler.py`)

**Status:** ✅ Uses LangGraph

**Entity Loading:**
- **Reads from NDJSON files** (lines 120-122)
- Rehydrates to entity objects (line 122)
- **Passes entities directly** to LangGraph (lines 315-316)
- **Does NOT read from DynamoDB** - uses NDJSON only

**ChromaDB Access:**
- **EFS → S3 → HTTP fallback** (storage mode: "auto")
- Infrastructure has EFS mount configured (lines 677-680 in infra.py)
- Tries EFS first if available (lines 175-204)
- Falls back to S3 download if EFS fails (lines 207-269)
- Falls back to HTTP endpoint if S3 download fails (lines 277-290)
- If all fail, proceeds without ChromaDB fast-path

**Code Location:**
```python
# Lines 120-122: Reads from NDJSON
# Lines 147-310: ChromaDB setup (EFS → S3 → HTTP fallback)
# Lines 312-327: Calls create_receipt_metadata_simple with NDJSON entities
```

---

### 3. Validate Merchant Handler (`infra/validate_merchant_step_functions/container/handler.py`)

**Status:** ✅ Uses LangGraph

**Entity Loading:**
- **Reads from DynamoDB** using `load_receipt_context()` (line 90)
- Passes entities directly to LangGraph (lines 123-124)
- **Note:** This handler needs the entities for embeddings anyway, so it loads them once

**ChromaDB Access:**
- **HTTP endpoint only** (non-time-sensitive)
- Uses `CHROMA_HTTP_ENDPOINT` if available
- If HTTP fails, proceeds without ChromaDB fast-path
- **No EFS or S3 download** (this is a non-time-sensitive Lambda)

**Code Location:**
```python
# Lines 89-92: Loads from DynamoDB (needed for embeddings anyway)
# Lines 95-106: ChromaDB setup (HTTP only)
# Lines 114-128: Calls create_receipt_metadata_simple with loaded entities
```

---

### 4. Line Polling Handler (`infra/embedding_step_functions/unified_embedding/handlers/line_polling.py`)

**Status:** ✅ Uses LangGraph

**Entity Loading:**
- **Reads from DynamoDB** using `get_receipt_details()` (lines 155-157)
- Passes entities directly to LangGraph (lines 173-174)
- **Note:** This is called during batch polling, so entities are fetched fresh

**ChromaDB Access:**
- **No ChromaDB client passed** (line 175)
- This handler doesn't have ChromaDB access configured
- LangGraph workflow proceeds without ChromaDB fast-path

**Code Location:**
```python
# Lines 153-161: Loads from DynamoDB
# Lines 165-175: Calls create_receipt_metadata_simple (no ChromaDB)
```

---

### 5. Word Polling Handler (`infra/embedding_step_functions/unified_embedding/handlers/word_polling.py`)

**Status:** ✅ Uses LangGraph

**Entity Loading:**
- **Reads from DynamoDB** using `get_receipt_details()` (similar to line_polling)
- Passes entities directly to LangGraph
- **Note:** This is called during batch polling, so entities are fetched fresh

**ChromaDB Access:**
- **No ChromaDB client passed**
- This handler doesn't have ChromaDB access configured
- LangGraph workflow proceeds without ChromaDB fast-path

---

## Handlers Still Using `resolve_receipt()` (Legacy)

These handlers still use the old programmatic approach and should be migrated:

1. **`infra/merchant_validation_container/handler/handler.py`** (line 242)
   - Uses `resolve_receipt()` with `write_metadata=True`
   - Should be migrated to LangGraph

2. **`infra/embedding_step_functions/simple_lambdas/process_receipt_realtime/handler.py`** (line 118)
   - Uses `resolve_receipt()` with `write_metadata=True`
   - Should be migrated to LangGraph

3. **`infra/validate_merchant_step_functions/handlers/validate_single_receipt_v2.py`** (line 121)
   - Uses `resolve_receipt()` with `write_metadata=True`
   - Should be migrated to LangGraph

4. **`infra/upload_images/embed_from_ndjson.py`** (line 136)
   - Uses `resolve_receipt()` with `write_metadata=True`
   - **Note:** This appears to be an old/legacy file - the container handler (`container/handler.py`) is the active one

---

## ChromaDB Access Strategy Summary

| Lambda | ChromaDB Access | Strategy |
|--------|----------------|----------|
| Upload OCR Handler | EFS → S3 → HTTP | Time-sensitive, tries EFS first |
| Upload Container Handler | **EFS → S3 → HTTP** | Has EFS access configured, tries EFS first |
| Validate Merchant Handler | HTTP only | Non-time-sensitive, HTTP endpoint |
| Line/Word Polling Handlers | None | No ChromaDB access configured |

---

## Entity Loading Strategy Summary

| Lambda | Entity Source | Strategy |
|--------|---------------|----------|
| Upload OCR Handler | Provided params or DynamoDB | Uses provided entities, falls back to DynamoDB |
| Upload Container Handler | **NDJSON files** | ✅ Reads from NDJSON, does NOT read from DynamoDB |
| Validate Merchant Handler | DynamoDB | Reads from DynamoDB (needed for embeddings anyway) |
| Line/Word Polling Handlers | DynamoDB | Reads from DynamoDB during batch polling |

---

## Key Findings

### ✅ Upload Container Handler Uses NDJSON
The upload container handler (`container/handler.py`) **correctly uses entities from NDJSON**:
- Lines 120-122: Reads lines and words from NDJSON files
- Line 122: Rehydrates to entity objects
- Lines 195-196: Passes NDJSON entities directly to LangGraph
- **Does NOT read from DynamoDB** for metadata creation

### ✅ All Handlers Pass Entities Directly
All handlers that use LangGraph now pass `receipt_lines` and `receipt_words` directly to avoid re-reading from DynamoDB within the workflow.

### ⚠️ ChromaDB Access Varies
- **Time-sensitive handlers** (Upload OCR): Use EFS with S3/HTTP fallback
- **Non-time-sensitive handlers** (Container, Validate Merchant): Use HTTP only
- **Polling handlers**: No ChromaDB access (not needed for their use case)

### 📝 Remaining Migration Work
4 handlers still use the old `resolve_receipt()` approach and should be migrated to LangGraph.

