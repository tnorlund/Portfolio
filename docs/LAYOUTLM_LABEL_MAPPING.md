# LayoutLM Label Mapping Documentation

## Overview

The LayoutLM model is trained on a simplified 4-label system, but the original database contains more granular CORE_LABELS. This document explains the mapping between original labels and the simplified labels used for training and inference.

## Label Mapping

### Original CORE_LABELS

The database stores labels from the following CORE_LABELS set:

**Merchant & Store Info:**
- `MERCHANT_NAME` - Trading name or brand of the store
- `STORE_HOURS` - Printed business hours
- `PHONE_NUMBER` - Telephone number
- `WEBSITE` - Web or email address
- `LOYALTY_ID` - Customer loyalty identifier

**Location:**
- `ADDRESS_LINE` - Full address line

**Transaction Info:**
- `DATE` - Calendar date of the transaction
- `TIME` - Time of the transaction
- `PAYMENT_METHOD` - Payment instrument
- `COUPON` - Coupon code
- `DISCOUNT` - Discount line item

**Line Items:**
- `PRODUCT_NAME` - Product description
- `QUANTITY` - Item count or weight
- `UNIT_PRICE` - Price per unit

**Totals:**
- `LINE_TOTAL` - Extended price for a line
- `SUBTOTAL` - Sum before tax and discounts
- `TAX` - Tax line
- `GRAND_TOTAL` - Final amount due

### Simplified 4-Label System

For training, these labels are normalized to 4 simplified labels:

| Simplified Label | Original Labels (Merged) | Description |
|-----------------|-------------------------|-------------|
| `MERCHANT_NAME` | `MERCHANT_NAME` | Store or business name (unchanged) |
| `DATE` | `DATE`, `TIME` | Transaction date and time (merged) |
| `ADDRESS` | `ADDRESS_LINE`, `PHONE_NUMBER` | Address and phone number (merged) |
| `AMOUNT` | `LINE_TOTAL`, `SUBTOTAL`, `TAX`, `GRAND_TOTAL` | All currency/amount values (merged) |

**Labels Filtered Out:**
- Labels not in the 4-label system are mapped to `O` (Other) during training
- Examples: `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, `STORE_HOURS`, `WEBSITE`, `LOYALTY_ID`, `PAYMENT_METHOD`, `COUPON`, `DISCOUNT`

## API Response Structure

The LayoutLM inference API returns predictions with the following label fields:

### Prediction Fields

```typescript
{
  // BIO labels (for correctness checking)
  predicted_label: "B-MERCHANT_NAME",           // BIO format from model
  ground_truth_label: "B-MERCHANT_NAME",        // BIO format (normalized)

  // Base labels (normalized to 4-label system)
  predicted_label_base: "MERCHANT_NAME",        // Clean label for display
  ground_truth_label_base: "MERCHANT_NAME",     // Normalized ground truth

  // Original ground truth label (from CORE_LABELS)
  ground_truth_label_original: "PHONE_NUMBER",  // Original label from database
}
```

### Example Mappings

**Example 1: Phone Number**
- Original: `PHONE_NUMBER`
- Normalized: `ADDRESS`
- Model Prediction: `ADDRESS`
- Result: ✓ Correct (model correctly maps phone number to address category)

**Example 2: Time**
- Original: `TIME`
- Normalized: `DATE`
- Model Prediction: `DATE`
- Result: ✓ Correct (model correctly maps time to date category)

**Example 3: Tax**
- Original: `TAX`
- Normalized: `AMOUNT`
- Model Prediction: `AMOUNT`
- Result: ✓ Correct (model correctly maps tax to amount category)

## Why This Mapping?

### Training Benefits

1. **Simpler Model**: 4 labels are easier to learn than 15+ labels
2. **Better Accuracy**: Research shows 4-label systems achieve 95%+ F1 scores (SROIE dataset)
3. **Reduced Confusion**: Fewer similar labels means less ambiguity
4. **Better Balance**: More balanced label distribution

### Trade-offs

- **Loss of Granularity**: Can't distinguish between `PHONE_NUMBER` and `ADDRESS_LINE`
- **Loss of Detail**: Can't distinguish between `DATE` and `TIME`
- **Amount Types**: Can't distinguish between `SUBTOTAL`, `TAX`, and `GRAND_TOTAL`

## Visualization

The visualization component shows:

1. **Original Ground Truth**: The actual label from the database (e.g., `PHONE_NUMBER`)
2. **Normalized Ground Truth**: How it maps to the 4-label system (e.g., `ADDRESS`)
3. **Model Prediction**: What the model predicted (e.g., `ADDRESS`)

This helps users understand:
- What was actually labeled in the database
- How labels are normalized for training
- Whether the model's prediction is correct (even if the original label differs)

## Implementation

The normalization logic is in:
- `receipt_layoutlm/receipt_layoutlm/data_loader.py` - `_normalize_word_label()`
- `infra/routes/layoutlm_inference_cache_generator/lambdas/index.py` - `_normalize_label_for_4label_setup()`

Both functions apply the same mapping:
- `TIME` → `DATE`
- `PHONE_NUMBER` → `ADDRESS`
- `ADDRESS_LINE` → `ADDRESS`
- `LINE_TOTAL`, `SUBTOTAL`, `TAX`, `GRAND_TOTAL` → `AMOUNT`
- All other labels → `O` (if not in allowed set)

