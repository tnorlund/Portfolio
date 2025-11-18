# LayoutLM Label Expansion Strategy

## When Can You Add More Labels?

### Current Status: 4-Label System (F1: 52-56% at epoch 7-10)

**Recommended Thresholds:**
- ✅ **Add 1-2 labels**: When F1 ≥ **65%** (stable 4-label performance)
- ✅ **Add 3-5 labels**: When F1 ≥ **70%** (strong 4-label performance)
- ✅ **Add 6+ labels**: When F1 ≥ **75%** (excellent 4-label performance)

**Why These Thresholds?**
- Adding labels increases task complexity
- Model needs strong baseline before handling more classes
- Research shows diminishing returns if you add labels too early

## Available Product-Based Labels (CORE_LABELS)

### Line-Item Fields (3 labels)
1. **`PRODUCT_NAME`** - Descriptive text of a purchased product (item name)
   - **Count**: ~4,534 VALID labels (largest class!)
   - **Difficulty**: Medium-Hard (can be ambiguous, varies in length)
   - **Priority**: ⭐⭐⭐ High (most valuable for product extraction)

2. **`QUANTITY`** - Numeric count or weight of the item (e.g., 2, 1.31 lb)
   - **Count**: ~781 VALID labels
   - **Difficulty**: Easy-Medium (numeric patterns are easier)
   - **Priority**: ⭐⭐ Medium (useful for line item parsing)

3. **`UNIT_PRICE`** - Price per single unit / weight before tax
   - **Count**: ~953 VALID labels
   - **Difficulty**: Easy (currency patterns, similar to AMOUNT)
   - **Priority**: ⭐⭐ Medium (useful for line item parsing)

### Current Status
- These labels are **already in your database** (labeled by LLM)
- They're currently **filtered out** during training (mapped to `O`)
- You have **good data coverage** for all three labels

## Two-Stage Approach Strategy

### ✅ Recommended: Two-Stage Extraction

**Stage 1: Core Fields (Current - 4 labels)**
- Train LayoutLM to extract: `MERCHANT_NAME`, `DATE`, `ADDRESS`, `AMOUNT`
- Use this model to find currency amounts (`AMOUNT` labels)
- **Goal**: Achieve 70%+ F1 on core fields

**Stage 2: Product Fields (Future - Add 3 labels)**
- Train a **separate LayoutLM model** focused on line items
- Use Stage 1 model to identify line item regions (words near `AMOUNT` labels)
- Train Stage 2 model on: `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`, `AMOUNT`
- **Goal**: Extract product details from identified line item regions

### Why Two-Stage Works Better

1. **Simpler Models**: Each model has fewer labels (4 vs 7)
2. **Better Accuracy**: Simpler tasks = higher F1 scores
3. **Contextual Focus**: Stage 2 can focus on line item regions
4. **Incremental**: Can deploy Stage 1 while training Stage 2

### Alternative: Single-Stage (7 labels)

**When to Use:**
- If you achieve 75%+ F1 on 4 labels
- If you have 1000+ high-quality receipts
- If you need all labels in one pass

**Trade-offs:**
- More complex model (7 labels vs 4)
- Lower F1 expected (65-70% vs 70-75%)
- But simpler deployment (one model)

## Expansion Roadmap

### Phase 1: Stabilize 4-Label System (Current)
**Goal**: Achieve 70%+ F1 on 4 labels
- ✅ Current: 52-56% F1 (epoch 7-10)
- ⏭️ Target: 70%+ F1 (epoch 12-15)
- **Actions**:
  - Let current training finish
  - Fix early stopping
  - Improve data quality
  - Review label consistency

### Phase 2: Add PRODUCT_NAME (5 labels)
**Goal**: Add product extraction capability
- **When**: After achieving 70%+ F1 on 4 labels
- **New Labels**: `PRODUCT_NAME` (add to existing 4)
- **Expected F1**: 65-70% (slight drop due to complexity)
- **Training Command**:
  ```bash
  --allowed-label MERCHANT_NAME \
  --allowed-label DATE \
  --allowed-label ADDRESS \
  --allowed-label AMOUNT \
  --allowed-label PRODUCT_NAME
  ```
- **Why First**: Largest class (4,534 labels), most valuable

### Phase 3: Add QUANTITY + UNIT_PRICE (7 labels)
**Goal**: Complete line item extraction
- **When**: After achieving 65%+ F1 on 5 labels
- **New Labels**: `QUANTITY`, `UNIT_PRICE`
- **Expected F1**: 60-65% (more complex, but still good)
- **Training Command**:
  ```bash
  --allowed-label MERCHANT_NAME \
  --allowed-label DATE \
  --allowed-label ADDRESS \
  --allowed-label AMOUNT \
  --allowed-label PRODUCT_NAME \
  --allowed-label QUANTITY \
  --allowed-label UNIT_PRICE
  ```

### Phase 4: Optional Labels (8-10 labels)
**Goal**: Add transaction details
- **When**: After achieving 65%+ F1 on 7 labels
- **Candidates**: `PAYMENT_METHOD`, `LOYALTY_ID`, `COUPON`, `DISCOUNT`
- **Expected F1**: 55-60% (diminishing returns)
- **Priority**: Lower (less critical for core use case)

## Two-Stage Implementation Details

### Stage 1: Core Fields Model (Current)
```python
# Current training
labels = ["MERCHANT_NAME", "DATE", "ADDRESS", "AMOUNT"]
# Use this to find AMOUNT labels (line totals)
```

### Stage 2: Product Fields Model (Future)
```python
# New training - focus on line items
# Option A: Train on full receipt with 4 product labels
labels = ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "AMOUNT"]

# Option B: Train only on line item regions
# 1. Use Stage 1 model to find AMOUNT labels
# 2. Extract words near each AMOUNT (line item region)
# 3. Train Stage 2 model only on these regions
# 4. Labels: ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE"]
```

### Stage 2: Region-Based Training (Recommended)

**Advantages:**
- Smaller context (just line items)
- Less noise (no merchant info, dates, etc.)
- Higher accuracy (focused task)
- Faster inference (smaller regions)

**Implementation:**
1. Run Stage 1 model to get `AMOUNT` predictions
2. For each `AMOUNT` prediction:
   - Extract words in same line (vertical alignment)
   - Extract words to the left (product name, quantity, unit price)
   - Create mini-dataset of line item regions
3. Train Stage 2 model on line item regions only
4. Labels: `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`

## Data Quality Requirements

### Before Adding Labels

**Check Label Distribution:**
```python
# Query DynamoDB for label counts
PRODUCT_NAME: 4,534 VALID ✅ (excellent)
QUANTITY: 781 VALID ✅ (good)
UNIT_PRICE: 953 VALID ✅ (good)
```

**Check Label Quality:**
- Review 50-100 examples of each new label
- Ensure consistent labeling (LLM validation helps!)
- Fix any systematic errors
- Resolve INVALID/NEEDS_REVIEW labels

**Check Label Balance:**
- PRODUCT_NAME: 4,534 (largest)
- MERCHANT_NAME: 647 (smallest)
- Ratio: 7:1 (acceptable, but watch for imbalance)

## Expected Performance Impact

### Adding PRODUCT_NAME (4 → 5 labels)
- **F1 Drop**: -3 to -5% (e.g., 70% → 65-67%)
- **Why**: PRODUCT_NAME is ambiguous, varies in length
- **Mitigation**:
  - Ensure high-quality PRODUCT_NAME labels
  - Use label smoothing (0.1)
  - Increase O:entity ratio if needed

### Adding QUANTITY + UNIT_PRICE (5 → 7 labels)
- **F1 Drop**: -2 to -4% (e.g., 65% → 61-63%)
- **Why**: More labels = more confusion
- **Mitigation**:
  - QUANTITY and UNIT_PRICE are easier (numeric patterns)
  - Should have less impact than PRODUCT_NAME

### Total Impact (4 → 7 labels)
- **Expected F1**: 60-65% (down from 70%+)
- **Trade-off**: Lower F1 but more granular information
- **Acceptable**: Still useful for production if >60%

## Recommendation

### ✅ Best Approach: Two-Stage with Region-Based Stage 2

1. **Stage 1 (Current)**: Train 4-label model to 70%+ F1
   - Extract: MERCHANT_NAME, DATE, ADDRESS, AMOUNT
   - Use AMOUNT labels to identify line item regions

2. **Stage 2 (Future)**: Train region-based product model
   - Focus on line item regions (words near AMOUNT)
   - Extract: PRODUCT_NAME, QUANTITY, UNIT_PRICE
   - Expected F1: 70-75% (simpler task, focused context)

3. **Combine Results**: Merge Stage 1 + Stage 2 predictions

### Why This Works Better

- **Higher Accuracy**: Each model is simpler (4 labels vs 7)
- **Better Performance**: 70%+ F1 on each stage vs 60-65% on 7 labels
- **Incremental**: Deploy Stage 1 while training Stage 2
- **Flexible**: Can improve Stage 2 independently

## Next Steps

1. **Current Training**: Let it finish, aim for 70%+ F1
2. **Data Quality**: Review PRODUCT_NAME, QUANTITY, UNIT_PRICE labels
3. **Plan Stage 2**: Design region extraction from AMOUNT labels
4. **When Ready**: Train Stage 2 model on line item regions
5. **Deploy**: Combine both models for full extraction

## Summary

- **When to Add**: After achieving 70%+ F1 on 4 labels
- **Product Labels**: PRODUCT_NAME (high priority), QUANTITY, UNIT_PRICE
- **Best Strategy**: Two-stage approach with region-based Stage 2
- **Expected F1**: 70%+ on each stage (better than 60-65% on 7 labels)
- **Data Ready**: You already have good coverage (4,534 PRODUCT_NAME labels!)

