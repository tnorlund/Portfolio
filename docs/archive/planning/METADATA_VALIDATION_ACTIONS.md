# ReceiptMetadata Validation Actions

## Problem Statement

The `merchant_name` field in ReceiptMetadata is the **most critical** field. If it doesn't match what's printed on the receipt, then the entire ReceiptMetadata record is likely **wrong** (wrong merchant was matched from Google Places).

## Current Behavior

When a merchant name mismatch is detected (similarity < 0.3), the validation:
1. Logs an error: `❌ MERCHANT_NAME CRITICAL MISMATCH`
2. Sets validation_status to `"CRITICAL_MISMATCH"`
3. Returns this in the validation results

## Validation Status Levels

- **`VALID`**: All fields match well (similarity ≥ 0.85)
- **`PARTIAL`**: Some fields match (similarity 0.60-0.84)
- **`INVALID`**: Low similarity (< 0.60)
- **`CRITICAL_MISMATCH`**: Merchant name doesn't match (similarity < 0.3) ⚠️
- **`NO_DATA`**: No comparable fields available

## Actions to Take

### When `validation_status == "CRITICAL_MISMATCH"`:

#### Option 1: Flag for Human Review (Current)
```python
# Mark ReceiptMetadata as needing review
metadata.validation_status = "NEEDS_REVIEW"
metadata.reasoning = f"Merchant name mismatch: receipt='{merchant_name_text}' metadata='{metadata.merchant_name}'"
client.update_receipt_metadata(metadata)
```

#### Option 2: Auto-Update (Risky but Fast)
```python
# Only if we're very confident (high LangGraph confidence)
if transaction_labels[merchant_index].confidence > 0.9:
    metadata.merchant_name = merchant_name_text  # Update with receipt text
    metadata.validation_status = "UPDATED_BY_VALIDATION"
    client.update_receipt_metadata(metadata)
```

#### Option 3: Mark as Incorrect (Safest)
```python
# Flag the metadata as incorrect
metadata.validation_status = "NO_MATCH"
metadata.reasoning = "Merchant validation failed - receipt text doesn't match metadata"
client.update_receipt_metadata(metadata)
```

## Recommended Approach

**For Production**:
1. **Don't auto-update** ReceiptMetadata (too risky)
2. **Flag for review** - set validation_status = "NEEDS_REVIEW"
3. **Store validation results** in state for debugging
4. **Alert monitoring** system about critical mismatches

**Implementation**:
```python
if validation_status == "CRITICAL_MISMATCH":
    # Get merchant name from validation results
    merchant_result = next(
        (m for m in validation_results["matches"] 
         if m["field"] == "merchant_name"), 
        None
    )
    
    if merchant_result:
        receipt_merchant = merchant_result["receipt_value"]
        metadata_merchant = merchant_result["metadata_value"]
        
        # Update ReceiptMetadata to flag for review
        metadata.validation_status = "NEEDS_REVIEW"
        metadata.reasoning = (
            f"CRITICAL: Merchant name mismatch detected. "
            f"Receipt: '{receipt_merchant}' Metadata: '{metadata_merchant}' "
            f"Requires manual review."
        )
        
        # Save update
        state.dynamo_client.update_receipt_metadata(metadata)
```

## Why This Matters

- **ReceiptMetadata** is used for:
  - Merchant clustering and deduplication
  - Building merchant-specific models
  - User expense tracking and categorization
- **Wrong merchant_name** means:
  - Expenses categorized under wrong merchant
  - Clustering groups wrong receipts together
  - Downstream models get wrong training data

## Monitoring

Track validation_status distribution:
- `VALID`: Green ✅
- `PARTIAL`: Yellow ⚠️
- `CRITICAL_MISMATCH`: Red 🚨 (requires immediate attention)

Set up alerts for:
- High `CRITICAL_MISMATCH` rate (> 5%)
- Individual critical mismatches in production logs

