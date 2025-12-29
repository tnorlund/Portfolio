# Project Context

## Current Focus: PR #567 - SageMaker Training Infrastructure

**Branch:** `feat/sagemaker-training`
**PR:** https://github.com/tnorlund/Portfolio/pull/567

### Summary
Adding SageMaker-based training infrastructure for LayoutLM models with GPU support (ml.g5.xlarge spot instances) and comprehensive training metrics recording to DynamoDB.

### Key Components

- **infra/sagemaker_training/** - New Pulumi component for SageMaker infrastructure
  - `component.py` - Main infrastructure (ECR, CodeBuild, Lambda, IAM roles)
  - `train.py` - Training script for BYOC container
  - `Dockerfile` - Container definition

- **receipt_dynamo/** - DynamoDB entities and data layer
  - `entities/job.py` - Job entity with new `results` field
  - `data/_job_metric.py` - JobMetric for training metrics

- **receipt_layoutlm/** - LayoutLM training package
  - `trainer.py` - Enhanced with metrics collection
  - `METRICS.md` - Documentation of all training metrics

### Metrics Tracked
- Per-epoch: val_f1, val_precision, val_recall, eval_loss, train_loss, learning_rate
- Per-label: label_{NAME} with f1/precision/recall/support
- Dataset quality: num_train_samples, num_val_samples, o_entity_ratio_train/val
- Summary: best_f1, best_epoch, train_runtime, total_flos (stored in Job.results)

### Deployment
1. Deploy Pulumi stack to create SageMaker infrastructure
2. CodeBuild builds Docker image on first deployment
3. Start training via Lambda invocation
