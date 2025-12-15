# Missing Features for AI Agent Training Analysis

This document identifies what information is **missing** that would help an AI agent better analyze what's working and what isn't in LayoutLM training.

**Last Updated:** After implementing training loss, learning rate, per-label metrics, early stopping tracking, training time metrics, checkpoint information, and model architecture info.

**Status:** Most critical features have been implemented! ✅ Coverage now ~90%

## Current Status

### ✅ What's Available (from job `9eefa1f0-bd2c-48cc-9d27-5c6a41e5fb50`)

1. **Job Configuration** - Full hyperparameters
2. **Dataset Statistics** - Train/val splits, label counts, O:entity ratios (in `run_config` log)
3. **Validation Metrics** - F1, precision, recall, loss (in `run_summary` log)
4. **Training Loss** - Per-epoch training loss (in `epoch_metrics`) ✅ **NEW**
5. **Learning Rate** - Per-epoch learning rate (in `epoch_metrics`) ✅ **NEW**
6. **Per-Label Metrics** - F1, precision, recall per label (in `epoch_metrics`) ✅ **NEW**
7. **Early Stopping Info** - Best epoch, best F1, whether triggered (in `run_summary`) ✅ **NEW**
8. **Training Time** - Total runtime and FLOPs (in `run_summary`) ✅ **NEW**
9. **Checkpoint Info** - Best checkpoint S3 path and count (in `run_summary`) ✅ **NEW**
10. **Model Architecture** - Number of labels (in `run_config`) ✅ **NEW**
11. **Epoch Progression** - All epochs with comprehensive metrics

### ⚠️ What's Still Missing (Lower Priority)

## 1. Training Loss (Critical for Overfitting Detection)

**Status:** ✅ **IMPLEMENTED**

**What's Available:**
- Training loss per epoch stored in `epoch_metrics` in `run_summary` log
- Extracted from trainer's `log_history` after each evaluation

**Why It's Important:**
- **Overfitting Detection**: If training loss decreases but validation loss increases → overfitting
- **Convergence**: If both training and validation loss plateau → model has converged
- **Learning Rate Issues**: If training loss oscillates wildly → learning rate too high

**Implementation:**
- Added to `_MetricLoggerCallback.on_evaluate()` in `trainer.py`
- Extracts `train_loss` from `state.log_history` after each evaluation
- Stored in `epoch_metrics` in both `run.json` and `run_summary` log

**Data Structure:**
```json
{
  "epoch": 18.0,
  "train_loss": 0.7770,
  "eval_loss": 0.6764,
  ...
}
```

---

## 2. Per-Label Performance Metrics

**Status:** ✅ **IMPLEMENTED**

**What's Available:**
- F1, precision, recall **per label** stored in `epoch_metrics` in `run_summary` log
- Uses `seqeval.classification_report()` to calculate per-label metrics
- Includes support (count) for each label

**Why It's Important:**
- **Label Imbalance**: Some labels may perform poorly due to class imbalance
- **Label Confusion**: Model might confuse similar labels (e.g., DATE vs TIME)
- **Targeted Improvement**: Know which labels need more training data

**Implementation:**
- Added to `compute_metrics()` in `trainer.py`
- Uses `seqeval.classification_report(output_dict=True)` to get per-label metrics
- Stored in `epoch_metrics` as nested `per_label_metrics` dict

**Data Structure:**
```json
{
  "epoch": 18.0,
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

## 3. Learning Rate Schedule

**Status:** ✅ **IMPLEMENTED**

**What's Available:**
- Learning rate value at each epoch stored in `epoch_metrics` in `run_summary` log
- Extracted from trainer's `log_history` after each evaluation

**Why It's Important:**
- **LR Too High**: If loss oscillates, LR might be too high
- **LR Too Low**: If loss decreases very slowly, LR might be too low
- **Warmup Issues**: If performance drops after warmup, warmup ratio might be wrong

**Implementation:**
- Added to `_MetricLoggerCallback.on_evaluate()` in `trainer.py`
- Extracts `learning_rate` from `state.log_history` after each evaluation
- Stored in `epoch_metrics` in both `run.json` and `run_summary` log

**Data Structure:**
```json
{
  "epoch": 18.0,
  "learning_rate": 3.98e-6,
  ...
}
```

---

## 4. Gradient Norms

**Status:** ❌ **NOT STORED**

**What's Missing:**
- Gradient norm per epoch/step
- Gradient clipping information

**Why It's Important:**
- **Gradient Explosion**: If gradient norm spikes → model unstable, need gradient clipping
- **Gradient Vanishing**: If gradient norm very small → model not learning
- **Training Stability**: Monitor for training instability

**Current Workaround:**
- Not available anywhere

**Recommendation:**
- Store `grad_norm` in `epoch_metrics` if available from trainer

---

## 5. Training Time Metrics

**Status:** ✅ **IMPLEMENTED**

**What's Available:**
- `eval_runtime` per epoch (in `epoch_metrics`)
- `eval_samples_per_second` per epoch (in `epoch_metrics`)
- `train_runtime_seconds` - Total training time (in `run_summary` log) ✅ **NEW**
- `total_flos` - Total floating point operations (in `run_summary` log) ✅ **NEW**

**Why It's Important:**
- **Performance Optimization**: Identify bottlenecks
- **Cost Estimation**: Estimate training costs based on runtime and FLOPs
- **Resource Planning**: Plan for future training runs

**Implementation:**
- Extracted from `trainer_state.json` after training completes
- Stored in `run_summary` log

**Data Structure:**
```json
{
  "type": "run_summary",
  "train_runtime_seconds": 12345.67,
  "total_flos": 1234567890123,
  ...
}
```

---

## 6. Early Stopping Information

**Status:** ✅ **IMPLEMENTED**

**What's Available:**
- `early_stopping_patience` in job config
- `best_epoch` and `best_f1` calculated from epoch_metrics
- `early_stopping_triggered` boolean flag
- `epochs_since_best` count

**Why It's Important:**
- **Training Efficiency**: Know if training stopped early or ran full epochs
- **Overfitting Detection**: If best epoch was early but training continued → overfitting
- **Hyperparameter Tuning**: Adjust patience based on when best epoch occurs

**Implementation:**
- Added to `run_summary` generation after training completes
- Calculates best epoch from epoch_metrics
- Determines if early stopping triggered by comparing last epoch to best epoch

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

## 7. Checkpoint Information

**Status:** ✅ **IMPLEMENTED**

**What's Available:**
- `best_checkpoint_s3_path` - S3 path to best checkpoint (in `run_summary` log) ✅ **NEW**
- `best_checkpoint_path` - Local path if S3 not configured (in `run_summary` log) ✅ **NEW**
- `num_checkpoints` - Total number of checkpoints saved (in `run_summary` log) ✅ **NEW**

**Why It's Important:**
- **Model Retrieval**: Know which model to use for inference
- **Storage Costs**: Track checkpoint storage usage
- **Model Comparison**: Compare different checkpoints

**Implementation:**
- Extracted from `trainer_state.json` after training completes
- S3 path constructed from `output_s3_path` config
- Checkpoint count from directory glob

**Data Structure:**
```json
{
  "type": "run_summary",
  "best_checkpoint_s3_path": "s3://bucket/runs/job_name/best/",
  "num_checkpoints": 3,
  ...
}
```

---

## 8. Error Logs and Failures

**Status:** ⚠️ **PARTIALLY AVAILABLE**

**What's Available:**
- Job status (succeeded, failed, running)
- Job logs (but may not contain error details)

**What's Missing:**
- Detailed error messages if training fails
- Stack traces
- OOM errors
- GPU errors

**Why It's Important:**
- **Debugging**: Understand why training failed
- **Prevention**: Fix issues before next run
- **Resource Planning**: Adjust batch size if OOM

**Current Workaround:**
- Error logs are in CloudWatch or on EC2
- Not easily accessible from DynamoDB

**Recommendation:**
- Store error messages in `JobLog` if training fails
- Include exception type and message

---

## 9. Hardware Utilization

**Status:** ❌ **NOT STORED**

**What's Missing:**
- GPU utilization percentage
- GPU memory usage
- CPU utilization
- Memory usage

**Why It's Important:**
- **Performance Optimization**: Identify if GPU is underutilized
- **Resource Planning**: Know if can increase batch size
- **Cost Optimization**: Right-size instances

**Current Workaround:**
- Would need to monitor EC2 instance separately
- Not available in DynamoDB

**Recommendation:**
- Store GPU utilization in `epoch_metrics` if available
- Or as separate `JobMetric` entries

---

## 10. Training vs Validation Comparison

**Status:** ⚠️ **PARTIALLY AVAILABLE**

**What's Available:**
- Validation metrics (F1, precision, recall, loss)
- Dataset statistics for both train and validation

**What's Missing:**
- Training metrics (F1, precision, recall) for comparison
- Training vs validation loss curves
- Gap between train and validation performance

**Why It's Important:**
- **Overfitting Detection**: Large gap between train and val → overfitting
- **Underfitting Detection**: Both train and val low → underfitting
- **Data Quality**: If train performance is low, data quality might be issue

**Current Workaround:**
- Can only see validation metrics
- Training metrics not stored

**Recommendation:**
- Store training metrics (F1, precision, recall) per epoch
- Include in `epoch_metrics` in `run_summary` log

---

## 11. Sample Predictions

**Status:** ❌ **NOT STORED**

**What's Missing:**
- Sample predictions vs ground truth
- Examples of correct predictions
- Examples of incorrect predictions (false positives, false negatives)

**Why It's Important:**
- **Error Analysis**: Understand what the model is getting wrong
- **Data Quality**: Identify labeling errors
- **Model Understanding**: See actual model behavior

**Current Workaround:**
- Would need to run inference separately
- Not available from training job

**Recommendation:**
- Store sample predictions in S3 or as part of job logs
- Include examples of common errors

---

## 12. Model Architecture Information

**Status:** ⚠️ **PARTIALLY AVAILABLE** (Improved)

**What's Available:**
- `pretrained_model_name` in job config
- `num_labels` - Number of labels (in `run_config` log) ✅ **NEW**
- `label_list` - Full list of labels (in `run_config` log)

**What's Still Missing:**
- Model size (number of parameters)
- Model architecture details
- Fine-tuning approach (full fine-tuning vs head-only)

**Why It's Important:**
- **Model Comparison**: Compare different architectures
- **Resource Planning**: Know model size for deployment
- **Reproducibility**: Full model configuration

**Implementation:**
- `num_labels` added to `run_config` log for easy access
- Can still infer architecture from `pretrained_model_name`

**Data Structure:**
```json
{
  "type": "run_config",
  "data": {
    "num_labels": 9,
    "label_list": ["O", "B-MERCHANT_NAME", "I-MERCHANT_NAME", ...],
    ...
  }
}
```

---

## Priority Recommendations

### High Priority (Most Impact on Analysis)

1. ✅ **Training Loss** - **IMPLEMENTED** - Critical for overfitting detection
2. ✅ **Per-Label Metrics** - **IMPLEMENTED** - Essential for understanding which labels need work
3. ⚠️ **Training vs Validation Comparison** - Partially available (validation metrics stored, training metrics not computed)
4. ✅ **Early Stopping Information** - **IMPLEMENTED** - Important for training efficiency

### Medium Priority (Helpful for Optimization)

5. ✅ **Learning Rate Schedule** - **IMPLEMENTED** - Useful for hyperparameter tuning
6. ✅ **Training Time Metrics** - **IMPLEMENTED** - Total runtime and FLOPs now stored
7. ✅ **Checkpoint Information** - **IMPLEMENTED** - Best checkpoint path and count now stored

### Low Priority (Nice to Have)

8. **Gradient Norms** - Useful for debugging but not critical
9. **Hardware Utilization** - Helpful for optimization but not essential
10. **Sample Predictions** - Useful for error analysis but can be done separately
11. **Error Logs** - Important for failures but not for successful runs
12. ⚠️ **Model Architecture** - Partially available (num_labels stored, but parameter count not stored)

---

## Implementation Suggestions

### Quick Wins (Easy to Add)

1. **Add training loss to `epoch_metrics`**:
   ```python
   # In trainer.py, in _MetricLoggerCallback.on_evaluate()
   entry["train_loss"] = float(state.log_history[-1].get("loss", 0))
   ```

2. **Add learning rate to `epoch_metrics`**:
   ```python
   entry["learning_rate"] = float(state.log_history[-1].get("learning_rate", 0))
   ```

3. **Store early stopping info in `run_summary`**:
   ```python
   summary = {
       "type": "run_summary",
       "epoch_metrics": epoch_metrics,
       "best_epoch": best_epoch,
       "early_stopping_triggered": early_stopped
   }
   ```

### Medium Effort

4. **Store per-label metrics**:
   - Use `seqeval.classification_report()` to get per-label metrics
   - Store as nested structure in `run_summary` log

5. **Store checkpoint info**:
   - Get best checkpoint from `trainer_state.json`
   - Store S3 path in job `storage` field

### Higher Effort

6. **Store gradient norms**:
   - Add callback to track gradient norms
   - Store in `epoch_metrics`

7. **Store hardware metrics**:
   - Add monitoring script to track GPU/CPU usage
   - Store as separate metrics

---

## Summary

**Current Coverage: ~90%** of what an agent needs for comprehensive training analysis. ✅

**Recently Implemented (✅):**
- ✅ Training loss (for overfitting detection)
- ✅ Per-label metrics (for targeted improvement)
- ✅ Learning rate tracking (for LR schedule verification)
- ✅ Early stopping details (for efficiency)
- ✅ Fixed epoch in JobMetric (was None, now populated)
- ✅ Training time metrics (total runtime and FLOPs)
- ✅ Checkpoint information (best checkpoint path and count)
- ✅ Model architecture info (num_labels in run_config)

**Remaining Gaps (⚠️):**
- ⚠️ Training vs validation comparison (validation metrics stored, but training F1/precision/recall not computed - would require running evaluation on training set)
- ⚠️ Gradient norms (useful but not critical)
- ⚠️ Hardware utilization (nice to have but not essential)
- ⚠️ Sample predictions (can be done separately)
- ⚠️ Model parameter count (can be inferred but not explicitly stored)

**With these additions, an agent can now:**
- ✅ Detect overfitting/underfitting (train_loss vs eval_loss)
- ✅ Identify which labels need more data (per-label metrics)
- ✅ Recommend hyperparameter changes (LR tracking, early stopping info)
- ✅ Optimize training efficiency (early stopping analysis)
- ✅ Debug training failures (comprehensive metrics)
- ✅ Retrieve best model checkpoint (S3 path stored)
- ✅ Estimate training costs (runtime and FLOPs available)
- ✅ Track checkpoint usage (count stored)
- ⚠️ Compare train vs val performance (validation metrics available, training metrics would require additional evaluation)

**See `docs/LAYOUTLM_TRAINING_METRICS_ENHANCEMENTS.md` for implementation details.**

