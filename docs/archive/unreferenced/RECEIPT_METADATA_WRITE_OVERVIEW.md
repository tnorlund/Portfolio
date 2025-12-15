# Receipt Metadata Write Overview

This document provides a comprehensive overview of all Lambda functions that write `ReceiptMetadata` to DynamoDB, including their methods, triggers, and write mechanisms.

## Write Mechanism

All `ReceiptMetadata` writes ultimately go through the same DynamoDB operation:
- **Method:** `client.add_receipt_metadatas([metadata])` (or `add_receipt_metadata(metadata)`)
- **Location:** `receipt_dynamo/receipt_dynamo/data/_receipt_metadata.py`
- **Operation:** Batch write to DynamoDB (up to 25 items per batch)

---

## Lambdas Using LangGraph (Current Approach) ✅

These Lambdas use the unified `create_receipt_metadata_simple()` workflow with LangGraph + Ollama Cloud.

### 1. Upload OCR Handler
**File:** `infra/upload_images/container_ocr/handler/embedding_processor.py`

**Method:**
- Calls `create_receipt_metadata_simple()` (line 348)
- Uses LangGraph workflow with optional ChromaDB fast-path

**Write Location:**
- `receipt_label/receipt_label/langchain/nodes/metadata_creation/create_metadata.py` (line 49)
- Calls `client.add_receipt_metadatas([metadata])`

**Trigger:**
- Called from `process_embeddings()` method
- Triggered by OCR results processing in upload container Lambda

**Entity Source:**
- Uses provided `receipt_lines` and `receipt_words` parameters
- Falls back to DynamoDB if not provided

**ChromaDB Access:**
- EFS → S3 → HTTP fallback
- Optional fast-path before Places API search

---

### 2. Upload Container Handler (embed_from_ndjson)
**File:** `infra/upload_images/container/handler.py`

**Method:**
- Calls `create_receipt_metadata_simple()` (line 316)
- Uses LangGraph workflow with optional ChromaDB fast-path

**Write Location:**
- `receipt_label/receipt_label/langchain/nodes/metadata_creation/create_metadata.py` (line 49)
- Calls `client.add_receipt_metadatas([metadata])`

**Trigger:**
- Lambda handler processes SQS messages from `embed_ndjson_queue`
- Processes NDJSON files from S3

**Entity Source:**
- **Reads from NDJSON files** (lines 120-122)
- Rehydrates to entity objects
- Passes entities directly to LangGraph (does NOT read from DynamoDB)

**ChromaDB Access:**
- EFS → S3 → HTTP fallback
- Optional fast-path before Places API search

---

### 3. Validate Merchant Handler (Container)
**File:** `infra/validate_merchant_step_functions/container/handler.py`

**Method:**
- Calls `create_receipt_metadata_simple()` (line 115)
- Uses LangGraph workflow with optional ChromaDB fast-path

**Write Location:**
- `receipt_label/receipt_label/langchain/nodes/metadata_creation/create_metadata.py` (line 49)
- Calls `client.add_receipt_metadatas([metadata])`

**Trigger:**
- Direct Lambda invocation (Step Function infrastructure removed)
- Can be invoked manually or via other triggers

**Entity Source:**
- Reads from DynamoDB using `load_receipt_context()` (line 90)
- Passes entities directly to LangGraph

**ChromaDB Access:**
- HTTP endpoint only (non-time-sensitive)
- Optional fast-path before Places API search

**Status:** ⚠️ **Infrastructure disabled** - Handler exists but Step Function infrastructure was removed as redundant

---

### 4. Line Polling Handler
**File:** `infra/embedding_step_functions/unified_embedding/handlers/line_polling.py`

**Method:**
- Calls `create_receipt_metadata_simple()` (line 165)
- Uses LangGraph workflow (no ChromaDB access)

**Write Location:**
- `receipt_label/receipt_label/langchain/nodes/metadata_creation/create_metadata.py` (line 49)
- Calls `client.add_receipt_metadatas([metadata])`

**Trigger:**
- Called from `_ensure_receipt_metadata_async()` (line 62)
- Triggered during OpenAI batch polling when metadata is missing

**Entity Source:**
- Reads from DynamoDB using `get_receipt_details()` (line 155)
- Passes entities directly to LangGraph

**ChromaDB Access:**
- None (no ChromaDB client configured)

---

### 5. Word Polling Handler
**File:** `infra/embedding_step_functions/unified_embedding/handlers/word_polling.py`

**Method:**
- Calls `create_receipt_metadata_simple()` (line 164)
- Uses LangGraph workflow (no ChromaDB access)

**Write Location:**
- `receipt_label/receipt_label/langchain/nodes/metadata_creation/create_metadata.py` (line 49)
- Calls `client.add_receipt_metadatas([metadata])`

**Trigger:**
- Called from `_ensure_receipt_metadata_async()` (similar to line polling)
- Triggered during OpenAI batch polling when metadata is missing

**Entity Source:**
- Reads from DynamoDB using `get_receipt_details()`
- Passes entities directly to LangGraph

**ChromaDB Access:**
- None (no ChromaDB client configured)

---

## Lambdas Using Legacy `resolve_receipt()` Approach ⚠️

These Lambdas still use the old programmatic `resolve_receipt()` function with `write_metadata=True`.

### 6. Merchant Validation Container Handler
**File:** `infra/merchant_validation_container/handler/handler.py`

**Method:**
- Calls `resolve_receipt()` with `write_metadata=True` (line 242)

**Write Location:**
- `receipt_label/receipt_label/merchant_resolution/resolver.py` (line 115)
- Calls `dynamo.add_receipt_metadatas([meta])` when:
  - `write_metadata=True`
  - Best candidate is from Places API
  - Best candidate has a `place_id`

**Trigger:**
- Lambda handler processes merchant validation requests
- Part of merchant validation container workflow

**Entity Source:**
- Reads from DynamoDB using `load_receipt_context()`

**ChromaDB Access:**
- Uses `DirectChromaAdapter` with EFS path (`CHROMA_ROOT`)

**Status:** ⚠️ **Should be migrated to LangGraph**

---

### 7. Process Receipt Realtime Handler
**File:** `infra/embedding_step_functions/simple_lambdas/process_receipt_realtime/handler.py`

**Method:**
- Calls `resolve_receipt()` with `write_metadata=False` (line 124)
- **Note:** Currently does NOT write metadata (write_metadata=False)

**Write Location:**
- N/A (write_metadata=False)

**Trigger:**
- Step Function workflow for realtime processing

**Entity Source:**
- Reads from DynamoDB

**ChromaDB Access:**
- HTTP endpoint if available

**Status:** ⚠️ **Uses legacy approach but doesn't write metadata**

---

### 8. Validate Single Receipt V2 Handler
**File:** `infra/validate_merchant_step_functions/handlers/validate_single_receipt_v2.py`

**Method:**
- Calls `resolve_receipt()` with `write_metadata=True` (line 127)

**Write Location:**
- `receipt_label/receipt_label/merchant_resolution/resolver.py` (line 115)
- Calls `dynamo.add_receipt_metadatas([meta])` when conditions are met

**Trigger:**
- Part of ValidateMerchantStepFunctions (now disabled/removed)
- May not be in use

**Entity Source:**
- Reads from DynamoDB

**ChromaDB Access:**
- HTTP endpoint if available

**Status:** ⚠️ **Legacy - Step Function infrastructure removed, handler may not be in use**

---

### 9. Embed from NDJSON (Legacy File)
**File:** `infra/upload_images/embed_from_ndjson.py`

**Method:**
- Calls `resolve_receipt()` with `write_metadata=True` (line 136)

**Write Location:**
- `receipt_label/receipt_label/merchant_resolution/resolver.py` (line 115)
- Calls `dynamo.add_receipt_metadatas([meta])` when conditions are met

**Trigger:**
- Appears to be a legacy file
- **Note:** The active handler is `container/handler.py` (see #2 above)

**Entity Source:**
- Reads from NDJSON files

**ChromaDB Access:**
- HTTP endpoint if available

**Status:** ⚠️ **Legacy file - may not be in use**

---

## Write Flow Summary

### LangGraph Workflow (Current)
```
Lambda Handler
  ↓
create_receipt_metadata_simple()
  ↓
LangGraph Workflow:
  1. load_receipt_data_for_metadata (uses provided entities or fetches from DynamoDB)
  2. check_chromadb (optional fast-path)
  3. extract_merchant_info (Ollama Cloud)
  4. search_places_for_merchant_with_agent (Ollama Cloud + Places API)
  5. create_receipt_metadata
      ↓
client.add_receipt_metadatas([metadata])
      ↓
DynamoDB
```

### Legacy `resolve_receipt()` Workflow
```
Lambda Handler
  ↓
resolve_receipt(write_metadata=True)
  ↓
Programmatic Flow:
  1. Query ChromaDB for similar receipts
  2. Search Google Places API
  3. Select best candidate
  4. If write_metadata=True and best is from Places with place_id:
      ↓
dynamo.add_receipt_metadatas([meta])
      ↓
DynamoDB
```

---

## Migration Status

| Lambda | Status | Method | Notes |
|--------|--------|--------|-------|
| Upload OCR Handler | ✅ Migrated | LangGraph | Uses EFS/S3/HTTP for ChromaDB |
| Upload Container Handler | ✅ Migrated | LangGraph | Reads from NDJSON, uses EFS/S3/HTTP |
| Validate Merchant Handler (Container) | ✅ Migrated | LangGraph | Uses HTTP for ChromaDB, infrastructure disabled |
| Line Polling Handler | ✅ Migrated | LangGraph | No ChromaDB access |
| Word Polling Handler | ✅ Migrated | LangGraph | No ChromaDB access |
| Merchant Validation Container | ⚠️ Legacy | resolve_receipt() | Should migrate |
| Process Receipt Realtime | ⚠️ Legacy | resolve_receipt() | Doesn't write (write_metadata=False) |
| Validate Single Receipt V2 | ⚠️ Legacy | resolve_receipt() | Step Function removed, may not be in use |
| Embed from NDJSON (legacy) | ⚠️ Legacy | resolve_receipt() | May not be in use |

---

## Key Differences

### LangGraph Approach (Current)
- ✅ Uses Ollama Cloud for intelligent extraction and search
- ✅ Optional ChromaDB fast-path before Places API
- ✅ Better error handling and observability (LangSmith tracing)
- ✅ Consistent workflow across all handlers
- ✅ Handles OCR errors better with LLM-based extraction

### Legacy `resolve_receipt()` Approach
- ⚠️ Programmatic ChromaDB + Places API search
- ⚠️ No LLM-based extraction
- ⚠️ Less flexible and adaptive
- ⚠️ Limited observability
- ⚠️ Only writes if best candidate is from Places with place_id

---

## Recommendations

1. **Migrate remaining handlers** to LangGraph:
   - Merchant Validation Container Handler
   - Validate Single Receipt V2 Handler (if still in use)

2. **Remove or update legacy files:**
   - `embed_from_ndjson.py` (if not in use)
   - `process_receipt_realtime/handler.py` (if write_metadata should be True)
   - ValidateMerchantStepFunctions infrastructure (removed - redundant with LangGraph)

3. **Standardize ChromaDB access:**
   - All handlers should support EFS → S3 → HTTP fallback
   - Non-time-sensitive handlers can use HTTP only

4. **Document write conditions:**
   - LangGraph always writes (creates minimal metadata if no match)
   - Legacy approach only writes if Places match found with place_id

5. **Consider consolidation/batch cleaning:**
   - If needed, extract as standalone scheduled Lambdas
   - No need for Step Function infrastructure

