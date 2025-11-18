# LayoutLM Training Metrics Enhancements

This document describes the enhancements made to capture additional training metrics for better AI agent analysis.

## Changes Made

### 1. ✅ Training Loss Tracking

**Location:** `receipt_layoutlm/receipt_layoutlm/trainer.py` - `_MetricLoggerCallback.on_evaluate()`

**What was added:**
- Extracts `train_loss` from trainer's `log_history` after each evaluation
- Stores in `epoch_metrics` in `run.json` and `run_summary` log

**Impact:**
- Agent can now detect overfitting by comparing train_loss vs eval_loss
- Enables training vs validation loss curve analysis

**Data Structure:**
```json
{
  "epoch": 1.0,
  "train_loss": 0.9829,
  "eval_loss": 1.8743,
  "eval_f1": 0.0239,
  ...
}
```

---

### 2. ✅ Learning Rate Tracking

**Location:** `receipt_layoutlm/receipt_layoutlm/trainer.py` - `_MetricLoggerCallback.on_evaluate()`

**What was added:**
- Extracts `learning_rate` from trainer's `log_history` after each evaluation
- Stores in `epoch_metrics` to track LR schedule

**Impact:**
- Agent can verify LR schedule (warmup, decay) is working correctly
- Can identify if LR is too high/low based on loss curves

**Data Structure:**
```json
{
  "epoch": 1.0,
  "learning_rate": 6e-5,
  ...
}
```

---

### 3. ✅ Per-Label Metrics

**Location:** `receipt_layoutlm/receipt_layoutlm/trainer.py` - `compute_metrics()`

**What was added:**
- Uses `seqeval.classification_report()` to get per-label F1, precision, recall
- Stores per-label metrics in `epoch_metrics` for each epoch

**Impact:**
- Agent can identify which labels perform poorly (e.g., MERCHANT_NAME vs AMOUNT)
- Enables targeted improvements (more data for specific labels)
- Can detect label confusion patterns

**Data Structure:**
```json
{
  "epoch": 1.0,
  "eval_f1": 0.5688,
  "per_label_metrics": {
    "B-AMOUNT": {
      "f1": 0.65,
      "precision": 0.70,
      "recall": 0.60,
      "support": 232
    },
    "B-DATE": {
      "f1": 0.55,
      "precision": 0.60,
      "recall": 0.50,
      "support": 93
    },
    ...
  }
}
```

---

### 4. ✅ Early Stopping Information

**Location:** `receipt_layoutlm/receipt_layoutlm/trainer.py` - After training completion

**What was added:**
- Calculates `best_epoch` and `best_f1` from epoch_metrics
- Determines if early stopping triggered
- Stores `epochs_since_best` in `run_summary` log

**Impact:**
- Agent knows which epoch had the best performance
- Can determine if training stopped early or ran full epochs
- Helps optimize patience hyperparameter

**Data Structure:**
```json
{
  "type": "run_summary",
  "epoch_metrics": [...],
  "best_epoch": 18.0,
  "best_f1": 0.5688,
  "early_stopping_triggered": false,
  "epochs_since_best": 2
}
```

---

### 5. ✅ Fixed Epoch in JobMetric

**Location:** `receipt_layoutlm/receipt_layoutlm/trainer.py` - `_MetricLoggerCallback.on_evaluate()`

**What was added:**
- Moved JobMetric storage from `compute_metrics()` to callback
- Now stores `epoch` and `step` in JobMetric entries

**Impact:**
- Fixes `epoch=None` issue in DynamoDB metrics
- Enables proper epoch-based queries and analysis

**Before:**
```python
# In compute_metrics() - no access to epoch
metric = JobMetric(..., epoch=None)  # ❌
```

**After:**
```python
# In callback - has access to state.epoch
metric = JobMetric(..., epoch=int(current_epoch), step=global_step)  # ✅
```

---

## Testing

To verify these enhancements work:

1. **Run a training job** with the updated code
2. **Check `run_summary` log** in DynamoDB for:
   - `train_loss` in `epoch_metrics`
   - `learning_rate` in `epoch_metrics`
   - `per_label_metrics` in `epoch_metrics`
   - `best_epoch`, `best_f1`, `early_stopping_triggered` in summary
3. **Check JobMetric entries** for:
   - `epoch` field populated (not None)
   - `step` field populated

---

## Example Enhanced Epoch Metrics

```json
{
  "epoch": 18.0,
  "global_step": 2610,
  "eval_loss": 0.6764,
  "eval_f1": 0.5688,
  "eval_precision": 0.4625,
  "eval_recall": 0.7383,
  "train_loss": 0.7770,
  "learning_rate": 3.98e-6,
  "per_label_metrics": {
    "B-AMOUNT": {
      "f1": 0.65,
      "precision": 0.70,
      "recall": 0.60,
      "support": 232
    },
    "B-DATE": {
      "f1": 0.55,
      "precision": 0.60,
      "recall": 0.50,
      "support": 93
    },
    "B-ADDRESS": {
      "f1": 0.50,
      "precision": 0.55,
      "recall": 0.45,
      "support": 102
    },
    "B-MERCHANT_NAME": {
      "f1": 0.45,
      "precision": 0.50,
      "recall": 0.40,
      "support": 66
    }
  }
}
```

---

## Agent Capabilities After These Changes

### ✅ Now Possible:
1. **Overfitting Detection**: Compare `train_loss` vs `eval_loss` trends
2. **Label-Specific Analysis**: Identify which labels need more data
3. **LR Schedule Verification**: Confirm warmup and decay are working
4. **Early Stopping Analysis**: Understand why training stopped
5. **Targeted Improvements**: Recommend specific labels to focus on

### Example Agent Queries:
- "Which label has the lowest F1 score?"
- "Is the model overfitting? (train_loss decreasing, eval_loss increasing)"
- "Did early stopping trigger? What was the best epoch?"
- "What's the learning rate at epoch 10?"
- "Which labels need more training data?"

---

## Backward Compatibility

- ✅ All changes are additive - existing code continues to work
- ✅ Per-label metrics are optional (only if `seqeval.classification_report` available)
- ✅ Training loss/LR are optional (only if available in log_history)
- ✅ Early stopping info is optional (calculated if epoch_metrics available)

---

## Files Modified

1. `receipt_layoutlm/receipt_layoutlm/trainer.py`
   - Enhanced `_MetricLoggerCallback` to capture train_loss and learning_rate
   - Enhanced `compute_metrics` to calculate per-label metrics
   - Enhanced `run_summary` generation to include early stopping info
   - Fixed JobMetric epoch storage

---

## Next Steps

After deploying these changes:
1. Run a test training job to verify all metrics are captured
2. Update agent helper scripts to use new metrics
3. Test agent analysis with enhanced data

