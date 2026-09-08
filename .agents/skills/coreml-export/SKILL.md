---
name: coreml-export
description: >-
  Convert trained LayoutLM checkpoints to CoreML for on-device inference. Use
  when queueing or debugging a CoreML export, running the macOS export worker,
  setting up the isolated coreml venv, choosing quantization, or editing
  receipt_layoutlm/export_*.py or infra/coreml_export/.
---

# CoreML export

Pipeline: S3 checkpoint → SQS job queue → Mac export worker → CoreML bundle in
S3. The worker must run on macOS (coremltools requirement).

## Key files

- `receipt_layoutlm/receipt_layoutlm/export_coreml.py` PyTorch → CoreML conversion.
- `receipt_layoutlm/receipt_layoutlm/export_worker.py` SQS-driven export job processor.
- `infra/coreml_export/queue_export.py` Lambda that queues exports on training completion.

## Auto-export (default, since #646)

1. EventBridge rule fires on SageMaker completion for job names matching `layoutlm-*`.
2. `queue_export.py` looks up the Job via `get_job_by_name()`, reads
   `best_checkpoint_s3_path` from `job.results`, writes a `CoreMLExportJob`
   record, and sends the SQS message.
3. The Mac worker polls the queue and processes the export.

Opt out with the training-job tag `skip-coreml-export: true`.

## Manual export

Queue a job:

```bash
aws sqs send-message \
  --queue-url <coreml-export-job-queue-url> \
  --message-body '{
    "export_id": "<uuid>",
    "job_id": "<training-job-name>",
    "model_s3_uri": "s3://bucket/runs/<job>/checkpoint-<step>/",
    "quantize": "float16",
    "output_s3_prefix": "s3://bucket/coreml/<job>/"
  }'
```

Run the worker on the Mac:

```bash
~/.coreml-venv/bin/layoutlm-cli export-worker --once \
  --job-queue-url "<job-queue-url>" \
  --results-queue-url "<results-queue-url>"
# or --continuous
```

Direct local export without SQS:

```bash
layoutlm-cli export-coreml \
  --s3-uri s3://bucket/runs/<job>/checkpoint-<step>/ \
  --output-dir ./output \
  --quantize float16
```

Worker environment variables: `COREML_EXPORT_JOB_QUEUE_URL`,
`COREML_EXPORT_RESULTS_QUEUE_URL`, `DYNAMO_TABLE_NAME`.

## Quantization

- `float16` ~220 MB, best accuracy, default.
- `int8` ~110 MB, good accuracy, size-constrained targets.
- `int4` ~55 MB, lower accuracy, experimental.

## Export-worker venv (keep it isolated)

coremltools 9.0 supports PyTorch through 2.7.0 and scikit-learn only through
1.5.1, but Python 3.13 macOS ARM wheels for scikit-learn start above that. The
worker converts PyTorch models only, so scikit-learn must not be installed there.

```bash
/usr/local/bin/python3.13 -m venv ~/.coreml-venv
# Quotes protect the extras syntax in zsh.
~/.coreml-venv/bin/pip install -e receipt_dynamo -e 'receipt_layoutlm[coreml]'
```

Do not add the `training` extra to this venv (it pulls scikit-learn 1.6+ and
`seqeval`). A clean install resolves coremltools 9.0 with PyTorch 2.7.0 and no
unsupported-version warning.

## Output bundle

```
model-bundle/
├── LayoutLM.mlpackage/   # compiled CoreML model
├── vocab.txt             # BERT tokenizer vocabulary
├── config.json           # label definitions
└── label_map.json        # label id → name
```

Uploaded to `s3://<bucket>/coreml/LayoutLM.mlpackage/`; the Mac OCR worker
downloads it from there (see the `mac-ocr-worker` skill).
