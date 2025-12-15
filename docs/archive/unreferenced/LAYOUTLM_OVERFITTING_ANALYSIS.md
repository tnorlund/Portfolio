# LayoutLM Overfitting Analysis

## Summary

**Risk Level: MODERATE to HIGH** ⚠️

The model shows clear signs of overfitting, with performance peaking at epoch 6 and degrading afterward. However, the degradation is relatively mild (~2-3% F1 drop), suggesting the overfitting is not severe.

## Evidence of Overfitting

### 1. Performance Degradation After Peak

**Training Run**: `receipts-2025-11-13-0625-4label-sroie-lr3e5-o25`

| Epoch | Validation F1 | Change from Best |
|-------|---------------|------------------|
| 6     | **0.7175** ⭐ | Best (baseline) |
| 7     | 0.7121       | -0.5% |
| 8     | 0.6936       | **-2.3%** ⚠️ |
| 9     | 0.7061       | -1.6% |
| 10    | 0.6990       | -1.8% |
| 11    | 0.7010       | -1.7% |

**Key Observations:**
- ✅ Best F1 at epoch 6: 0.7175
- ⚠️ Performance degraded after epoch 6
- ⚠️ Worst drop at epoch 8: -2.3% from best
- ⚠️ Model never recovered to best performance

### 2. Early Stopping Didn't Trigger

**Configuration:**
- Early stopping patience: 5 epochs
- Best epoch: 6
- Training continued to epoch 11

**Expected Behavior:**
- Should have stopped at epoch 11 (5 epochs after best at epoch 6)
- Early stopping callback may not be working correctly
- Or `metric_for_best_model` may not be set to `eval_f1`

**Impact:**
- Model trained 5 extra epochs after peak performance
- Wasted compute resources
- Final model is worse than epoch 6 checkpoint

### 3. Dataset Size vs Model Complexity

**Dataset:**
- Training examples: 9,498 lines
- Validation examples: 2,179 lines
- Total receipts: ~500-1,000 (estimated)
- 90/10 train/val split at receipt level

**Model:**
- Base model: `microsoft/layoutlm-base-uncased`
- Parameters: ~113M (pre-trained)
- Fine-tuning: Only classifier head (small)
- Label set: 4 labels (simplified from 7)

**Assessment:**
- ✅ Dataset size is reasonable for fine-tuning
- ✅ Receipt-level split prevents data leakage
- ⚠️ Model complexity is high (113M params)
- ⚠️ Training examples per parameter: ~84 examples/1M params

**Comparison to SROIE:**
- SROIE: 626 training receipts, 95% F1
- Your dataset: ~500-1,000 receipts, 71.75% F1
- Similar dataset size, but lower performance suggests overfitting risk

## Risk Factors

### ✅ Protective Factors (Lower Overfitting Risk)

1. **Label Smoothing (0.1)**: Helps prevent overconfidence
2. **O:Entity Ratio (2.5)**: Downsampling O tokens reduces memorization
3. **Receipt-Level Split**: Prevents data leakage between train/val
4. **Pre-trained Model**: Only fine-tuning classifier head (not full model)
5. **Simplified Labels**: 4 labels reduces complexity

### ⚠️ Risk Factors (Higher Overfitting Risk)

1. **Large Model**: 113M parameters for ~9,500 training examples
2. **Performance Degradation**: Clear drop after epoch 6
3. **Early Stopping Failure**: Didn't stop when it should have
4. **Limited Validation Set**: Only 2,179 examples (10% of data)
5. **Real-World Data**: More variation than standardized datasets

## Recommendations

### Immediate Actions

1. **✅ Use Epoch 6 Checkpoint** (Best Model)
   - Current best: Epoch 6 (F1: 0.7175)
   - Don't use epoch 11 checkpoint (F1: 0.7010)
   - Verify `best/` directory contains epoch 6 model

2. **Fix Early Stopping**
   - Verify `metric_for_best_model="eval_f1"` is set
   - Check that early stopping callback is properly configured
   - Test with a short run to confirm it triggers

3. **Monitor Training vs Validation Loss**
   - Check if training loss continues decreasing while validation loss increases
   - This is the clearest sign of overfitting

### Future Training Improvements

1. **Reduce Early Stopping Patience**
   - Current: 5 epochs
   - Recommended: 3 epochs
   - Faster detection of overfitting

2. **Increase Validation Set Size**
   - Current: 10% (2,179 examples)
   - Recommended: 15-20% (3,000-4,000 examples)
   - Better overfitting detection

3. **Add Regularization**
   - Current: Label smoothing 0.1 ✅
   - Consider: Dropout (if not already enabled)
   - Consider: Weight decay increase (currently 0.01)

4. **Reduce Model Complexity** (if overfitting persists)
   - Use smaller pre-trained model (LayoutLM-small)
   - Or freeze more layers during fine-tuning

5. **Data Augmentation** (if possible)
   - Add more training examples
   - Synthesize variations of existing receipts

## Severity Assessment

### Current Overfitting: **MODERATE** (2-3% F1 degradation)

**Why Moderate, Not Severe:**
- ✅ Degradation is relatively small (~2-3%)
- ✅ Model still performs reasonably well (70% F1)
- ✅ Best checkpoint is saved and usable
- ⚠️ But clear signs of overfitting present

**If Overfitting Worsens:**
- Watch for >5% F1 degradation
- Watch for training loss → 0 while validation loss increases
- Watch for validation F1 dropping below 65%

## Conclusion

**The model IS overfitting, but not severely.**

**Key Findings:**
1. ✅ Best model is at epoch 6 (F1: 0.7175)
2. ⚠️ Performance degraded 2-3% after epoch 6
3. ⚠️ Early stopping didn't trigger (should have stopped at epoch 11)
4. ✅ Overfitting is moderate, not severe

**Action Items:**
1. Use epoch 6 checkpoint (best model)
2. Fix early stopping configuration
3. Reduce patience to 3 epochs for future runs
4. Monitor training vs validation loss gap

**The model is still usable** - just make sure you're using the epoch 6 checkpoint, not the final epoch 11 checkpoint.

