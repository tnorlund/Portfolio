# LayoutLM AMOUNT Label Merging Analysis

## Problem Statement

The current training setup merges all amount-related labels into a single `AMOUNT` label:
- `LINE_TOTAL` (line item prices)
- `GRAND_TOTAL` (final receipt total)
- `SUBTOTAL` (receipt subtotal)
- `TAX` (tax amount)

This merging is causing class imbalance issues and precision problems.

## Label Distribution Analysis

### Current AMOUNT Breakdown (After Merging)

| Label | Count | Percentage |
|-------|-------|------------|
| LINE_TOTAL | 1,565 | 55.6% |
| GRAND_TOTAL | 744 | 26.4% |
| TAX | 271 | 9.6% |
| SUBTOTAL | 235 | 8.3% |
| **Total AMOUNT** | **2,815** | **100%** |

### Full 4-Label Distribution

| Label | Count | Percentage | Notes |
|-------|-------|------------|-------|
| AMOUNT | 2,815 | 45.8% | Most common (dominated by LINE_TOTAL) |
| ADDRESS | 1,695 | 27.6% | Second most common |
| DATE | 991 | 16.1% | Moderate frequency |
| MERCHANT_NAME | 647 | 10.5% | Least common |

**Imbalance Ratio**: 4.35x (AMOUNT has 4.35x more examples than MERCHANT_NAME)

## The Problem

### 1. Semantic Mismatch

`LINE_TOTAL` and receipt totals (`GRAND_TOTAL`, `SUBTOTAL`, `TAX`) are semantically different:

- **LINE_TOTAL**: Individual item prices
  - Appears on every line item
  - Context: Middle of receipt, next to product names
  - Format: Usually smaller, inline with items
  - Frequency: Very high (every line item has one)

- **Receipt Totals** (GRAND_TOTAL, SUBTOTAL, TAX):
  - Appear once per receipt
  - Context: Bottom of receipt, summary section
  - Format: Often larger, bold, separated
  - Frequency: Low (3-4 per receipt total)

### 2. Class Imbalance Impact

The model is learning the prior distribution:
- "55.6% of AMOUNT labels are LINE_TOTAL" → predicts AMOUNT frequently
- "45.8% of all entities are AMOUNT" → predicts AMOUNT very often
- Result: **High recall (90-92%) but low precision (60-63%)**

### 3. Training Metrics Evidence

From training run `receipts-2025-11-13-0551-4label-sroie-lr3e5`:

- **Best F1**: 0.7453 (epoch 7)
- **Precision**: 0.6261 (62.6%)
- **Recall**: 0.9205 (92.1%)

The high recall but low precision indicates the model is:
- ✅ Finding most entities (high recall)
- ❌ Predicting entities that don't exist (low precision, false positives)

### 4. API Cache Evidence

From the inference cache analysis:
- **32 false positives for B-AMOUNT** (most common label)
- **7 false positives for B-ADDRESS** (second most common)
- Model is over-predicting the most common labels

## Solutions

### Option 1: Increase O:Entity Ratio (Quick Fix)

**Pros:**
- Simple change (just update CLI flag)
- Keeps 4-label setup (matches SROIE research)
- Quick to test

**Cons:**
- Doesn't address semantic mismatch
- May not fully solve the problem

**Implementation:**
```bash
--o-entity-ratio 2.5  # or 3.0 (currently 2.0)
```

This penalizes false positives more, which should improve precision.

### Option 2: Separate LINE_TOTAL from Receipt Totals (Recommended)

**Pros:**
- Addresses semantic mismatch
- More accurate label structure
- Better matches actual receipt structure
- Should improve precision significantly

**Cons:**
- Changes to 5 labels (instead of 4)
- Requires code changes to training/inference
- Need to update cache generator normalization

**Implementation:**

Separate labels:
- `LINE_TOTAL`: Keep as separate label (or merge with `UNIT_PRICE`?)
- `TOTAL`: Merge `GRAND_TOTAL` + `SUBTOTAL` + `TAX`

Result: 5 labels instead of 4:
1. MERCHANT_NAME
2. DATE
3. ADDRESS
4. LINE_TOTAL (or UNIT_PRICE)
5. TOTAL

**Code Changes Needed:**
1. Update `_normalize_label_for_4label_setup()` in cache generator
2. Update training data loader normalization
3. Update allowed labels list
4. Update inference normalization

### Option 3: Use Class Weights (Advanced)

**Pros:**
- Keeps 4-label setup
- Addresses class imbalance directly
- More fine-grained control

**Cons:**
- More complex to implement
- Requires tuning weights
- May not address semantic mismatch

**Implementation:**
- Weight `LINE_TOTAL` lower (it's too common)
- Weight `TOTAL` (GRAND_TOTAL/SUBTOTAL/TAX) higher (less common)
- Requires changes to loss function

## Recommendation

### Phase 1: Quick Test (Next Run)
Try increasing O:entity ratio first:
```bash
--o-entity-ratio 2.5
```

This is a quick test to see if penalizing false positives helps.

### Phase 2: Structural Fix (If Needed)
If precision doesn't improve enough, implement Option 2:
- Separate `LINE_TOTAL` from receipt totals
- Update normalization code in training and inference
- Retrain with 5-label setup

## Expected Impact

### If We Separate LINE_TOTAL:

**New Label Distribution:**
- LINE_TOTAL: 1,565 labels (25.5%)
- TOTAL: 1,250 labels (20.4%)
- ADDRESS: 1,695 labels (27.6%)
- DATE: 991 labels (16.1%)
- MERCHANT_NAME: 647 labels (10.5%)

**Benefits:**
- More balanced distribution (no single label > 50%)
- Better semantic accuracy
- Model can learn different contexts (line items vs receipt totals)
- Should improve precision significantly

**Trade-offs:**
- 5 labels instead of 4 (slightly more complex)
- Doesn't match SROIE's 4-label setup exactly
- But SROIE doesn't have line items, so this is more accurate for our use case

## Related Issues

- **Class Imbalance**: 4.35x ratio between most/least common labels
- **High Recall, Low Precision**: Model is too aggressive in predictions
- **False Positives**: Especially for AMOUNT and ADDRESS (most common labels)

## References

- Training run: `receipts-2025-11-13-0551-4label-sroie-lr3e5`
- Best F1: 0.7453 (epoch 7)
- Precision: 0.6261, Recall: 0.9205
- Label statistics from DynamoDB query

## Next Steps

1. ✅ Document the problem (this file)
2. ⏭️ Test with increased O:entity ratio (2.5 or 3.0)
3. ⏭️ If needed, implement LINE_TOTAL separation
4. ⏭️ Update cache generator normalization to match
5. ⏭️ Retrain and compare results

