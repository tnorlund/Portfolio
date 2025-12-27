# LayoutLM Training Metrics

This document tracks the metrics recorded during LayoutLM training runs and their storage in DynamoDB.

## Storage Entities

| Entity | Purpose | Query Capabilities |
|--------|---------|-------------------|
| `Job` | Training run metadata, config, status | Query by status, created_at |
| `JobMetric` | Individual metric values with epoch/step | Query by metric name across jobs (GSI1, GSI2) |
| `JobLog` | Structured JSON logs (config, summary) | Query by job_id |

## Per-Epoch Training Metrics

Recorded as `JobMetric` entities after each evaluation epoch.

| Metric | Status | Source | Unit | Notes |
|--------|--------|--------|------|-------|
| `val_f1` | ✅ Implemented | `compute_metrics` | ratio | Entity-level F1 from seqeval |
| `val_precision` | ✅ Implemented | `compute_metrics` | ratio | Entity-level precision |
| `val_recall` | ✅ Implemented | `compute_metrics` | ratio | Entity-level recall |
| `eval_loss` | ✅ Implemented | HF Trainer | loss | Validation loss (lower is better) |
| `train_loss` | ✅ Implemented | HF Trainer `log_history` | loss | Training loss curve |
| `learning_rate` | ✅ Implemented | HF Trainer `log_history` | rate | LR scheduler tracking |

**Implementation:** `receipt_layoutlm/trainer.py` → `_MetricLoggerCallback.on_evaluate()`

**Batch Write:** All 6 metrics written in single `add_job_metrics()` call per epoch.

## Per-Label Metrics

Per-label breakdown stored as one `JobMetric` per label with Dict value.

| Metric | Status | Source | Notes |
|--------|--------|--------|-------|
| `label_{LABEL_NAME}` | ✅ Implemented | `classification_report` | Dict with f1, precision, recall, support |

**Storage format:** One record per label per epoch:
```python
JobMetric(
    metric_name="label_MERCHANT_NAME",
    value={"f1": 0.92, "precision": 0.94, "recall": 0.90, "support": 156},
    unit="per_label",
    epoch=3,
)
JobMetric(
    metric_name="label_AMOUNT",
    value={"f1": 0.85, "precision": 0.88, "recall": 0.82, "support": 423},
    unit="per_label",
    epoch=3,
)
```

**Query examples:**
```python
# Get MERCHANT_NAME metrics across all jobs
metrics, _ = dynamo.get_metrics_by_name("label_MERCHANT_NAME")
for m in metrics:
    print(f"Job {m.job_id[:8]} Epoch {m.epoch}: F1={m.value['f1']:.3f}")

# Compare label performance within a job
label_metrics, _ = dynamo.list_job_metrics(job_id)
for m in [m for m in label_metrics if m.metric_name.startswith("label_")]:
    print(f"{m.metric_name}: F1={m.value['f1']:.3f} Support={m.value['support']}")
```

## Training Summary Metrics

End-of-run metrics stored in `Job.results` field after training completes.

| Metric | Status | Source | Unit |
|--------|--------|--------|------|
| `best_f1` | ✅ Job.results | computed | ratio |
| `best_epoch` | ✅ Job.results | computed | epoch number |
| `train_runtime` | ✅ Job.results | `trainer_state.json` | seconds |
| `total_flos` | ✅ Job.results | `trainer_state.json` | FLOPs |
| `early_stopping_triggered` | ✅ Job.results | computed | boolean |
| `best_checkpoint_s3_path` | ✅ Job.results | computed | S3 URI |

**Storage format:** Updated on Job entity after training:
```python
job.status = "succeeded"
job.results = {
    "best_f1": 0.92,
    "best_epoch": 7,
    "train_runtime": 3847.5,
    "total_flos": 1200000000000000,
    "early_stopping_triggered": True,
    "best_checkpoint_s3_path": "s3://bucket/runs/job-name/best/",
}
dynamo.update_job(job)
```

**Query examples:**
```python
# Get a job and check its results
job = dynamo.get_job(job_id)
if job.results:
    print(f"Best F1: {job.results['best_f1']}")
    print(f"Training time: {job.results['train_runtime']:.1f}s")
```

## GPU/Resource Metrics

Hardware utilization metrics. Not currently captured.

| Metric | Status | Source | Notes |
|--------|--------|--------|-------|
| `gpu_memory_used` | ❌ Not captured | `torch.cuda.memory_allocated()` | Peak GPU usage |
| `gpu_utilization` | ❌ Not captured | nvidia-smi or SageMaker | Compute efficiency |
| `samples_per_second` | ❌ Not captured | HF Trainer | Throughput |
| `steps_per_second` | ❌ Not captured | HF Trainer | Training speed |

**Note:** SageMaker captures some of these in CloudWatch metrics automatically.

## Dataset Quality Metrics

Dataset statistics captured at training start as `JobMetric` records with `epoch=None`.

| Metric | Status | Source | Unit | Notes |
|--------|--------|--------|------|-------|
| `num_train_samples` | ✅ Implemented | `_count_labels()` | count | Training set size (num_lines) |
| `num_val_samples` | ✅ Implemented | `_count_labels()` | count | Validation set size (num_lines) |
| `o_entity_ratio_train` | ✅ Implemented | `_count_labels()` | ratio | Class imbalance in training set |
| `o_entity_ratio_val` | ✅ Implemented | `_count_labels()` | ratio | Class imbalance in validation set |

**Implementation:** `receipt_layoutlm/trainer.py` → written after JobLog config entry.

**Batch Write:** All 4 metrics written in single `add_job_metrics()` call at training start.

**Query examples:**
```python
# Find jobs with high class imbalance
metrics, _ = dynamo.get_metrics_by_name("o_entity_ratio_train")
for m in metrics:
    if m.value > 5.0:
        print(f"Job {m.job_id[:8]}: O:entity ratio = {m.value:.2f}")

# Compare dataset sizes across jobs
train_sizes, _ = dynamo.get_metrics_by_name("num_train_samples")
for m in train_sizes:
    print(f"Job {m.job_id[:8]}: {int(m.value)} training samples")
```

## Query Examples

### Compare F1 across training runs
```python
# Get val_f1 for all jobs, sorted by job then timestamp
metrics, _ = dynamo.get_metrics_by_name_across_jobs("val_f1")

for m in metrics:
    print(f"Job {m.job_id[:8]} Epoch {m.epoch}: F1={m.value:.4f}")
```

### Get training curve for a job
```python
# Get all metrics for a specific job
loss_metrics, _ = dynamo.list_job_metrics(job_id, metric_name="train_loss")
lr_metrics, _ = dynamo.list_job_metrics(job_id, metric_name="learning_rate")

# Plot training curve
epochs = [m.epoch for m in loss_metrics]
losses = [m.value for m in loss_metrics]
```

### Find best performing jobs
```python
# Get all val_f1 metrics
metrics, _ = dynamo.get_metrics_by_name("val_f1")

# Group by job and find max F1 per job
from collections import defaultdict
job_best = defaultdict(float)
for m in metrics:
    job_best[m.job_id] = max(job_best[m.job_id], m.value)

# Sort by best F1
ranked = sorted(job_best.items(), key=lambda x: x[1], reverse=True)
```

## GSI Structure

`JobMetric` has two GSIs for efficient queries:

| GSI | Key Structure | Use Case |
|-----|---------------|----------|
| GSI1 | `METRIC#{name}` → `{timestamp}` | "All val_f1 scores over time" |
| GSI2 | `METRIC#{name}` → `JOB#{id}#{timestamp}` | "Compare metric across jobs" |
