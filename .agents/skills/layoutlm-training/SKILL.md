---
name: layoutlm-training
description: >-
  Train LayoutLM token-classification models on receipt OCR data with SageMaker.
  Use when starting or monitoring a LayoutLM training job, tuning hyperparameters,
  choosing label merge presets, or editing receipt_layoutlm/ or
  infra/sagemaker_training/.
---

# LayoutLM training

Pipeline: DynamoDB (labels) → SageMaker GPU training (BYOC container) → S3
checkpoint. Training completion auto-queues a CoreML export (see the
`coreml-export` skill).

## Key files

- `receipt_layoutlm/receipt_layoutlm/trainer.py` training orchestration and metrics collection.
- `receipt_layoutlm/receipt_layoutlm/data_loader.py` DynamoDB data loading and tokenization.
- `receipt_layoutlm/receipt_layoutlm/config.py` training/data config, label merge presets.
- `receipt_layoutlm/receipt_layoutlm/cli.py` `layoutlm-cli` entrypoint.
- `infra/sagemaker_training/component.py` Pulumi component (ECR, CodeBuild, Lambda, IAM).
- `infra/sagemaker_training/train.py` entrypoint inside the SageMaker container.
- `infra/sagemaker_training/Dockerfile` BYOC container definition.

`receipt_layoutlm` is not installed in the default venv (torch). Install with
`pip install -e "receipt_layoutlm[test]"` before running its tests.

## Deploying infra changes

Only when the user explicitly asks for a dev deployment:

```bash
cd infra
pulumi preview --stack tnorlund/portfolio/dev
pulumi up --stack tnorlund/portfolio/dev
```

CodeBuild rebuilds the training image automatically when source files change.
Never touch the prod stack.

## Starting a training job

```bash
aws lambda invoke --function-name layoutlm-sagemaker-start-training-<id> \
  --payload '{
    "job_name": "layoutlm-my-experiment",
    "use_spot": false,
    "hyperparameters": {
      "epochs": "10",
      "batch_size": "8",
      "learning_rate": "5e-5",
      "warmup_ratio": "0.1",
      "early_stopping_patience": "2",
      "merge_amounts": "true"
    }
  }' response.json
```

Job names must match `layoutlm-*` for the auto-export EventBridge rule to fire.
Add the tag `skip-coreml-export: true` to opt out.

## Hyperparameters

- `epochs` (10) number of training epochs.
- `batch_size` (8) per GPU.
- `learning_rate` (5e-5) initial learning rate.
- `warmup_ratio` (0.1) warmup steps as a fraction of total.
- `early_stopping_patience` (2) epochs without improvement before stopping.
- `merge_amounts` (false) merge LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL → AMOUNT.

## Label merge presets (`config.py`)

```python
MERGE_PRESETS = {
    "amounts": {"AMOUNT": ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]},
    "date_time": {"DATE": ["TIME"]},
    "sroie": {  # SROIE-like 4-label setup
        "AMOUNT": [...],
        "DATE": ["TIME"],
        "ADDRESS": ["PHONE_NUMBER", "ADDRESS_LINE"],
    },
}
```

## Monitoring

```bash
aws sagemaker describe-training-job --training-job-name <job-name>

aws logs get-log-events \
  --log-group-name /aws/sagemaker/TrainingJobs \
  --log-stream-name <job-name>/algo-1-<timestamp>
```

Per-epoch metrics (val_f1, val_precision, val_recall, losses, learning rate),
per-label f1/precision/recall/support, and summary values (best_f1, best_epoch,
train_runtime) are written to DynamoDB `JobMetric` records and `Job.results`.

## Related history

- #567 SageMaker training infrastructure.
- #645 / #646 auto-queue CoreML export after training.
- #647 two-pass LayoutLM for hierarchical classification.
