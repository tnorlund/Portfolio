---
name: mac-ocr-worker
description: >-
  Build and run the Swift Mac worker that performs Apple Vision OCR and CoreML
  LayoutLM inference on uploaded receipts. Use when processing the OCR queue,
  running OCR on a local image, debugging model download/caching, or editing
  receipt_ocr_swift/.
---

# Mac OCR + LayoutLM worker

Upload Lambda writes an OCR job to DynamoDB and SQS → the Swift worker downloads
the image from `raw-receipts`, runs Vision OCR and LayoutLM, uploads
`receipts/` and `ocr_results/` to S3, writes `ReceiptWordLabel` and routing
records to DynamoDB, and posts to the OCR results queue.

## Key files

- `receipt_ocr_swift/Sources/ReceiptOCRCLI/main.swift` CLI entry.
- `receipt_ocr_swift/Sources/ReceiptOCRCore/Config/Config.swift` Pulumi-output config loader.
- `receipt_ocr_swift/Sources/ReceiptOCRCore/Worker/OCRWorker.swift` queue worker.
- `receipt_ocr_swift/Sources/ReceiptOCRCore/OCR/VisionOCREngine.swift` Vision OCR.
- `receipt_ocr_swift/Sources/ReceiptOCRCore/LayoutLM/LayoutLMInference.swift` CoreML inference.
- `receipt_ocr_swift/Sources/ReceiptOCRCore/AWS/ModelDownloader.swift` S3 model download + cache.

## Build

```bash
cd receipt_ocr_swift
swift build --configuration release
```

Binary: `receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr`.

## Run against the dev stack

`--env dev` loads queue URLs and LayoutLM model config from Pulumi outputs and
downloads the model from S3 into the local cache.

```bash
BIN=./receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr

$BIN --env dev                                   # one batch (up to 10 images)
$BIN --env dev --continuous --log-level info     # drain the queue
$BIN --env dev --stub-ocr --continuous           # queue-flow test, no real OCR
```

## Run on a local image (no upload)

```bash
$BIN --process-local-image ~/test-receipt.png \
  --output-dir ~/output \
  --layoutlm-model ~/.models/layoutlm \
  --log-level debug
```

## CLI flags

- `--env <dev|prod>` load config from the Pulumi stack. Agents use `dev` only.
- `--continuous` process until the queue is empty.
- `--log-level` trace, debug, info, warn, error.
- `--layoutlm-model` path to a local CoreML model bundle.
- `--layoutlm-cache-path` where to cache the downloaded model (default `.models/layoutlm`).
- `--stub-ocr` skip real OCR.

## Model location

- S3: `s3://<bucket>/coreml/LayoutLM.mlpackage/`
- Local cache: `~/.models/layoutlm/` (or `--layoutlm-cache-path`)

Bundle contents: `LayoutLM.mlpackage/`, `vocab.txt`, `config.json`, `label_map.json`.

## Gotchas

- Timestamps sent to Python must use `yyyy-MM-dd'T'HH:mm:ss.SSSxxx` (produces
  `+00:00`). `XXXXX` produces `Z`, which `datetime.fromisoformat()` rejects.
- LayoutLM output here is layout "furniture" labels; do not feed product labels
  into the section finder (see the `sprouts-line-item-stack` skill).
