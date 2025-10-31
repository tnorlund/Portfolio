# ReceiptMetadata Validation Strategy - Final

## The Problem

We have **two sources of truth** for merchant information:

1. **ReceiptMetadata** (created during OCR processing)
   - Source: Google Places API + ChromaDB similarity
   - Data: Canonical merchant info (merchant_name, phone_number, address)
   - Confidence: High (validated against Google Places database)

2. **Labeled Words** (extracted by LangGraph)
   - Source: OCR text + LLM extraction from receipt image
   - Data: What's actually printed on the receipt
   - Confidence: Medium (depends on OCR + LLM quality)

## The Goal

**Validate that ReceiptMetadata matches what's printed on the receipt.**

If they don't match, we need to decide:
- Is ReceiptMetadata wrong? (wrong merchant matched from Google Places)
- Are the labels wrong? (OCR/LLM extraction errors)
- Both?

## Validation Strategy

### Step 1: Extract Label Values

From **Phase 1 Context labels** (`transaction_labels`):

```python
# Find labels by type
merchant_name_label = find_label_by_type(transaction_labels, "MERCHANT_NAME")
phone_label = find_label_by_type(transaction_labels, "PHONE_NUMBER")
address_label = find_label_by_type(transaction_labels, "ADDRESS_LINE")

# Extract the actual text from the receipt
receipt_merchant_text = merchant_name_label["word_text"]  # e.g., "COSTCO"
receipt_phone_text = phone_label["word_text"]  # e.g., "(818) 597-3901"
receipt_address_text = address_label["word_text"]  # e.g., "5700 Lindero Canyon Rd"
```

### Step 2: Compare with ReceiptMetadata

```python
# Load existing ReceiptMetadata
metadata = state.receipt_metadata  # Created during OCR processing

# Compare
merchant_similarity = compare_merchant_names(receipt_merchant_text, metadata.merchant_name)
phone_similarity = compare_phone_numbers(receipt_phone_text, metadata.phone_number)
address_similarity = compare_addresses(receipt_address_text, metadata.address)
```

### Step 3: Interpret Results

#### Case A: All Similarities High ✅
```
merchant_similarity: 0.92
phone_similarity: 1.00
address_similarity: 0.78

Result: ReceiptMetadata is VALID (matches what's on receipt)
```

#### Case B: Partial Match ⚠️
```
merchant_similarity: 0.85
phone_similarity: 0.45  ← Low!
address_similarity: 0.72

Result: ReceiptMetadata is PARTIALLY VALID
  - Merchant name matches well
  - Phone number mismatch (maybe not printed on receipt?)
  - Address matches
```

#### Case C: Low Match ❌
```
merchant_similarity: 0.45  ← Very low!
phone_similarity: 0.30
address_similarity: 0.25

Result: ReceiptMetadata might be WRONG
  - Could be wrong merchant matched from Google Places
  - Should be flagged for human review
```

## Updated Validation Logic

Instead of just comparing strings, we should:

### 1. Use Label Word Mapping

Don't just use the first MERCHANT_NAME label - use **ALL words** that match:

```python
# Find ALL words labeled as MERCHANT_NAME
merchant_name_words = [
    w.word_text 
    for w in receipt_words 
    if has_label(w, "MERCHANT_NAME")  # Check word has this label
]

# Build full merchant name from receipt
receipt_merchant_name = " ".join(merchant_name_words)  # e.g., "COSTCO EWHOLESALE"
```

This is better than just `word_text` from the label because:
- MERCHANT_NAME might span multiple words ("COSTCO EWHOLESALE")
- We want the full extracted text, not just one word

### 2. Include Confidence Scores

Each label has a `confidence` score. Use this to weight comparisons:

```python
merchant_confidence = merchant_name_label["confidence"]  # e.g., 0.92
phone_confidence = phone_label["confidence"]  # e.g., 0.88

# Weight similarity by confidence
weighted_merchant_similarity = merchant_similarity * merchant_confidence
```

### 3. Handle Multiple Label Occurrences

Some fields might appear multiple times on the receipt:

```python
# DATE: line 30, line 45
# TIME: line 30, line 34, line 46

# For phone number, use the first high-confidence occurrence
phone_labels = [l for l in transaction_labels if l.label_type.value == "PHONE_NUMBER"]
best_phone = max(phone_labels, key=lambda l: l.confidence)
```

### 4. Report Validation Status

```python
validation_results = {
    "merchant_name": {
        "receipt_value": "COSTCO EWHOLESALE",
        "metadata_value": "Costco Wholesale",
        "similarity": 0.92,
        "confidence": 0.95,
        "status": "MATCH"
    },
    "phone_number": {
        "receipt_value": "(818) 597-3901",
        "metadata_value": "818-597-3901",
        "similarity": 1.00,
        "confidence": 0.88,
        "status": "MATCH"
    },
    "address": {
        "receipt_value": "5700 Lindero Canyon Rd",
        "metadata_value": "5700 Lindero Canyon Road",
        "similarity": 0.78,
        "confidence": 0.82,
        "status": "MATCH"
    },
    "overall_confidence": 0.92,  # Average of all weighted similarities
    "validation_status": "VALID"  # VALID, PARTIAL, INVALID
}
```

## Integration with State

### Add to CurrencyAnalysisState

```python
class CurrencyAnalysisState(BaseModel):
    # ... existing fields ...
    
    # NEW: Metadata validation
    metadata_validation: Optional[Dict[str, Any]] = None
    validation_conflicts: List[str] = Field(default_factory=list)
```

### Update Graph Flow

```
load_data → phase1_currency (parallel)
            → phase1_context (parallel)
            → validate_metadata (NEW - depends on both phase1_context AND receipt_metadata)
            → phase2_line_analysis
            → combine_results
```

**Validation node depends on**:
1. `transaction_labels` from Phase 1 Context
2. `receipt_metadata` loaded in initial state

## Actions Based on Validation

### If Validation Passes ✅
- Continue with LangGraph labeling
- Optional: Update ReceiptMetadata.validation_status = "MATCHED"
- Optional: Add validation_reasoning to ReceiptMetadata

### If Validation Partial ⚠️
- Continue but flag in state
- Log mismatches for review
- Optional: Set ReceiptMetadata.validation_status = "UNSURE"

### If Validation Fails ❌
- Flag for human review
- Don't fail the workflow
- Optional: Set ReceiptMetadata.validation_status = "NO_MATCH"

## Key Insight

**We're validating ReceiptMetadata against what's actually printed on the receipt.**

This catches issues like:
- Wrong merchant matched from Google Places
- OCR errors in metadata creation
- Receipt metadata from a different location/store

The validation gives us **confidence** that our ReceiptMetadata is correct before we use it for word-level labeling.

