# Label Selection Recommendation: Which Labels to Train On?

## Current Training Labels (7 labels)

- MERCHANT_NAME: 647 VALID
- PHONE_NUMBER: 301 VALID
- ADDRESS_LINE: 1,394 VALID
- DATE: 435 VALID
- TIME: 556 VALID
- PRODUCT_NAME: 4,534 VALID
- AMOUNT (merged): ~2,815 VALID (LINE_TOTAL: 1,565 + SUBTOTAL: 235 + TAX: 271 + GRAND_TOTAL: 744)

**Current F1: 70%**

## Analysis of All Available Labels

### High-Quality Labels (High VALID, Low INVALID/NEEDS_REVIEW)

| Label | VALID | INVALID | NEEDS_REVIEW | Quality Score | Recommendation |
|-------|-------|---------|--------------|---------------|----------------|
| **PRODUCT_NAME** | 4,534 | 359 | 40 | **92.6%** | ✅ Keep (essential) |
| **LINE_TOTAL** | 1,565 | 441 | 0 | 78.0% | ✅ In AMOUNT merge |
| **ADDRESS_LINE** | 1,394 | 545 | 1,365 | 42.3%* | ⚠️ Keep but review |
| **QUANTITY** | 781 | 111 | 0 | **87.6%** | ✅ Consider adding |
| **UNIT_PRICE** | 953 | 551 | 0 | 63.3% | ⚠️ Consider adding |
| **GRAND_TOTAL** | 744 | 254 | 0 | 74.5% | ✅ In AMOUNT merge |
| **MERCHANT_NAME** | 647 | 346 | 798 | 36.2%* | ⚠️ Keep but review |
| **TIME** | 556 | 251 | 0 | **68.9%** | ✅ Keep |
| **LOYALTY_ID** | 520 | 198 | 178 | 58.0% | ⚠️ Optional |
| **PAYMENT_METHOD** | 517 | 129 | 625 | 40.7%* | ⚠️ Optional |
| **DATE** | 435 | 450 | 0 | 49.1% | ⚠️ Keep (low quality) |
| **TAX** | 271 | 263 | 0 | 50.8% | ✅ In AMOUNT merge |
| **SUBTOTAL** | 235 | 394 | 0 | 37.4% | ✅ In AMOUNT merge |
| **PHONE_NUMBER** | 301 | 384 | 0 | 43.9% | ⚠️ Keep (rare) |

*Quality score low due to high NEEDS_REVIEW count (could be resolved)

### Low-Quality Labels (Low VALID, High INVALID/NEEDS_REVIEW)

| Label | VALID | INVALID | NEEDS_REVIEW | Recommendation |
|-------|-------|---------|--------------|----------------|
| **DISCOUNT** | 216 | 206 | 519 | ❌ Skip (low quality) |
| **WEBSITE** | 218 | 52 | 537 | ❌ Skip (low quality) |
| **COUPON** | 103 | 182 | 77 | ❌ Skip (too few) |
| **STORE_HOURS** | 76 | 41 | 649 | ❌ Skip (too few) |

## Recommendation: Three Options

### Option 1: Match SROIE Exactly (4 labels) - **Highest F1 Potential** ⭐⭐⭐

**Labels:**
1. **MERCHANT_NAME** (647 VALID) - Company
2. **DATE** (435 VALID) - Merge with TIME → **991 VALID** (DATE+TIME)
3. **ADDRESS_LINE** (1,394 VALID) - Merge with PHONE_NUMBER → **1,695 VALID** (ADDRESS+PHONE)
4. **AMOUNT** (2,815 VALID) - Already merged (LINE_TOTAL + SUBTOTAL + TAX + GRAND_TOTAL)

**Expected F1: 85-95%** (matches SROIE approach)

**Pros:**
- Matches research benchmark exactly
- Highest F1 potential
- Simplest model
- Fastest training

**Cons:**
- Loses granularity (can't distinguish DATE from TIME)
- Loses PHONE_NUMBER as separate label
- Loses PRODUCT_NAME (but you might not need it for basic extraction)

### Option 2: SROIE + Product Info (5 labels) - **Balanced** ⭐⭐

**Labels:**
1. **MERCHANT_NAME** (647 VALID)
2. **DATE** (991 VALID) - Merge DATE + TIME
3. **ADDRESS_LINE** (1,695 VALID) - Merge ADDRESS_LINE + PHONE_NUMBER
4. **AMOUNT** (2,815 VALID) - Already merged
5. **PRODUCT_NAME** (4,534 VALID) - Keep for line items

**Expected F1: 80-90%**

**Pros:**
- Still simple (5 labels)
- Keeps PRODUCT_NAME (useful for line items)
- Good balance

**Cons:**
- Still loses DATE/TIME distinction
- Still loses PHONE_NUMBER as separate label

### Option 3: Current + Quality Improvements (7-8 labels) - **Most Granular** ⭐

**Labels:**
1. **MERCHANT_NAME** (647 VALID) - Keep
2. **PHONE_NUMBER** (301 VALID) - Keep (rare but useful)
3. **ADDRESS_LINE** (1,394 VALID) - Keep
4. **DATE** (435 VALID) - Keep
5. **TIME** (556 VALID) - Keep
6. **PRODUCT_NAME** (4,534 VALID) - Keep
7. **AMOUNT** (2,815 VALID) - Already merged
8. **QUANTITY** (781 VALID) - **Add** (high quality, 87.6% valid)

**Expected F1: 70-80%** (with class weighting/focal loss)

**Pros:**
- Most granular information
- Keeps all useful labels
- Can add QUANTITY for line items

**Cons:**
- More complex (7-8 labels)
- Lower F1 potential
- Requires class balancing techniques

## My Recommendation: **Option 2 (SROIE + Product Info - 5 labels)**

### Why Option 2?

1. **Best balance** between simplicity and usefulness
2. **High F1 potential** (80-90%) - close to SROIE's 95%
3. **Keeps PRODUCT_NAME** - essential for line items
4. **Reduces complexity** from 7 to 5 labels
5. **Better balance** - reduces 15:1 ratio to ~4:1 (PRODUCT_NAME vs MERCHANT_NAME)

### Implementation

**Merge DATE + TIME:**
```python
# In data_loader.py, add to merge logic
if label in ["DATE", "TIME"]:
    return "DATE_TIME"  # or just "DATE"
```

**Merge ADDRESS_LINE + PHONE_NUMBER:**
```python
# In data_loader.py, add to merge logic
if label in ["ADDRESS_LINE", "PHONE_NUMBER"]:
    return "ADDRESS"  # or "CONTACT_INFO"
```

**Final 5 labels:**
1. MERCHANT_NAME
2. DATE (merged from DATE + TIME)
3. ADDRESS (merged from ADDRESS_LINE + PHONE_NUMBER)
4. AMOUNT (already merged)
5. PRODUCT_NAME

## Expected Results

**Current (7 labels):**
- F1: 70%
- Imbalance: 15:1 (PRODUCT_NAME vs PHONE_NUMBER)

**Option 2 (5 labels):**
- Expected F1: **80-90%**
- Imbalance: ~4:1 (PRODUCT_NAME vs MERCHANT_NAME)
- Much more balanced!

**With class weighting/focal loss:**
- Expected F1: **85-92%**

**With LayoutLMv2:**
- Expected F1: **88-95%** ✅

## Action Plan

### Step 1: Implement Option 2 (5 labels)

1. **Merge DATE + TIME** → "DATE"
2. **Merge ADDRESS_LINE + PHONE_NUMBER** → "ADDRESS"
3. **Keep MERCHANT_NAME, AMOUNT, PRODUCT_NAME**

### Step 2: Add Class Weighting

- Weight MERCHANT_NAME higher (it's now the rarest)
- Weight PRODUCT_NAME lower (it's still most common)

### Step 3: Test and Compare

- Compare F1 to current 7-label setup
- Should see significant improvement

## Alternative: Option 1 (4 labels) if You Don't Need PRODUCT_NAME

If you don't need line item extraction (PRODUCT_NAME), **Option 1 (4 labels)** would give you the highest F1 (85-95%), matching SROIE exactly.

## Conclusion

**I recommend Option 2 (5 labels):**
- MERCHANT_NAME
- DATE (merged from DATE + TIME)
- ADDRESS (merged from ADDRESS_LINE + PHONE_NUMBER)
- AMOUNT (already merged)
- PRODUCT_NAME

This gives you:
- **80-90% F1** (vs current 70%)
- **Better balance** (4:1 vs 15:1)
- **Still useful** (keeps PRODUCT_NAME for line items)
- **Path to 95%** with LayoutLMv2 + class weighting

Should I implement Option 2?

