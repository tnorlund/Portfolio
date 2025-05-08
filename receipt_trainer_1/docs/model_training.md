# Model Training with Auto-Scaling GPU Instances

This document describes how to train receipt recognition models using the auto-scaling infrastructure with GPU EC2 spot instances.

## Overview

The training system consists of these components:

1. **SQS Queue**: Holds training jobs waiting to be processed
2. **Auto-Scaling Manager**: Monitors queue depth and launches/terminates EC2 instances as needed
3. **Worker Instances**: GPU-equipped EC2 spot instances that process training jobs
4. **Pulumi Infrastructure**: Provides configuration values and resources

Jobs submitted to the queue will automatically trigger the creation of EC2 instances with GPUs, which will then process the jobs and terminate when the queue is empty.

## Prerequisites

Before you can use the auto-scaling training system, ensure you have:

1. AWS credentials configured (`~/.aws/credentials` or environment variables)
2. Pulumi stack configured with the required resources
3. The `receipt_trainer` package installed

## Single Model Training

To train a single model with specific hyperparameters:

```bash
# Run the end-to-end test (for validation)
ENABLE_REAL_AWS_TESTS=1 python -m pytest receipt_trainer/tests/end_to_end/test_hyperparameter_training.py::test_single_model_training -v

# Or submit a job directly through the CLI
python -m receipt_trainer.scripts.auto_scale --stack dev --start

# Submit a training job to the queue
python -m receipt_trainer.jobs.submit --config examples/training_job.yaml --queue-url <your-queue-url>
```

## Hyperparameter Sweep

To perform a hyperparameter sweep (training multiple models with different hyperparameter combinations):

```bash
# Run a hyperparameter sweep with default settings
python -m receipt_trainer.scripts.hyperparameter_sweep --stack dev --monitor

# Customize the hyperparameter search space
python -m receipt_trainer.scripts.hyperparameter_sweep \
  --stack dev \
  --learning-rates 5e-5,3e-5,1e-5 \
  --batch-sizes 4,8,16 \
  --epochs 3,5,10 \
  --warmup-ratios 0.1,0.2 \
  --monitor

# Dry run (print jobs without submitting)
python -m receipt_trainer.scripts.hyperparameter_sweep --dry-run
```

## Training Job Configuration

A training job YAML file has this structure:

```yaml
# Basic job information
job_name: Train LayoutLM for Receipt OCR
description: Fine-tune LayoutLM model for receipt field extraction
tags:
  project: receipt-ocr
  environment: production
  owner: data-science-team

# Model specification
model: microsoft/layoutlm-base-uncased

# Training configuration
training_config:
  num_epochs: 5
  batch_size: 8
  learning_rate: 5.0e-5
  weight_decay: 0.01
  gradient_accumulation_steps: 2
  fp16: true
  early_stopping_patience: 3

# Data configuration
data_config:
  dataset_name: internal-receipts
  train_split: train
  eval_split: validation
  max_samples: 10000
  use_sliding_window: true
  augment: true
```

## Auto-Scaling Configuration

The auto-scaling system has these parameters:

- `min_instances`: Minimum number of instances to keep running (default: 1)
- `max_instances`: Maximum number of instances allowed (default: 10)
- `cpu_instance_types`: List of CPU instance types to use
- `gpu_instance_types`: List of GPU instance types to use

To modify these parameters:

```bash
python -m receipt_trainer.scripts.auto_scale \
  --stack dev \
  --min-instances 0 \
  --max-instances 4 \
  --start
```

## Monitoring Training Jobs

You can monitor the auto-scaling system and job status:

```bash
# Check auto-scaling status
python -m receipt_trainer.scripts.auto_scale --stack dev --status

# Monitor a hyperparameter sweep
python -m receipt_trainer.scripts.hyperparameter_sweep --stack dev --monitor --monitor-timeout 3600
```

## Optimizing Cost

The auto-scaling system automatically selects the most cost-effective instance types by:

1. Analyzing job requirements to determine if GPU is needed
2. Selecting the cheapest available instance type based on current spot pricing
3. Scaling down instances when the queue is empty

To further optimize costs:

- Use smaller `min_instances` values (0 or 1)
- Choose appropriate `max_instances` based on your budget
- Set job priority appropriately (lower priority jobs will wait longer)
- Use spot instances (the default) rather than on-demand
