# Receipt Metadata Finder Agent - Both Lambda Integration

## Overview

The receipt metadata finder agent has been integrated into **both** upload lambda workflows to automatically find ALL missing metadata fields (place_id, merchant_name, address, phone_number) for receipts.

## Two Different Scenarios

### 1. Container OCR Handler (`infra/upload_images/container_ocr/handler/handler.py`)
**Scenario**: Processes OCR results directly
- **Lines/Words Source**: From OCR processing (already in memory)
- **Embeddings**: Not yet stored in ChromaDB (being created)
- **Use Case**: First-time receipt processing after OCR

### 2. Embed from NDJSON Handler (`infra/upload_images/container/handler.py`)
**Scenario**: Processes NDJSON files from S3
- **Lines/Words Source**: Read from NDJSON files (already in memory)
- **Embeddings**: Not yet stored in ChromaDB (being created)
- **Use Case**: Processing receipts from NDJSON artifacts

## Key Differences

Both scenarios share the same challenge:
- Lines/words are **already loaded in memory** (from OCR or NDJSON)
- Embeddings **haven't been stored in ChromaDB yet**
- The metadata finder should **NOT read from DynamoDB** for lines/words when they're already available

## Solution

### Updated `run_receipt_metadata_finder` Function

The workflow function now accepts pre-loaded lines/words:

```python
async def run_receipt_metadata_finder(
    graph: Any,
    state_holder: dict,
    image_id: str,
    receipt_id: int,
    line_embeddings: Optional[dict[str, list[float]]] = None,
    word_embeddings: Optional[dict[str, list[float]]] = None,
    receipt_lines: Optional[list] = None,  # NEW: Pre-loaded lines
    receipt_words: Optional[list] = None,   # NEW: Pre-loaded words
) -> dict:
```

**Key Changes**:
1. Accepts `receipt_lines` and `receipt_words` parameters
2. Converts entity objects to dict format expected by agent tools
3. Sets them in `ReceiptContext` so agent tools don't try to load from DynamoDB
4. Avoids unnecessary DynamoDB reads when data is already available

### Updated `MetadataFinderProcessor`

The processor now passes pre-loaded lines/words:

```python
agent_result = await run_receipt_metadata_finder(
    graph=graph,
    state_holder=state_holder,
    image_id=image_id,
    receipt_id=receipt_id,
    receipt_lines=receipt_lines,  # Pass pre-loaded lines (avoids DynamoDB read)
    receipt_words=receipt_words,   # Pass pre-loaded words (avoids DynamoDB read)
)
```

## Integration Points

### 1. Container OCR Handler

**File**: `infra/upload_images/container_ocr/handler/embedding_processor.py`

**Integration**:
- After `create_receipt_metadata_simple()` creates initial metadata
- Calls `MetadataFinderProcessor.find_missing_metadata()`
- Passes `receipt_lines` and `receipt_words` from OCR processing
- Updates metadata with any found fields

### 2. Embed from NDJSON Handler

**File**: `infra/upload_images/container/handler.py`

**Integration**:
- After `create_receipt_metadata_simple()` creates initial metadata
- Calls `MetadataFinderProcessor.find_missing_metadata()`
- Passes `lines` and `words` loaded from NDJSON files
- Updates metadata with any found fields

## Files Modified

1. **`receipt_agent/receipt_agent/graph/receipt_metadata_finder_workflow.py`**
   - Added `receipt_lines` and `receipt_words` parameters to `run_receipt_metadata_finder()`
   - Converts entity objects to dict format for agent tools
   - Sets pre-loaded data in `ReceiptContext` to avoid DynamoDB reads

2. **`infra/upload_images/container_ocr/handler/metadata_finder_processor.py`**
   - Updated to pass `receipt_lines` and `receipt_words` to workflow

3. **`infra/upload_images/container_ocr/handler/embedding_processor.py`**
   - Already integrated (from previous work)

4. **`infra/upload_images/container/handler.py`**
   - Added metadata finder integration after initial metadata creation
   - Passes lines/words from NDJSON files

5. **`infra/upload_images/container/metadata_finder_processor.py`** (new)
   - Copied from container_ocr version for use in embed_from_ndjson handler

6. **`infra/upload_images/container_ocr/Dockerfile`**
   - Added `receipt_agent` and `receipt_places` packages

7. **`infra/upload_images/container/Dockerfile`**
   - Added `receipt_agent` and `receipt_places` packages

## Benefits

1. **Efficient**: Avoids unnecessary DynamoDB reads when data is already in memory
2. **Works in both scenarios**: Handles OCR processing and NDJSON processing
3. **Complete metadata**: Finds all missing fields automatically
4. **Non-blocking**: Failures don't break the upload process

## How It Works

### Container OCR Handler Flow

1. OCR processing → lines/words in memory
2. Initial metadata creation → may have only merchant_name
3. Metadata finder runs → uses pre-loaded lines/words (no DynamoDB read)
4. Updates metadata with found fields
5. Embeddings created with complete metadata

### Embed from NDJSON Handler Flow

1. Read NDJSON files → lines/words in memory
2. Initial metadata creation → may have only merchant_name
3. Metadata finder runs → uses pre-loaded lines/words (no DynamoDB read)
4. Updates metadata with found fields
5. Embeddings created with complete metadata

## Error Handling

- If ChromaDB is unavailable, the finder is skipped (non-critical)
- If the agent fails, it's logged but doesn't fail the upload
- If all fields are already present, the finder is skipped (efficient)
- If lines/words aren't provided, the agent will load from DynamoDB (fallback)

## Testing

Both handlers should:
1. Create initial metadata with merchant_name
2. Run metadata finder to find missing fields
3. Update metadata with found fields
4. Continue with embedding creation

Look for `[METADATA_FINDER]` prefix in CloudWatch logs for both handlers.


