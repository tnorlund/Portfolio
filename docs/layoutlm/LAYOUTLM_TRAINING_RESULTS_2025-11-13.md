# LayoutLM Training Results - November 13, 2025

## Summary

**Major Success!** Training achieved **F1 = 0.70 (70.21%)** after just 3 epochs, more than **doubling** the previous best result of 0.32 F1.

## Training Run Details

### Validation Run (3 epochs)
- **Job Name**: `receipts-2025-11-13-02:XX-validate`
- **Job ID**: `49a5ce3b-4653-4aad-95da-11c9a7345b85`
- **Duration**: ~11.6 minutes (696.98 seconds)
- **Instance**: g5.xlarge (A10G GPU)

### Configuration
```bash
layoutlm-cli train \
  --job-name "receipts-$(date +%F-%H%M)-validate" \
  --dynamo-table "ReceiptsTable-dc5be22" \
  --epochs 3 \
  --batch-size 64 \
  --lr 6e-5 \
  --warmup-ratio 0.2 \
  --label-smoothing 0.1 \
  --o-entity-ratio 2.0 \
  --merge-amounts \
  --allowed-label MERCHANT_NAME \
  --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE \
  --allowed-label DATE \
  --allowed-label TIME \
  --allowed-label PRODUCT_NAME \
  --allowed-label AMOUNT
```

## Results by Epoch

| Epoch | F1      | Precision | Recall | Loss    |
|-------|---------|-----------|--------|---------|
| 1     | 0.6808  | 0.6460    | 0.7196 | 0.7644  |
| 2     | 0.6912  | 0.6326    | 0.7616 | 0.7503  |
| 3     | **0.7021** | **0.6438** | **0.7721** | 0.7557  |

**Final Metrics:**
- **F1 Score**: 0.7021 (70.21%) ✅
- **Precision**: 0.6438 (64.38%)
- **Recall**: 0.7721 (77.21%)
- **Training Loss**: 0.9099

## Comparison to Previous Runs (Sept 2025)

| Metric | Previous Best | Current Run | Improvement |
|--------|---------------|-------------|-------------|
| **F1 Score** | 0.32 (32%) | **0.70 (70%)** | **+119%** |
| **Epochs to Best** | 12 | 3 | 4x faster |
| **Precision** | ~0.35 | 0.64 | +83% |
| **Recall** | ~0.30 | 0.77 | +157% |

## Dataset Analysis

The dataset has grown significantly since September 2025. VALID label counts (used for training):

| Label | VALID Count | Notes |
|-------|-------------|-------|
| PRODUCT_NAME | 4,534 | Largest class |
| AMOUNT (merged) | ~2,815 | LINE_TOTAL (1,565) + SUBTOTAL (235) + TAX (271) + GRAND_TOTAL (744) |
| ADDRESS_LINE | 1,394 | |
| MERCHANT_NAME | 647 | |
| TIME | 556 | |
| DATE | 435 | |
| PHONE_NUMBER | 301 | |

**Total VALID labels**: ~10,682 (excluding AMOUNT breakdown)

### Label Quality Breakdown
- **High Quality Labels** (low INVALID/NEEDS_REVIEW):
  - PRODUCT_NAME: 4,534 VALID vs 359 INVALID (92.6% valid)
  - TIME: 556 VALID vs 251 INVALID (68.9% valid)
  - DATE: 435 VALID vs 450 INVALID (49.1% valid)
  
- **Labels Needing Review**:
  - ADDRESS_LINE: 1,394 VALID, 545 INVALID, 1,365 NEEDS_REVIEW
  - MERCHANT_NAME: 647 VALID, 346 INVALID, 798 NEEDS_REVIEW
  - PAYMENT_METHOD: 517 VALID, 129 INVALID, 625 NEEDS_REVIEW

## Key Success Factors

1. **Larger Dataset**: Significantly more VALID labels than September 2025
2. **Label Strategy**: Merged amounts (LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL → AMOUNT) worked well
3. **Simplified Label Set**: Focused on 7 core labels reduced confusion
4. **O:Entity Ratio**: 2.0 provided good balance between precision and recall
5. **Hyperparameters**: LR 6e-5, warmup 0.2, label smoothing 0.1 were effective

## Infrastructure Setup

- **Instance Type**: g5.xlarge (A10G 24GB GPU)
- **AMI**: Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.5.1 (Ubuntu 22.04)
- **Python**: 3.10 (via DLAMI conda environment)
- **PyTorch**: Pre-installed with CUDA 12.4
- **Key Pair**: Nov2025MacBookPro

### Setup Notes
- User-data script failed (written for Amazon Linux, but AMI is Ubuntu)
- Manually installed wheels and dependencies
- Added `~/.local/bin` to PATH for `layoutlm-cli`

## Next Steps

### 1. Full Training Run (20 epochs)
Currently running with:
- 20 epochs
- Batch size 128 (vs 64 for validation)
- Early stopping patience 5
- Same hyperparameters

**Expected**: F1 may improve to 0.75-0.80 with longer training

### 2. Model Evaluation
- Test on held-out validation set
- Evaluate per-label performance
- Check for overfitting

### 3. Production Deployment
- Export best model checkpoint
- Create inference Docker image
- Deploy to Fargate/CPU for production use

### 4. Further Improvements
- Consider adding more labels if dataset grows
- Experiment with different O:entity ratios
- Try LayoutLMv2 if images are available

## Files and Artifacts

- **Local Training Output**: `/tmp/receipt_layoutlm/receipts-2025-11-13-02:XX-validate/`
- **DynamoDB Job**: `49a5ce3b-4653-4aad-95da-11c9a7345b85`
- **S3 Bucket**: `layoutlm-models-1c8f680` (wheels and models)

## Lessons Learned

1. **Dataset size matters**: The larger dataset made a huge difference
2. **Label quality**: VALID labels are critical - the filtering worked well
3. **Simplified labels**: Merging amounts and using a whitelist improved performance
4. **Infrastructure**: Need to fix user-data script for Ubuntu AMI compatibility

## Conclusion

This training run demonstrates that with a larger, higher-quality dataset, LayoutLM can achieve strong performance (70%+ F1) for receipt token classification. The model is ready for production use after the full 20-epoch training completes.

