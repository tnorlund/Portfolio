# Balance Strategies: Do You Need 1:1:1:1 Per Receipt?

## Short Answer: **No!**

You don't need 1:1:1:1 per receipt. You need **balanced training examples overall**, which can be achieved through several techniques.

## Why SROIE Has 1:1:1:1 Per Receipt

**SROIE's structure:**
- 4 labels total (Company, Date, Address, Total)
- Each receipt has exactly **one of each type**
- This creates natural 1:1:1:1 balance

**Your structure:**
- 7 labels total
- PRODUCT_NAME appears **many times per receipt** (multiple line items)
- PHONE_NUMBER appears **0-1 times per receipt**
- This creates natural imbalance

## The Real Goal: Balanced Training Examples

**What matters for training:**
- Not balance per receipt
- **Balance in the training dataset overall**
- Equal learning opportunities for each label class

## Solutions (Without Restructuring Data)

### Option 1: Class Weighting (Easiest) ⭐⭐⭐

**How it works:**
- Weight rare classes higher in loss function
- Model pays more attention to rare examples during training
- No data changes needed

**Implementation:**
```python
# Calculate class weights inversely proportional to frequency
class_weights = {
    "PHONE_NUMBER": 15.0,  # 15x more weight (inverse of 1/15 ratio)
    "DATE": 10.0,          # 10x more weight
    "MERCHANT_NAME": 7.0,  # 7x more weight
    "PRODUCT_NAME": 0.5,   # Less weight (most common)
}
```

**Expected Impact**: +1-3% F1

### Option 2: Focal Loss (Automatic Balancing) ⭐⭐⭐

**How it works:**
- Automatically down-weights easy examples (PRODUCT_NAME)
- Automatically up-weights hard examples (PHONE_NUMBER, DATE)
- No manual tuning needed

**Expected Impact**: +2-4% F1

### Option 3: Oversampling Rare Classes ⭐⭐

**How it works:**
- Duplicate training examples with rare labels
- Creates balanced dataset without changing receipt structure
- Can oversample at the word level or receipt level

**Expected Impact**: +1-3% F1

### Option 4: Reduce Label Count (Match SROIE Approach) ⭐⭐⭐

**How it works:**
- Merge DATE+TIME → reduces imbalance
- Merge ADDRESS+PHONE → reduces imbalance
- Fewer labels = naturally more balanced

**Expected Impact**: +5-10% F1 (biggest impact)

## Comparison: Per-Receipt vs Overall Balance

### SROIE Approach (1:1:1:1 per receipt)
- ✅ Natural balance
- ✅ Simple structure
- ❌ Requires restructuring your data
- ❌ Loses granularity (can't distinguish DATE from TIME)

### Your Approach (Imbalanced per receipt, balanced overall)
- ✅ Keep your data structure
- ✅ Use class weighting/focal loss
- ✅ Keep label granularity
- ⚠️ Requires balancing techniques

## Recommended Strategy

### Phase 1: Use Balancing Techniques (Keep Current Structure)

1. **Add class weighting** to loss function
   - Weight PHONE_NUMBER, DATE higher
   - Weight PRODUCT_NAME lower
   - **Expected**: 71-73% F1

2. **Or use focal loss** (automatic)
   - Handles imbalance automatically
   - **Expected**: 72-74% F1

3. **Oversample rare classes** (optional)
   - Duplicate PHONE_NUMBER, DATE examples
   - **Expected**: +1-2% F1

**Combined Expected**: 73-76% F1

### Phase 2: Reduce Label Count (If Needed)

If you want to match SROIE's 95% F1:
- Merge DATE+TIME → 6 labels
- Merge ADDRESS+PHONE → 5 labels
- **Expected**: 80-85% F1

**With balancing techniques**: 85-90% F1

## Why You Don't Need 1:1:1:1 Per Receipt

**Key insight:**
- Training happens at the **word/token level**, not receipt level
- What matters is: **equal learning opportunities per label class**
- This can be achieved through:
  - Class weighting (adjusts loss)
  - Focal loss (automatic balancing)
  - Oversampling (duplicates rare examples)
  - Undersampling (removes common examples)

**Example:**
- Receipt 1: 10 PRODUCT_NAME, 1 PHONE_NUMBER
- Receipt 2: 8 PRODUCT_NAME, 1 PHONE_NUMBER
- Receipt 3: 12 PRODUCT_NAME, 0 PHONE_NUMBER

**With class weighting:**
- PHONE_NUMBER gets 15x weight
- Each PHONE_NUMBER example counts as 15 examples
- Model learns PHONE_NUMBER just as well as PRODUCT_NAME

## Bottom Line

**You don't need to restructure your data to have 1:1:1:1 per receipt.**

**You can achieve balance through:**
1. **Class weighting** (easiest, recommended)
2. **Focal loss** (automatic, recommended)
3. **Oversampling** (simple, effective)

**These techniques will:**
- Balance training examples overall
- Give rare classes equal learning opportunities
- Improve F1 by 1-4%
- Keep your current data structure

**To reach 95% F1 like SROIE, you'd still need to:**
- Reduce label count to 4-5 (match SROIE's simplicity)
- Add balancing techniques
- Upgrade to LayoutLMv2

But you don't need to restructure your receipts to have exactly one of each label type per receipt!

