# ReceiptMetadata Validation Review

## How ReceiptMetadata is Loaded

### Current Implementation

**Location**: `receipt_label/receipt_label/langchain/currency_validation.py` (lines 666-670)

```python
# Load ReceiptMetadata for merchant context
receipt_metadata = None
try:
    receipt_metadata = client.get_receipt_metadata(image_id, receipt_id)
    print(f"   📋 Loaded ReceiptMetadata: {receipt_metadata.merchant_name}")
except Exception as e:
    print(f"   ⚠️ Could not load ReceiptMetadata: {e}")
    # Continue without metadata - not critical
```

**Source**: DynamoDB table via `client.get_receipt_metadata()`

### ReceiptMetadata Fields

**Primary fields** (from Google Places API):
- `merchant_name` - Canonical business name (e.g., "Costco Wholesale")
- `phone_number` - Formatted phone (e.g., "(818) 597-3901")
- `address` - Full address line (e.g., "5700 Lindero Canyon Road")
- `merchant_category` - Business type (e.g., "Warehouse Club")
- `place_id` - Google Places API ID
- `matched_fields` - List of fields that matched (e.g., ["name", "phone"])

**Validation fields**:
- `validation_status` - "MATCHED", "UNSURE", or "NO_MATCH"
- `validated_by` - Source (e.g., "GPT+GooglePlaces")
- `reasoning` - Justification for the match
- `timestamp` - When record was created

**Canonical fields** (from clustering):
- `canonical_merchant_name` - From most representative business
- `canonical_address` - From most representative business
- `canonical_phone_number` - From most representative business

### Labels from Phase 1 Context

**Merchant Info Labels** (that match ReceiptMetadata):
1. **MERCHANT_NAME** - Store name from receipt text (e.g., "COSTCO")
2. **PHONE_NUMBER** - Phone number from receipt text (e.g., "(818) 597-3901")
3. **ADDRESS_LINE** - Address from receipt text (e.g., "5700 Lindero Canyon Rd")

**Transaction Labels** (for reference):
4. **DATE** - Transaction date
5. **TIME** - Transaction time
6. **PAYMENT_METHOD** - Payment type
7. **LOYALTY_ID** - Member/rewards ID
8. **COUPON** - Coupon codes
9. **DISCOUNT** - Discount amounts
10. **STORE_HOURS** - Business hours
11. **WEBSITE** - Web address

## Validation Strategy

### Goal
Validate that the `ReceiptMetadata` (from Google Places API) matches what's actually printed on the receipt (from OCR + LangGraph labels).

### Why This Matters
- **Data Quality**: Ensure ReceiptMetadata is actually correct
- **Mismatch Detection**: Flag potentially wrong metadata
- **Quality Control**: Track validation confidence across receipts

### Comparison Logic

#### 1. MERCHANT_NAME Comparison
- **Method**: Sequence matching (SequenceMatcher from difflib)
- **Threshold**: 70% similarity
- **Example**:
  - Receipt: "COSTCO"
  - Metadata: "Costco Wholesale"
  - Similarity: 55% (below threshold - would be flagged)

#### 2. PHONE_NUMBER Comparison
- **Method**: Digit-only comparison
- **Threshold**: 80% similarity
- **Normalization**: Extract digits only (e.g., "(818) 597-3901" → "8185973901")
- **Example**:
  - Receipt: "(818) 597-3901"
  - Metadata: "818-597-3901"
  - Similarity: 100% (match)

#### 3. ADDRESS_LINE Comparison
- **Method**: Token overlap (Jaccard similarity)
- **Threshold**: 60% similarity
- **Normalization**: Split into tokens, compare sets
- **Example**:
  - Receipt: "5700 Lindero Canyon Rd"
  - Metadata: "5700 Lindero Canyon Road"
  - Similarity: 60% (match)

## Test Results

### Function Testing
```bash
MERCHANT_NAME test:
  COSTCO vs Costco Wholesale: 0.55 (below threshold)

PHONE_NUMBER test:
  (818) 597-3901 vs 818-597-3901: 1.00 (perfect match)

ADDRESS test:
  5700 Lindero Canyon Rd vs 5700 Lindero Canyon Road: 0.60 (matches threshold)
```

### Expected Behavior
- **Perfect Match** (1.0): Both values identical
- **High Match** (0.8+): Very similar values
- **Low Match** (0.6-0.8): Similar but different formatting
- **Mismatch** (< 0.6): Potentially wrong metadata

## Implementation Status

### ✅ Created
- `receipt_label/receipt_label/langchain/nodes/phase1_validate.py` - Validation node

### ⏳ Next Steps
1. Add `metadata_validation` field to `CurrencyAnalysisState`
2. Integrate validation node into graph flow
3. Test with actual receipt data
4. Add optional ReceiptMetadata status updates

### Graph Flow Update

**Current**:
```
load_data → phase1_currency (parallel)
            → phase1_context (parallel)
            → phase2_line_analysis
            → combine_results
```

**With Validation**:
```
load_data → phase1_currency (parallel)
            → phase1_context (parallel)
            → validate_metadata (NEW)
            → phase2_line_analysis
            → combine_results
```

## Next Actions

1. ✅ Review how ReceiptMetadata is loaded
2. ✅ Create validation comparison functions
3. ⏳ Add validation node to graph flow
4. ⏳ Update state model with validation fields
5. ⏳ Test with actual receipts

