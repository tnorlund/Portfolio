# AI Agent Training Assistant Guide

This guide outlines what context and tools an AI agent needs to effectively help with LayoutLM model training, optimization, and improvement.

## Overview

An AI agent can help with:
- **Hyperparameter optimization**: Suggesting better learning rates, batch sizes, etc.
- **Training speed optimization**: Parallelization, mixed precision, gradient accumulation
- **Model analysis**: Identifying overfitting, underfitting, convergence issues
- **Data quality insights**: Label distribution, class imbalance, dataset size recommendations
- **Experiment tracking**: Comparing runs, identifying patterns, suggesting next steps

## Required Context for the Agent

### 1. **Training Infrastructure & Setup**

The agent needs to understand:
- **EC2 Instance**: g5.xlarge with A10G GPU, Ubuntu AMI
- **Auto Scaling Group**: `layoutlm-asg-<suffix>` (retrieved from Pulumi)
- **S3 Bucket**: Stores wheels, models, checkpoints (from Pulumi output)
- **DynamoDB Table**: Stores job metadata, metrics, logs
- **CLI Tool**: `layoutlm-cli train` command interface
- **Conda Environment**: `layoutlm_training` with specific Python/PyTorch versions

**How to provide this:**
```python
# Agent context file: agent_context/infrastructure.json
{
  "ec2_instance_type": "g5.xlarge",
  "gpu": "A10G",
  "ami": "ubuntu",
  "asg_name": "layoutlm-asg-871b451",  # From: pulumi stack output layoutlm_training_asg_name
  "s3_bucket": "layoutlm-models-1c8f680",  # From: pulumi stack output layoutlm_training_bucket
  "dynamo_table": "ReceiptsTable-dc5be22",  # From: pulumi stack output dynamodb_table_name
  "conda_env": "layoutlm_training",
  "python_version": "3.11",
  "pytorch_version": "2.5.1",
  "transformers_version": "4.40.0"
}
```

### 2. **Current Training Configuration**

The agent needs access to:
- **Hyperparameters**: Learning rate, batch size, epochs, warmup ratio, label smoothing, O:entity ratio
- **Data configuration**: Allowed labels, label merging strategy, dataset snapshot paths
- **Model configuration**: Pretrained model name, max sequence length, tokenizer settings

**How to provide this:**
```python
# Agent can query DynamoDB for latest job config
from receipt_dynamo import DynamoClient

dynamo = DynamoClient(table_name="ReceiptsTable-dc5be22")
latest_job = dynamo.get_latest_job_by_status("succeeded")
current_config = latest_job.job_config
```

**Or provide a template:**
```json
{
  "hyperparameters": {
    "learning_rate": 6e-5,
    "batch_size": 64,
    "epochs": 20,
    "warmup_ratio": 0.2,
    "label_smoothing": 0.1,
    "o_entity_ratio": 2.0,
    "early_stopping_patience": 5
  },
  "data_config": {
    "allowed_labels": ["MERCHANT_NAME", "PHONE_NUMBER", "ADDRESS_LINE", "DATE", "TIME", "PRODUCT_NAME", "AMOUNT"],
    "merge_amounts": true,
    "merge_date_time": false,
    "merge_address_phone": false
  },
  "model_config": {
    "pretrained_model": "microsoft/layoutlm-base-uncased",
    "max_seq_length": 512,
    "num_labels": 8  # O + 7 labels
  }
}
```

### 3. **Historical Training Results**

The agent needs access to:
- **All previous job runs**: Job IDs, configs, final metrics
- **Per-epoch metrics**: F1, precision, recall, loss for each epoch
- **Training logs**: Error messages, warnings, convergence patterns
- **Best performing runs**: What worked and what didn't

**How to provide this:**
```python
# Agent helper function to get training history
def get_training_history(dynamo: DynamoClient, limit: int = 50):
    """Get all training jobs with their metrics."""
    jobs = dynamo.list_jobs_by_status("succeeded")[:limit]
    history = []

    for job in jobs:
        metrics = dynamo.list_job_metrics(job.job_id)
        best_metric = max(metrics, key=lambda m: m.value) if metrics else None

        history.append({
            "job_id": job.job_id,
            "job_name": job.name,
            "created_at": job.created_at,
            "config": job.job_config,
            "best_f1": best_metric.value if best_metric else None,
            "best_epoch": best_metric.epoch if best_metric else None,
            "all_metrics": [
                {
                    "epoch": m.epoch,
                    "f1": m.value,
                    "timestamp": m.timestamp
                }
                for m in metrics
            ]
        })

    return history
```

**Or provide a summary file:**
```markdown
# Training History Summary

## Best Run (Nov 2025)
- Job ID: 49a5ce3b-4653-4aad-95da-11c9a7345b85
- F1: 0.7021 (70.21%)
- Epoch: 3
- Config: batch=64, lr=6e-5, o_entity=2.0, merged_amounts

## Previous Best (Sept 2025)
- F1: 0.32 (32%)
- Config: batch=128, lr=6e-5, o_entity=2.0

## Key Findings
- Batch 64 outperforms batch 128
- Merging amounts helps significantly
- Dataset size is critical (10,682 VALID labels in Nov vs fewer in Sept)
```

### 4. **Dataset Statistics**

The agent needs to understand:
- **Label distribution**: Count of each label type (VALID status)
- **Dataset size**: Number of receipts, lines, words
- **Train/val split**: How data is split (receipt-level vs line-level)
- **Class imbalance**: Ratio of O tokens to entity tokens
- **Data quality**: Validation status distribution

**How to provide this:**
```python
# Agent helper function to get dataset stats
def get_dataset_stats(dynamo: DynamoClient):
    """Get current dataset statistics."""
    # Query DynamoDB for label counts
    label_counts = {}
    for label in CORE_LABELS.keys():
        count = dynamo.count_labels_by_category_and_status(label, "VALID")
        label_counts[label] = count

    # Get receipt counts
    receipt_count = dynamo.count_receipts()

    return {
        "label_counts": label_counts,
        "total_receipts": receipt_count,
        "total_valid_labels": sum(label_counts.values()),
        "class_imbalance": {
            "o_ratio": 2.0,  # From config
            "entity_count": sum(label_counts.values())
        }
    }
```

**Or provide a snapshot:**
```json
{
  "dataset_stats": {
    "total_receipts": 500,
    "total_lines": 9500,
    "label_counts": {
      "MERCHANT_NAME": 500,
      "PHONE_NUMBER": 350,
      "ADDRESS_LINE": 450,
      "DATE": 500,
      "TIME": 400,
      "PRODUCT_NAME": 4534,
      "AMOUNT": 2815
    },
    "total_valid_labels": 9549,
    "train_val_split": "receipt-level 90/10"
  }
}
```

### 5. **Current Training Run Status**

The agent needs real-time access to:
- **Active job status**: Running, pending, failed
- **Current epoch**: What epoch is training now
- **Live metrics**: Latest F1, loss, learning rate
- **Training logs**: Recent log messages, errors, warnings
- **GPU utilization**: Memory usage, compute utilization

**How to provide this:**
```python
# Agent helper function to monitor active training
def get_active_training_status(dynamo: DynamoClient):
    """Get status of currently running training job."""
    running_jobs = dynamo.list_jobs_by_status("running")

    if not running_jobs:
        return None

    job = running_jobs[0]  # Assume one active job
    latest_metrics = dynamo.list_job_metrics(job.job_id, limit=1)
    latest_logs = dynamo.list_job_logs(job.job_id, limit=10)

    return {
        "job_id": job.job_id,
        "job_name": job.name,
        "status": job.status,
        "started_at": job.created_at,
        "latest_epoch": latest_metrics[0].epoch if latest_metrics else None,
        "latest_f1": latest_metrics[0].value if latest_metrics else None,
        "recent_logs": [log.message for log in latest_logs]
    }
```

### 6. **Codebase Context**

The agent needs to understand:
- **Training code**: `receipt_layoutlm/receipt_layoutlm/trainer.py`
- **Data loading**: `receipt_layoutlm/receipt_layoutlm/data_loader.py`
- **CLI interface**: `receipt_layoutlm/receipt_layoutlm/cli.py`
- **Configuration**: `receipt_layoutlm/receipt_layoutlm/config.py`
- **DynamoDB entities**: Job, JobMetric, JobLog structures

**How to provide this:**
- Point agent to codebase root: `/Users/tnorlund/Portfolio`
- Agent can read files directly using file reading tools
- Provide key file paths:
  - `receipt_layoutlm/receipt_layoutlm/trainer.py`
  - `receipt_layoutlm/receipt_layoutlm/data_loader.py`
  - `receipt_layoutlm/receipt_layoutlm/cli.py`
  - `docs/LAYOUTLM_RETRAINING_GUIDE.md`
  - `docs/layoutlm/LAYOUTLM_TRAINING_STRATEGY.md`

## Required Tools & Access

### 1. **DynamoDB Access**

The agent needs to:
- Query job records: `list_jobs_by_status()`, `get_job()`
- Query metrics: `list_job_metrics()`, `get_job_metric()`
- Query logs: `list_job_logs()`
- Count labels: `count_labels_by_category_and_status()`

**Setup:**
```python
from receipt_dynamo import DynamoClient

dynamo = DynamoClient(
    table_name="ReceiptsTable-dc5be22",
    region="us-east-1"
)
```

### 2. **S3 Access**

The agent needs to:
- List model checkpoints: `s3://layoutlm-models-1c8f680/runs/`
- Read training logs: `s3://layoutlm-models-1c8f680/runs/{job_name}/run.json`
- Check model artifacts: Checkpoint directories, config files

**Setup:**
```python
import boto3

s3_client = boto3.client('s3', region_name='us-east-1')
bucket = "layoutlm-models-1c8f680"
```

### 3. **EC2 Access (Optional)**

For real-time monitoring, the agent could:
- SSH into EC2 instance (if credentials provided)
- Monitor GPU usage: `nvidia-smi`
- Check training logs: `tail -f /tmp/receipt_layoutlm/{job_name}/trainer.log`
- Monitor system resources: CPU, memory, disk

**Note**: This requires SSH key access and is optional. Most monitoring can be done via DynamoDB.

### 4. **Pulumi Access (Optional)**

To get infrastructure details:
- Get ASG name: `pulumi stack output layoutlm_training_asg_name`
- Get S3 bucket: `pulumi stack output layoutlm_training_bucket`
- Get DynamoDB table: `pulumi stack output dynamodb_table_name`

**Setup:**
```bash
# Agent can run these commands if Pulumi is configured
cd infra
pulumi stack select dev
```

## Agent Capabilities & Use Cases

### 1. **Hyperparameter Optimization**

**What the agent can do:**
- Analyze historical runs to find best hyperparameters
- Suggest improvements based on current performance
- Recommend learning rate schedules
- Optimize batch size for GPU memory

**Example interaction:**
```
User: "My F1 is stuck at 0.56 after 10 epochs. What should I change?"

Agent:
1. Analyzes current run metrics from DynamoDB
2. Compares to historical best (0.70 F1)
3. Identifies: Learning rate might be too high (loss not decreasing)
4. Suggests: Try lr=5e-5 with longer warmup (0.3)
5. Provides exact CLI command with new hyperparameters
```

### 2. **Training Speed Optimization**

**What the agent can do:**
- Suggest gradient accumulation for larger effective batch size
- Recommend mixed precision training (fp16/bf16)
- Optimize data loading (num_workers, prefetch_factor)
- Suggest model compilation (torch.compile) if compatible

**Example interaction:**
```
User: "Training is taking 25 minutes per epoch. Can we make it faster?"

Agent:
1. Checks current config: batch_size=64, fp16=False
2. Suggests: Enable fp16 (2x speedup on A10G)
3. Suggests: Increase gradient_accumulation_steps=2 (effective batch=128)
4. Suggests: Increase num_workers=4 for data loading
5. Estimates: 12-15 minutes per epoch (40-50% faster)
```

### 3. **Model Analysis & Debugging**

**What the agent can do:**
- Detect overfitting (train loss decreasing, val F1 plateauing)
- Identify underfitting (both train and val F1 low)
- Analyze class imbalance issues
- Suggest data augmentation strategies

**Example interaction:**
```
User: "Why did F1 drop from 0.57 to 0.55 after epoch 15?"

Agent:
1. Analyzes metrics: val_loss increasing, train_loss still decreasing
2. Identifies: Overfitting (model memorizing training data)
3. Suggests: Early stopping should have triggered (patience=5)
4. Recommends: Use checkpoint from epoch 10 (best F1)
5. Suggests: Add dropout or label smoothing for next run
```

### 4. **Experiment Tracking & Comparison**

**What the agent can do:**
- Compare multiple runs side-by-side
- Identify patterns across experiments
- Suggest next experiment based on results
- Track what hyperparameters correlate with better F1

**Example interaction:**
```
User: "What's the best batch size for my dataset?"

Agent:
1. Queries all historical runs from DynamoDB
2. Groups by batch_size: 64, 128, 32
3. Finds: batch=64 averaged 0.65 F1, batch=128 averaged 0.45 F1
4. Recommends: batch=64 (consistent best performer)
5. Provides statistical analysis: p-value, confidence intervals
```

### 5. **Data Quality Insights**

**What the agent can do:**
- Analyze label distribution and class imbalance
- Identify underrepresented labels
- Suggest which labels to merge or split
- Recommend minimum dataset size for target F1

**Example interaction:**
```
User: "Should I add PRODUCT_NAME to my training?"

Agent:
1. Checks dataset: PRODUCT_NAME has 4,534 VALID labels (largest class)
2. Checks current config: Not in allowed_labels
3. Analyzes: Adding it would increase from 4 to 5 labels
4. Recommends: Yes, but start with 4 labels until F1 > 0.65
5. Suggests: Two-stage approach (train on 4 labels, then fine-tune on 5)
```

## Agent Helper Scripts

Create these scripts to make the agent's job easier:

### 1. **Training History Analyzer**

```python
# scripts/agent_analyze_training_history.py
"""Analyze training history and provide insights for AI agent."""

from receipt_dynamo import DynamoClient
import json
import sys

def analyze_training_history(table_name: str, output_file: str = None):
    """Analyze all training jobs and output JSON for agent."""
    dynamo = DynamoClient(table_name=table_name)

    jobs = dynamo.list_jobs_by_status("succeeded")
    history = []

    for job in jobs:
        metrics = dynamo.list_job_metrics(job.job_id)
        if not metrics:
            continue

        best_metric = max(metrics, key=lambda m: m.value)
        epoch_metrics = sorted(metrics, key=lambda m: m.epoch or 0)

        history.append({
            "job_id": job.job_id,
            "job_name": job.name,
            "created_at": job.created_at,
            "config": job.job_config,
            "best_f1": best_metric.value,
            "best_epoch": best_metric.epoch,
            "epochs": [
                {
                    "epoch": m.epoch,
                    "f1": m.value,
                    "timestamp": m.timestamp
                }
                for m in epoch_metrics
            ]
        })

    output = {
        "total_runs": len(history),
        "runs": sorted(history, key=lambda x: x["best_f1"], reverse=True)
    }

    if output_file:
        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2)
    else:
        print(json.dumps(output, indent=2))

    return output

if __name__ == "__main__":
    table_name = sys.argv[1] if len(sys.argv) > 1 else "ReceiptsTable-dc5be22"
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    analyze_training_history(table_name, output_file)
```

### 2. **Dataset Statistics Reporter**

```python
# scripts/agent_dataset_stats.py
"""Get current dataset statistics for AI agent."""

from receipt_dynamo import DynamoClient
from receipt_layoutlm.receipt_layoutlm.data_loader import CORE_LABELS
import json
import sys

def get_dataset_stats(table_name: str, output_file: str = None):
    """Get dataset statistics and output JSON for agent."""
    dynamo = DynamoClient(table_name=table_name)

    label_counts = {}
    for label in CORE_LABELS.keys():
        count = dynamo.count_labels_by_category_and_status(label, "VALID")
        label_counts[label] = count

    receipt_count = dynamo.count_receipts()

    stats = {
        "total_receipts": receipt_count,
        "label_counts": label_counts,
        "total_valid_labels": sum(label_counts.values()),
        "label_distribution": {
            label: count / sum(label_counts.values())
            for label, count in label_counts.items()
        }
    }

    if output_file:
        with open(output_file, 'w') as f:
            json.dump(stats, f, indent=2)
    else:
        print(json.dumps(stats, indent=2))

    return stats

if __name__ == "__main__":
    table_name = sys.argv[1] if len(sys.argv) > 1 else "ReceiptsTable-dc5be22"
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    get_dataset_stats(table_name, output_file)
```

### 3. **Active Training Monitor**

```python
# scripts/agent_monitor_training.py
"""Monitor active training job for AI agent."""

from receipt_dynamo import DynamoClient
import json
import sys
from datetime import datetime

def monitor_active_training(table_name: str, output_file: str = None):
    """Get status of currently running training job."""
    dynamo = DynamoClient(table_name=table_name)

    running_jobs = dynamo.list_jobs_by_status("running")

    if not running_jobs:
        status = {"status": "no_active_jobs"}
    else:
        job = running_jobs[0]
        metrics = dynamo.list_job_metrics(job.job_id)
        logs = dynamo.list_job_logs(job.job_id, limit=20)

        latest_metric = max(metrics, key=lambda m: m.epoch or 0) if metrics else None

        status = {
            "job_id": job.job_id,
            "job_name": job.name,
            "status": job.status,
            "started_at": job.created_at,
            "config": job.job_config,
            "latest_epoch": latest_metric.epoch if latest_metric else None,
            "latest_f1": latest_metric.value if latest_metric else None,
            "all_epochs": [
                {
                    "epoch": m.epoch,
                    "f1": m.value,
                    "timestamp": m.timestamp
                }
                for m in sorted(metrics, key=lambda m: m.epoch or 0)
            ],
            "recent_logs": [log.message for log in logs[-10:]]
        }

    if output_file:
        with open(output_file, 'w') as f:
            json.dump(status, f, indent=2)
    else:
        print(json.dumps(status, indent=2))

    return status

if __name__ == "__main__":
    table_name = sys.argv[1] if len(sys.argv) > 1 else "ReceiptsTable-dc5be22"
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    monitor_active_training(table_name, output_file)
```

## Making Training Faster

### 1. **Enable Mixed Precision (fp16)**

**Current**: Not enabled
**Impact**: ~2x speedup on A10G GPU
**How to enable**: Already in code, just need to verify `fp16=True` in TrainingArguments

```python
# In trainer.py, TrainingArguments should have:
training_args = TrainingArguments(
    ...
    fp16=True,  # Enable mixed precision
    ...
)
```

### 2. **Optimize Data Loading**

**Current**: Default num_workers
**Impact**: 10-20% speedup
**How to optimize**:

```python
# In trainer.py, DataLoader should have:
train_dataloader = DataLoader(
    train_dataset,
    batch_size=batch_size,
    num_workers=4,  # Parallel data loading
    pin_memory=True,  # Faster GPU transfer
    prefetch_factor=2,  # Prefetch batches
)
```

### 3. **Gradient Accumulation for Larger Effective Batch**

**Current**: gradient_accumulation_steps=1
**Impact**: Can use larger effective batch without OOM
**How to use**:

```bash
# If you want effective batch=128 but GPU can only fit 64:
layoutlm-cli train \
  --batch-size 64 \
  --gradient-accumulation-steps 2  # Effective batch = 64 * 2 = 128
```

### 4. **Model Compilation (if compatible)**

**Current**: Disabled due to Triton issues
**Impact**: 10-30% speedup if working
**Status**: Currently disabled, but can be re-enabled if Triton is fixed

### 5. **Reduce Evaluation Frequency**

**Current**: Evaluates every epoch
**Impact**: Saves ~16 seconds per evaluation
**How to optimize**:

```python
# In trainer.py:
training_args = TrainingArguments(
    ...
    evaluation_strategy="steps",  # Instead of "epoch"
    eval_steps=500,  # Evaluate every 500 steps instead of every epoch
    ...
)
```

### 6. **Use Dataset Snapshots**

**Current**: Loads from DynamoDB each time
**Impact**: Saves 1-2 minutes per training run
**How to use**:

```bash
# First run: Create snapshot
layoutlm-cli train --dataset-snapshot-save /tmp/dataset_snapshot ...

# Subsequent runs: Load from snapshot
layoutlm-cli train --dataset-snapshot-load /tmp/dataset_snapshot ...
```

## Example Agent Workflow

### Scenario: User wants to improve F1 from 0.56 to 0.65+

**Step 1: Agent gathers context**
```python
# Agent runs helper scripts
history = analyze_training_history("ReceiptsTable-dc5be22")
stats = get_dataset_stats("ReceiptsTable-dc5be22")
active = monitor_active_training("ReceiptsTable-dc5be22")
```

**Step 2: Agent analyzes**
- Current F1: 0.56 (epoch 10)
- Best historical: 0.70 (batch=64, lr=6e-5)
- Current config: batch=128, lr=6e-5
- Dataset: 9,549 VALID labels

**Step 3: Agent suggests**
- Change batch size from 128 to 64 (historical best)
- Reduce learning rate to 5e-5 (current might be too high)
- Increase epochs to 25 (more time to converge)
- Enable fp16 for faster training

**Step 4: Agent provides command**
```bash
layoutlm-cli train \
  --job-name "receipts-$(date +%F-%H%M)-agent-optimized" \
  --dynamo-table "ReceiptsTable-dc5be22" \
  --epochs 25 \
  --batch-size 64 \
  --lr 5e-5 \
  --warmup-ratio 0.2 \
  --label-smoothing 0.1 \
  --o-entity-ratio 2.0 \
  --merge-amounts \
  --early-stopping-patience 5 \
  --allowed-label MERCHANT_NAME \
  --allowed-label PHONE_NUMBER \
  --allowed-label ADDRESS_LINE \
  --allowed-label DATE \
  --allowed-label TIME \
  --allowed-label PRODUCT_NAME \
  --allowed-label AMOUNT
```

**Step 5: Agent monitors**
- Watches DynamoDB for new metrics
- Alerts if F1 plateaus or decreases
- Suggests early stopping if needed

## Next Steps

1. **Create agent helper scripts** (see above)
2. **Set up agent context files** (infrastructure.json, config template)
3. **Test agent with sample queries**:
   - "What's the best batch size?"
   - "Why is my F1 stuck at 0.56?"
   - "How can I make training faster?"
4. **Integrate agent into training workflow**:
   - Run before training: Get recommendations
   - Run during training: Monitor and alert
   - Run after training: Analyze results and suggest next steps

## Summary

An AI agent can be highly effective at helping with LayoutLM training if it has:
- ✅ Access to DynamoDB (jobs, metrics, logs)
- ✅ Access to S3 (checkpoints, training logs)
- ✅ Historical training results
- ✅ Current dataset statistics
- ✅ Codebase context (training code, data loading)
- ✅ Helper scripts for data gathering

The agent can then:
- 🎯 Optimize hyperparameters based on historical data
- ⚡ Suggest training speed improvements
- 📊 Analyze model performance and detect issues
- 🔬 Compare experiments and identify patterns
- 💡 Provide data quality insights

