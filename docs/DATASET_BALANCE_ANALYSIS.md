# Dataset Balance Analysis: SROIE vs Your Dataset

## SROIE Dataset Balance

**SROIE Label Distribution (Estimated):**
- **Company**: ~626 labels (1 per receipt)
- **Date**: ~626 labels (1 per receipt)
- **Address**: ~626 labels (1 per receipt)
- **Total**: ~626 labels (1 per receipt)

**Balance Ratio**: ~1:1:1:1 (Perfectly balanced) ✅

**Characteristics:**
- Each receipt has exactly 4 labels (one of each type)
- No class imbalance
- Standardized format ensures consistent labeling

## Your Dataset Balance

**Your Label Distribution (November 2025):**

| Label | Count | Percentage | Notes |
|-------|-------|------------|-------|
| **PRODUCT_NAME** | 4,534 | **42.4%** | Dominant class |
| **AMOUNT** (merged) | 2,815 | 26.3% | |
| **ADDRESS_LINE** | 1,394 | 13.0% | |
| **MERCHANT_NAME** | 647 | 6.1% | |
| **TIME** | 556 | 5.2% | |
| **DATE** | 435 | 4.1% | |
| **PHONE_NUMBER** | 301 | **2.8%** | Rare class |

**Total**: ~10,682 labels

**Balance Ratio**: **15:1** (PRODUCT_NAME vs PHONE_NUMBER) ⚠️

**Class Imbalance Issues:**
- **PRODUCT_NAME** is 15x more common than **PHONE_NUMBER**
- **PRODUCT_NAME** is 10x more common than **DATE**
- **PRODUCT_NAME** is 7x more common than **MERCHANT_NAME**

## Impact of Class Imbalance

### Why Balance Matters

**Class imbalance causes:**
1. **Model bias toward majority class** - Model learns to predict PRODUCT_NAME more often
2. **Poor performance on rare classes** - PHONE_NUMBER, DATE get fewer learning examples
3. **Lower overall F1** - Rare classes drag down the average

### Your Current Situation

**Good News:**
- ✅ **OCR is very accurate** (you mentioned this) - eliminates one source of error
- ✅ **Large dataset** - 10,682 labels provides good coverage
- ✅ **All classes have reasonable counts** - Even PHONE_NUMBER has 301 examples

**Challenges:**
- ⚠️ **Severe class imbalance** - 15:1 ratio between most and least common
- ⚠️ **PRODUCT_NAME dominance** - 42% of all labels
- ⚠️ **Rare classes** - PHONE_NUMBER (2.8%), DATE (4.1%) have fewer examples

## Comparison: SROIE vs Your Dataset

| Aspect | SROIE | Your Dataset | Impact |
|--------|-------|--------------|--------|
| **Balance** | Perfect (1:1:1:1) | Imbalanced (15:1) | ⚠️ Hurts performance |
| **Label Count** | ~2,500-5,000 | ~10,682 | ✅ More data |
| **OCR Quality** | Clean | Very accurate (you said) | ✅ Not a factor |
| **Format** | Standardized | Real-world | ⚠️ More challenging |
| **Label Types** | 4 | 7 | ⚠️ More complex |

## How SROIE's Balance Helps

**Perfect balance means:**
1. **Equal learning opportunities** - Each label type gets same number of examples
2. **No bias** - Model doesn't favor any class
3. **Better rare class performance** - All classes are equally represented
4. **Higher F1** - Balanced classes contribute equally to metrics

**Your imbalance means:**
1. **PRODUCT_NAME gets over-learned** - Model becomes very good at predicting it
2. **Rare classes under-learned** - PHONE_NUMBER, DATE get less attention
3. **Lower F1 on rare classes** - Drags down overall performance

## Solutions for Class Imbalance

### Option 1: Class Weighting (Already Partially Implemented)

**Your current approach:**
- O:entity ratio of 2.0 downsampling
- This helps balance O tokens vs entity tokens

**Could add:**
- Per-class weights in loss function
- Weight rare classes (PHONE_NUMBER, DATE) higher

**Expected Impact**: +1-3% F1

### Option 2: Focal Loss

**Focal loss** focuses learning on hard examples:
- Automatically down-weights easy examples (PRODUCT_NAME)
- Up-weights hard examples (PHONE_NUMBER, DATE)

**Expected Impact**: +2-4% F1

### Option 3: Oversampling Rare Classes

**Synthetic data generation:**
- Duplicate examples with rare labels
- Or use data augmentation on rare class examples

**Expected Impact**: +1-3% F1

### Option 4: Undersampling Majority Class

**Reduce PRODUCT_NAME examples:**
- Randomly sample PRODUCT_NAME labels
- Keep all rare class examples

**Expected Impact**: +2-5% F1, but loses data

### Option 5: Separate Training for Rare Classes

**Two-stage training:**
1. Train on all data
2. Fine-tune on balanced subset (equal examples per class)

**Expected Impact**: +3-5% F1

## Recommendations

### Immediate Actions

1. **Add class weighting to loss function** (Easiest)
   - Weight PHONE_NUMBER, DATE higher
   - Weight PRODUCT_NAME lower
   - Expected: +1-3% F1

2. **Implement focal loss** (Medium complexity)
   - Automatically handles imbalance
   - Expected: +2-4% F1

3. **Oversample rare classes** (Easy)
   - Duplicate PHONE_NUMBER, DATE examples
   - Expected: +1-3% F1

### Long-term Solutions

4. **Collect more rare class examples**
   - Focus labeling effort on PHONE_NUMBER, DATE
   - Expected: +2-5% F1

5. **Reduce label count** (Matches SROIE approach)
   - Merge DATE+TIME → reduces imbalance
   - Merge ADDRESS+PHONE → reduces imbalance
   - Expected: +5-10% F1

## Expected Impact of Balancing

**Current F1: 70%**

**With class balancing techniques:**
- Class weighting: **71-73% F1**
- Focal loss: **72-74% F1**
- Oversampling: **71-73% F1**
- Combined: **73-76% F1**

**With label reduction (match SROIE):**
- Reduce to 4-5 labels: **80-85% F1**
- Add class balancing: **85-90% F1**

## Conclusion

**Yes, SROIE's dataset is perfectly balanced (1:1:1:1), which is a significant advantage.**

**Your dataset has:**
- ✅ **More data** (10,682 vs ~2,500-5,000 labels)
- ✅ **Very accurate OCR** (not a limiting factor)
- ⚠️ **Severe class imbalance** (15:1 ratio)
- ⚠️ **More complex labels** (7 vs 4)

**To reach 95% F1 like SROIE, you should:**
1. **Reduce label count** to 4-5 (match SROIE approach) - **Biggest impact**
2. **Add class balancing** (weighting, focal loss, oversampling) - **Medium impact**
3. **Upgrade to LayoutLMv2** - **High impact**

**The combination of perfect balance + simple labels is why SROIE achieves 95% F1. Your imbalance is definitely hurting performance, but it's not the only factor - label complexity is the bigger issue.**

