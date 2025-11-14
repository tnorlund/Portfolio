# Training Command for 4-Label Setup (Option 1: Match SROIE)

## What We're Testing

**Option 1: 4 labels matching SROIE exactly**
- MERCHANT_NAME (647 VALID)
- DATE (991 VALID) - merged from DATE (435) + TIME (556)
- ADDRESS (1,695 VALID) - merged from ADDRESS_LINE (1,394) + PHONE_NUMBER (301)
- AMOUNT (2,815 VALID) - already merged from LINE_TOTAL + SUBTOTAL + TAX + GRAND_TOTAL

**Expected F1: 85-95%** (matching SROIE's approach)

## Training Command

```bash
JOB=receipts-$(date +%F-%H%M)-4label-sroie
layoutlm-cli train \
  --job-name "$JOB" \
  --dynamo-table "$DYNAMO_TABLE_NAME" \
  --epochs 20 \
  --batch-size 64 \
  --lr 6e-5 \
  --warmup-ratio 0.2 \
  --label-smoothing 0.1 \
  --o-entity-ratio 2.0 \
  --merge-amounts \
  --merge-date-time \
  --merge-address-phone \
  --early-stopping-patience 5 \
  --allowed-label MERCHANT_NAME \
  --allowed-label DATE \
  --allowed-label ADDRESS \
  --allowed-label AMOUNT
```

## What Changed

1. **Added `--merge-date-time`**: Merges DATE and TIME into DATE
2. **Added `--merge-address-phone`**: Merges ADDRESS_LINE and PHONE_NUMBER into ADDRESS
3. **Removed PRODUCT_NAME**: Not included in 4-label setup
4. **Removed PHONE_NUMBER, TIME, ADDRESS_LINE**: Now merged into ADDRESS and DATE

## Expected Results

**Current (7 labels):**
- F1: 70%
- Imbalance: 15:1 (PRODUCT_NAME vs PHONE_NUMBER)

**New (4 labels):**
- Expected F1: **85-95%**
- Better balance: ~4:1 (AMOUNT vs MERCHANT_NAME)
- Matches SROIE structure exactly

## Comparison

| Metric | Current (7 labels) | New (4 labels) | SROIE (4 labels) |
|--------|-------------------|----------------|------------------|
| **F1 Score** | 70% | **85-95%** (expected) | 95.24% |
| **Label Count** | 7 | 4 | 4 |
| **Balance** | 15:1 | ~4:1 | 1:1:1:1 |
| **Structure** | Complex | Simple | Simple |

## Next Steps

1. **Build and upload new wheels** to EC2
2. **Run the training command** above
3. **Compare results** to current 7-label setup
4. **If successful**, consider adding LayoutLMv2 for even higher F1

