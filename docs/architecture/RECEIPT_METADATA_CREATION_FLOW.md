# ReceiptMetadata Creation Flow

## Overview

`ReceiptMetadata` is created during the **OCR processing** phase in the `upload-images-process-ocr-image-dev` lambda, specifically during the **merchant validation and embedding** step.

## Flow Summary

```
OCR Job Complete
    ↓
Upload Lambda Handler
    ↓
OCRProcessor.process_ocr_job()
    ↓
EmbeddingProcessor.process_embeddings()  ← ReceiptMetadata created here
    ↓
EmbeddingProcessor._resolve_merchant()
    ↓
resolve_receipt() [with write_metadata=True]
    ↓
ChromaDB candidate search (using embeddings)
    ↓
Google Places API fallback (if ChromaDB score < 0.5)
    ↓
decide_best_candidate()
    ↓
metadata_from_places_candidate()  ← Creates ReceiptMetadata
    ↓
dynamo.add_receipt_metadatas([meta])
    ↓
ReceiptMetadata written to DynamoDB
```

## Step-by-Step Details

### 1. Lambda Handler Entry Point

**File**: `infra/upload_images/container_ocr/handler/handler.py` (lines 90-217)

```python
def _process_single_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single SQS record."""
    # ...
    
    # Step 1: Process OCR (parse, classify, store in DynamoDB)
    ocr_result = ocr_processor.process_ocr_job(image_id, job_id)
    
    # Step 2: Validate merchant and create embeddings
    embedding_processor = EmbeddingProcessor(...)
    embedding_result = embedding_processor.process_embeddings(
        image_id=image_id,
        receipt_id=receipt_id,
        lines=ocr_result.get("receipt_lines"),
        words=ocr_result.get("receipt_words"),
    )
```

### 2. Embedding Processor

**File**: `infra/upload_images/container_ocr/handler/embedding_processor.py` (lines 62-145)

```python
def process_embeddings(self, image_id: str, receipt_id: int, ...):
    # Step 1: Use provided lines/words or fetch from DynamoDB
    receipt_lines = lines or self.dynamo.list_receipt_lines_from_receipt(...)
    receipt_words = words or self.dynamo.list_receipt_words_from_receipt(...)
    
    # Step 2: Resolve merchant
    merchant_name = self._resolve_merchant(image_id, receipt_id)
    
    # Step 3: Generate embeddings and upload deltas
    run_id = self._create_embeddings_and_deltas(...)
```

### 3. Merchant Resolution

**File**: `infra/upload_images/container_ocr/handler/embedding_processor.py` (lines 147-345)

```python
def _resolve_merchant(self, image_id: str, receipt_id: int) -> Optional[str]:
    # Initialize ChromaDB client (from EFS or S3 snapshot)
    chroma_line_client = ...
    
    # Resolve merchant
    resolution = resolve_receipt(
        key=(image_id, receipt_id),
        dynamo=self.dynamo,
        places_api=self.places_api,
        chroma_line_client=chroma_line_client,
        embed_fn=_embed_texts,
        write_metadata=True,  # ← Creates ReceiptMetadata!
    )
    
    # Extract merchant name
    decision = resolution.get("decision") or {}
    best = decision.get("best") or {}
    merchant_name = best.get("name") or best.get("merchant_name")
    
    return merchant_name
```

### 4. Receipt Resolution

**File**: `receipt_label/receipt_label/merchant_resolution/resolver.py` (lines 19-122)

```python
def resolve_receipt(key, dynamo, places_api, chroma_line_client, embed_fn, write_metadata):
    # Load receipt context (merchant name, phone, address from OCR)
    ctx = load_receipt_context(dynamo, key)
    
    # Step 1: Try ChromaDB (similar receipts)
    c = chroma_find_candidates(chroma_line_client, embed_fn, ctx, _get_neighbor_phones)
    chroma_best = max(c or [], key=lambda x: x.get("score", 0.0), default=None)
    
    # Step 2: Fallback to Google Places API if ChromaDB score < 0.5
    threshold = 0.5
    if not chroma_best or float(chroma_best.get("score", 0.0)) < threshold:
        p = places_find_candidates(places_api, ctx)
    else:
        p = []
    
    # Step 3: Decide best candidate
    decision = decide_best_candidate(ctx, c, p)
    best = decision.get("best")
    
    # Step 4: Write ReceiptMetadata if best candidate is from Places
    wrote_metadata = False
    if (
        write_metadata
        and best
        and best.get("source") == "places"
        and best.get("place_id")
    ):
        meta = metadata_from_places_candidate(key, best)
        dynamo.add_receipt_metadatas([meta])  # ← Writes to DynamoDB!
        wrote_metadata = True
    
    return {"context": {"key": key}, "decision": decision, "wrote_metadata": wrote_metadata}
```

### 5. Metadata Creation

**File**: `receipt_label/receipt_label/merchant_resolution/metadata.py` (lines 8-30)

```python
def metadata_from_places_candidate(key: Tuple[str, int], cand: Dict[str, Any]) -> ReceiptMetadata:
    """Convert a Places candidate dict into ReceiptMetadata entity."""
    img, rec = key
    
    return ReceiptMetadata(
        image_id=str(img),
        receipt_id=int(rec),
        place_id=cand.get("place_id", ""),           # From Google Places
        merchant_name=cand.get("name", ""),          # From Google Places
        matched_fields=[f"google_places_{cand.get('reason', '')}"],
        timestamp=datetime.now(timezone.utc),
        merchant_category="",
        address=cand.get("address", ""),             # From Google Places
        phone_number=cand.get("phone", ""),          # From Google Places
        validated_by=ValidationMethod.INFERENCE.value,
        reasoning=f"Selected via {cand.get('source', '')}:{cand.get('reason', '')}",
        canonical_place_id=cand.get("place_id", ""),
        canonical_merchant_name=cand.get("name", ""),
        canonical_address=cand.get("address", ""),
        canonical_phone_number=cand.get("phone", ""),
        validation_status="",
    )
```

## Key Points

### When ReceiptMetadata is Created
- **Trigger**: During OCR processing in `EmbeddingProcessor.process_embeddings()`
- **Condition**: Only when `write_metadata=True` and best candidate is from **Google Places API** (not ChromaDB)
- **Location**: In `resolve_receipt()` function

### What Data Sources
1. **ChromaDB**: Searches for similar receipts (has stored metadata from previous receipts)
2. **Google Places API**: Fallback if ChromaDB score < 0.5 threshold
   - Uses **receipt context** (merchant name, phone, address from OCR)
   - Performs **text search** against Google Places
   - Returns candidate with `place_id`, `name`, `address`, `phone`

### ReceiptMetadata Fields from Google Places
- `merchant_name` - From Google Places `name` field
- `phone_number` - From Google Places `phone` field
- `address` - From Google Places `address` field
- `place_id` - Google Places unique ID
- `matched_fields` - Which fields matched (e.g., `["google_places_name", "google_places_phone"]`)
- `validated_by` - `"INFERENCE"` (from `ValidationMethod.INFERENCE.value`)
- `reasoning` - Explanation of why this merchant was selected

### Validation Strategy

Now that we understand how `ReceiptMetadata` is created, we can validate it against **LangGraph labels**:

1. **ReceiptMetadata** = Google Places API result (canonical merchant info)
2. **LangGraph Labels** = OCR + LLM extraction (what's actually on the receipt)
3. **Comparison** = Validate that Places data matches receipt text

This is what our `phase1_validate_metadata` node does!

