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
- `receipt_ocr_swift/Sources/ReceiptOCRCore/LayoutLM/LayoutLMInference.swift` shared tokenization/windowing + backend dispatch.
- `receipt_ocr_swift/Sources/ReceiptOCRCore/LayoutLM/CoreMLLayoutLMBackend.swift` default Core ML backend.
- `receipt_ocr_swift/Sources/ReceiptOCRCore/LayoutLM/CoreAILayoutLMBackend.swift` opt-in Core AI backend (macOS 27+).
- `receipt_ocr_swift/Sources/ReceiptOCRCore/AWS/ModelDownloader.swift` S3 model download + cache.

## Build

```bash
cd receipt_ocr_swift
swift build --configuration release
```

Binary: `receipt_ocr_swift/.build/release/receipt-ocr`.

## Run against the dev stack

`--env dev` loads queue URLs and LayoutLM model config from Pulumi outputs and
downloads the model from S3 into the local cache.

```bash
BIN=./receipt_ocr_swift/.build/release/receipt-ocr

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
- `--layoutlm-model` path to a local model bundle (`.mlpackage` or `.aimodel` + sidecars).
- `--layoutlm-backend` `coreml` (default) or `coreai` (opt-in; requires macOS 27 and a
  worker built with `RECEIPT_OCR_COREAI=1`, see the `coreai-export` skill).
- `--layoutlm-cache-path` where to cache the downloaded model (default `.models/layoutlm`).
- `--stub-ocr` skip real OCR.

Environment overrides (same as Python training): `LAYOUTLM_WINDOW_SIZE` (200),
`LAYOUTLM_WINDOW_STRIDE` (150), `LAYOUTLM_INFERENCE_MODE` (`windowed`),
`LAYOUTLM_BACKEND` (`coreml`|`coreai`).

## Model location

- S3: the versioned archive selected by `coreml/active.json`; the legacy
  bundle key is `coreml/layoutlm-coreml-bundle.zip`.
- Local cache: `.models/layoutlm/<env>/` relative to the worker's working
  directory (or the explicit `--layoutlm-cache-path`). Verify the process cwd.

Core ML bundle contents: `LayoutLM.mlpackage/`, `vocab.txt`, `config.json`,
`label_map.json`.

Core AI local bundles (experimental): `LayoutLM.aimodel/` plus the same
sidecars. See the `coreai-export` skill. Production S3 pointers still serve
Core ML only.

## Gotchas

- Timestamps sent to Python must use `yyyy-MM-dd'T'HH:mm:ss.SSSxxx` (produces
  `+00:00`). `XXXXX` produces `Z`, which `datetime.fromisoformat()` rejects.
- LayoutLM output here is layout "furniture" labels; do not feed product labels
  into the section finder (see the `sprouts-line-item-stack` skill).
