# Metadata Validation Strategy

## Overview

We need to validate `ReceiptMetadata` (from Google Places API) against the labels extracted by our LangGraph workflow (Phase 1 Context). This ensures data quality and can flag mismatches.

## ReceiptMetadata Fields (from DynamoDB)

### Primary Fields (from Google Places API)
- `merchant_name` - Canonical name (e.g., "Starbucks")
- `phone_number` - Formatted phone (e.g., "(818) 597-3901")
- `address` - Full address line
- `merchant_category` - Business type (e.g., "Coffee Shop")
- `place_id` - Google Places API ID
- `matched_fields` - Fields that matched (e.g., ["name", "phone"])

### Validation Status Fields
- `validation_status` - "MATCHED", "UNSURE", or "NO_MATCH"
- `validated_by` - Source (e.g., "GPT+GooglePlaces")
- `reasoning` - Justification for the match

## Labels from Phase 1 Context

### Merchant Info Labels (that match ReceiptMetadata)
- **MERCHANT_NAME** - Store name from receipt text
- **PHONE_NUMBER** - Phone number from receipt text
- **ADDRESS_LINE** - Address from receipt text

### Transaction Labels (validation only)
- **DATE** - Transaction date
- **TIME** - Transaction time
- **PAYMENT_METHOD** - Payment type
- **LOYALTY_ID** - Member/rewards ID
- **COUPON** - Coupon codes
- **DISCOUNT** - Discount amounts
- **STORE_HOURS** - Business hours
- **WEBSITE** - Web address

## Validation Strategy

### Step 1: Create Validation Node (Phase 1.5)
After Phase 1 Context completes, validate the merchant info:

```python
async def validate_metadata(
    state: CurrencyAnalysisState
) -> dict:
    """Compare Phase 1 Context labels with ReceiptMetadata."""
    
    metadata = state.receipt_metadata
    transaction_labels = state.transaction_labels
    
    if not metadata:
        print("⚠️ No ReceiptMetadata available for validation")
        return {"metadata_validation": None}
    
    # Extract labels by type
    merchant_name_label = find_label(transaction_labels, "MERCHANT_NAME")
    phone_label = find_label(transaction_labels, "PHONE_NUMBER")
    address_label = find_label(transaction_labels, "ADDRESS_LINE")
    
    # Compare with metadata
    matches = []
    mismatches = []
    
    # Compare MERCHANT_NAME
    if merchant_name_label:
        similarity = calculate_similarity(
            merchant_name_label.word_text, 
            metadata.merchant_name
        )
        if similarity > 0.7:
            matches.append(("name", similarity))
        else:
            mismatches.append(("name", metadata.merchant_name, merchant_name_label.word_text))
    
    # Compare PHONE_NUMBER
    if phone_label:
        similarity = compare_phone_numbers(
            phone_label.word_text, 
            metadata.phone_number
        )
        if similarity > 0.8:
            matches.append(("phone", similarity))
        else:
            mismatches.append(("phone", metadata.phone_number, phone_label.word_text))
    
    # Compare ADDRESS_LINE
    if address_label:
        similarity = compare_addresses(
            address_label.word_text, 
            metadata.address
        )
        if similarity > 0.6:
            matches.append(("address", similarity))
        else:
            mismatches.append(("address", metadata.address, address_label.word_text))
    
    return {
        "metadata_validation": {
            "matches": matches,
            "mismatches": mismatches,
            "confidence": len(matches) / (len(matches) + len(mismatches)) if (matches + mismatches) else None,
        }
    }
```

### Step 2: Add Validation Fields to State

```python
class CurrencyAnalysisState(BaseModel):
    # ... existing fields ...
    
    # NEW: Metadata validation results
    metadata_validation: Optional[dict] = None
    validation_conflicts: List[str] = Field(default_factory=list)
```

### Step 3: Update Graph Flow

```
load_data → phase1_currency (parallel)
            → phase1_context (parallel)
            → validate_metadata (NEW - after phase1_context)
            → phase2_line_analysis
            → combine_results
```

## Comparison Logic

### 1. MERCHANT_NAME Comparison
- Extract text from receipt (OCR)
- Compare with ReceiptMetadata.merchant_name
- Use fuzzy matching (fuzzywuzzy or similar)
- Threshold: 70% similarity

```python
from difflib import SequenceMatcher

def compare_merchant_names(receipt_text: str, metadata_name: str) -> float:
    """Compare merchant names using sequence matching."""
    return SequenceMatcher(None, receipt_text.lower(), metadata_name.lower()).ratio()
```

### 2. PHONE_NUMBER Comparison
- Normalize both (remove formatting)
- Extract digits only
- Compare digit sequences
- Threshold: 80% similarity (allow for partial matches)

```python
def normalize_phone(phone: str) -> str:
    """Extract digits only."""
    return ''.join(filter(str.isdigit, phone))

def compare_phone_numbers(receipt_phone: str, metadata_phone: str) -> float:
    """Compare phone numbers by digit sequence."""
    receipt_digits = normalize_phone(receipt_phone)
    metadata_digits = normalize_phone(metadata_phone)
    
    # Check if one contains the other
    if receipt_digits in metadata_digits or metadata_digits in receipt_digits:
        return 1.0
    
    # Check sequence similarity
    return SequenceMatcher(None, receipt_digits, metadata_digits).ratio()
```

### 3. ADDRESS_LINE Comparison
- Tokenize both addresses
- Compare token overlap
- Use Jaccard similarity
- Threshold: 60% similarity (addresses can be formatted differently)

```python
def compare_addresses(receipt_address: str, metadata_address: str) -> float:
    """Compare addresses using token overlap."""
    receipt_tokens = set(receipt_address.lower().split())
    metadata_tokens = set(metadata_address.lower().split())
    
    intersection = receipt_tokens & metadata_tokens
    union = receipt_tokens | metadata_tokens
    
    return len(intersection) / len(union) if union else 0.0
```

## Expected Outcomes

### Case 1: All Fields Match ✅
```json
{
  "matches": [
    ["name", 0.92],
    ["phone", 1.0],
    ["address", 0.78]
  ],
  "mismatches": [],
  "confidence": 1.0
}
```
**Action**: Mark ReceiptMetadata as validated

### Case 2: Partial Match ⚠️
```json
{
  "matches": [["name", 0.85]],
  "mismatches": [
    ["phone", "(818) 597-3901", "Unknown"]
  ],
  "confidence": 0.33
}
```
**Action**: Flag for review, ReceiptMetadata may be partial

### Case 3: No Match ❌
```json
{
  "matches": [],
  "mismatches": [
    ["name", "Starbucks", "Costco"],
    ["phone", "(818) 597-3901", "No phone found"]
  ],
  "confidence": 0.0
}
```
**Action**: Flag as potentially wrong metadata

## Integration with Existing Flow

### Current Graph Structure
```
START → load_data → phase1_currency + phase1_context (parallel)
                    → validate_metadata (NEW)
                    → phase2_line_analysis
                    → combine_results → END
```

### Updated Flow with Validation
```python
def create_unified_analysis_graph(ollama_api_key: str, save_dev_state: bool = False):
    workflow = StateGraph(CurrencyAnalysisState)
    
    # ... existing nodes ...
    
    # NEW: Add validation node
    workflow.add_node("validate_metadata", validate_metadata_state)
    
    # Update edges
    workflow.add_edge("phase1_context", "validate_metadata")  # NEW
    workflow.add_edge("validate_metadata", "combine_results")  # NEW
    
    # ... rest of graph ...
```

## Validation Status Updates

After validation, we can optionally update ReceiptMetadata:

```python
def update_receipt_metadata_validation_status(
    client: DynamoClient,
    receipt_metadata: ReceiptMetadata,
    validation_results: dict
) -> None:
    """Update ReceiptMetadata validation_status based on label matches."""
    
    confidence = validation_results.get("confidence", 0.0)
    mismatches = validation_results.get("mismatches", [])
    
    if confidence >= 0.8:
        receipt_metadata.validation_status = "MATCHED"
    elif confidence >= 0.4:
        receipt_metadata.validation_status = "UNSURE"
    else:
        receipt_metadata.validation_status = "NO_MATCH"
    
    receipt_metadata.validated_by = "LangGraph+Labels"
    receipt_metadata.reasoning = f"Validation: {len(validation_results['matches'])}/{len(validation_results['matches']) + len(validation_results['mismatches'])} fields matched"
    
    client.update_receipt_metadata(receipt_metadata)
```

