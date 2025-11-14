# Path to 95% F1 Score: What It Would Take

## The 95% Benchmark: SROIE Dataset

**SROIE Results:**
- **F1 Score: 95.24% - 96.04%**
- **Labels: Only 4 entity types**
  - Company (merchant name)
  - Date
  - Address
  - Total (amount)
- **Dataset**: Standardized receipt format, clean OCR

**Your Current Setup:**
- **F1 Score: 0.70 (70%)**
- **Labels: 7 entity types** (75% more labels!)
- **Dataset**: Real-world receipts with varying formats

## Key Differences: Why SROIE Gets 95%

### 1. **Simpler Label Set (4 vs 7 labels)**
- **SROIE**: 4 distinct, non-overlapping labels
- **Your setup**: 7 labels with semantic overlap
  - DATE vs TIME (both temporal)
  - MERCHANT_NAME vs PRODUCT_NAME (both names)
  - ADDRESS_LINE vs PHONE_NUMBER (both contact info)

**Impact**: More labels = more confusion = lower F1

### 2. **Standardized Receipt Format**
- SROIE uses a standardized dataset with consistent layouts
- Your receipts are real-world with varying formats, fonts, layouts

**Impact**: Standardized format = easier for model to learn patterns

### 3. **Clean OCR**
- SROIE likely has high-quality OCR preprocessing
- Real-world receipts may have OCR errors, noise, poor quality scans

**Impact**: Clean OCR = better text input = better predictions

## Strategies to Reach 95% F1

### Strategy 1: Reduce Label Count (Highest Impact) ⭐⭐⭐

**Merge similar labels to match SROIE's 4-label approach:**

```python
# Option A: Match SROIE exactly (4 labels)
- MERCHANT_NAME (keep)
- DATE (merge DATE + TIME)
- ADDRESS_LINE (merge ADDRESS_LINE + PHONE_NUMBER)
- AMOUNT (already merged)

# Option B: Slightly more granular (5 labels)
- MERCHANT_NAME (keep)
- DATE (merge DATE + TIME)
- ADDRESS_LINE (keep)
- PHONE_NUMBER (keep)
- AMOUNT (keep)
- PRODUCT_NAME (keep) - but this is the hardest one
```

**Expected Impact: +5-10% F1**
- Reducing from 7 to 4-5 labels significantly reduces confusion
- Matches the approach that achieved 95% in research

**Trade-off**: Less granular information (e.g., can't distinguish DATE from TIME)

### Strategy 2: Upgrade to LayoutLMv2 (High Impact) ⭐⭐⭐

**LayoutLMv2 adds visual features:**
- Image embeddings (font, style, visual cues)
- Better understanding of document layout
- Improved performance on complex documents

**Expected Impact: +3-7% F1**

**Implementation:**
```python
# Change model from:
pretrained_model_name = "microsoft/layoutlm-base-uncased"

# To:
pretrained_model_name = "microsoft/layoutlmv2-base-uncased"
```

**Trade-off**:
- Larger model (more memory)
- Slower training/inference
- May need to adjust hyperparameters

### Strategy 3: Improve Data Quality (Medium Impact) ⭐⭐

**Focus on high-quality labels:**
1. **Review and fix INVALID labels** - Remove noisy data
2. **Resolve NEEDS_REVIEW labels** - Increase training data
3. **Filter low-quality receipts** - Remove blurry, poorly scanned receipts
4. **Improve OCR quality** - Better preprocessing

**Expected Impact: +2-5% F1**

**Current Status:**
- You have ~13,000 VALID labels
- Some labels have high INVALID/NEEDS_REVIEW rates (e.g., ADDRESS_LINE, MERCHANT_NAME)
- Resolving these could add significant training data

### Strategy 4: Data Augmentation (Medium Impact) ⭐⭐

**Augment training data:**
1. **Bounding box perturbations** - Slight shifts in coordinates
2. **Text variations** - Synonym replacement (careful with receipt data)
3. **Layout variations** - Simulate different receipt formats
4. **Noise injection** - Simulate OCR errors

**Expected Impact: +2-4% F1**

**Implementation:**
- Add augmentation in data_loader.py
- Apply during preprocessing

### Strategy 5: Hyperparameter Tuning (Low-Medium Impact) ⭐

**Fine-tune hyperparameters:**
1. **Learning rate**: Try 3e-5, 4e-5, 5e-5 (currently 6e-5)
2. **Batch size**: Try 32, 48 (currently 64)
3. **Warmup ratio**: Try 0.1, 0.15 (currently 0.2)
4. **Label smoothing**: Try 0.05, 0.15 (currently 0.1)
5. **O:entity ratio**: Try 1.5, 1.8, 2.2 (currently 2.0)

**Expected Impact: +1-3% F1**

**Approach:**
- Grid search or random search
- Focus on learning rate and batch size first

### Strategy 6: Multi-Task Learning (Medium Impact) ⭐⭐

**We just implemented category-aware embeddings (Option 2)**
- Next step: Full multi-task learning (Option 1)
- Predict category + label simultaneously

**Expected Impact: +2-5% F1**

**Implementation:**
- Add category classifier head
- Multi-task loss: label_loss + category_loss

### Strategy 7: Ensemble Methods (High Impact, High Complexity) ⭐⭐⭐

**Combine multiple models:**
1. Train 3-5 models with different seeds
2. Average predictions at inference time
3. Or use voting/weighted averaging

**Expected Impact: +3-8% F1**

**Trade-off**:
- 3-5x training time
- 3-5x inference time
- More complex deployment

### Strategy 8: More Training Data (Diminishing Returns) ⭐

**Add more labeled receipts:**
- You already have ~13,000 labels (good amount)
- Research shows diminishing returns after ~5,000-10,000 examples
- Focus on quality over quantity

**Expected Impact: +1-2% F1** (if you double the dataset)

## Realistic Path to 95% F1

### Quick Wins (Implement First)

1. **Reduce labels to 4-5** → +5-10% F1 → **75-80% F1**
2. **Upgrade to LayoutLMv2** → +3-7% F1 → **78-87% F1**
3. **Improve data quality** → +2-5% F1 → **80-92% F1**
4. **Category-aware embeddings** (already done) → +1-3% F1 → **81-95% F1**

**Combined Expected: 85-95% F1** ✅

### Advanced Techniques (If Needed)

5. **Multi-task learning** → +2-5% F1
6. **Data augmentation** → +2-4% F1
7. **Hyperparameter tuning** → +1-3% F1
8. **Ensemble methods** → +3-8% F1

## Recommended Implementation Order

### Phase 1: Quick Wins (This Week)
1. ✅ **Category-aware embeddings** (already implemented)
2. **Reduce labels to 4-5** (merge DATE+TIME, consider merging ADDRESS+PHONE)
3. **Upgrade to LayoutLMv2** (change model name)

**Expected Result: 80-85% F1**

### Phase 2: Data Quality (Next Week)
4. **Review and fix INVALID labels**
5. **Resolve NEEDS_REVIEW labels**
6. **Filter low-quality receipts**

**Expected Result: 85-90% F1**

### Phase 3: Advanced (If Needed)
7. **Multi-task learning** (full implementation)
8. **Data augmentation**
9. **Hyperparameter tuning**

**Expected Result: 90-95% F1**

## Trade-offs to Consider

### Reducing Labels
- **Pro**: Higher F1, simpler model, faster training
- **Con**: Less granular information (can't distinguish DATE from TIME)

### LayoutLMv2
- **Pro**: Better accuracy, visual understanding
- **Con**: Larger model, slower, more memory

### More Training Data
- **Pro**: Better generalization
- **Con**: Diminishing returns, time-consuming to label

## Bottom Line

**To reach 95% F1, you likely need:**

1. **Reduce to 4-5 labels** (match SROIE approach) - **Most Important**
2. **Upgrade to LayoutLMv2** - **High Impact**
3. **Improve data quality** - **Medium Impact**
4. **Category-aware embeddings** - **Already Done** ✅

**Combined, these should get you to 85-95% F1**, which is very close to the research benchmark.

The key insight: **SROIE's 95% was achieved with only 4 labels**. Your 7-label setup is inherently more difficult. Reducing label count is the single biggest lever you can pull.

