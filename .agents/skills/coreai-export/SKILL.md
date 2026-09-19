---
name: coreai-export
description: >-
  Convert trained LayoutLM checkpoints to Apple Core AI (.aimodel) for on-device
  inference. Use when setting up the isolated coreai venv, running
  layoutlm-cli export-coreai / validate-coreai, or editing
  receipt_layoutlm/export_coreai.py / validate_coreai.py.
---

# Core AI export (experimental)

LayoutLM v1 → `torch.export` → `coreai-torch` → `LayoutLM.aimodel` bundle.
Opt-in Swift backend via `LAYOUTLM_BACKEND=coreai`. **Core ML remains the
default** until parity is proven.

## Environments (do not mix)

| Env | Python | Purpose |
|-----|--------|---------|
| Main LayoutLM | 3.14 | train / infer / tests; `torch>=2.6,<3` |
| `~/.coreml-venv` | 3.13 | legacy Core ML (`[coreml]`, torch≤2.7) |
| `~/.coreai-venv` | 3.13 | Core AI (`[coreai]`, torch≥2.8) |

`coreai-core` currently requires Python `<3.14`, which is why Core AI export
stays on 3.13 even though the main package supports 3.14.

## Create the Core AI venv

```bash
/usr/local/bin/python3.13 -m venv ~/.coreai-venv
# Quotes protect the extras syntax in zsh.
~/.coreai-venv/bin/pip install -e 'receipt_layoutlm[coreai]'
```

Do **not** install `[training]` or `[coreml]` into this venv.

## Local export

```bash
~/.coreai-venv/bin/layoutlm-cli export-coreai \
  --checkpoint-dir /path/to/checkpoint \
  --output-dir ./coreai-out
```

Fixed `seq_length=512`, float32 only (no quantization yet).

Bundle layout:

```
coreai-out/
├── LayoutLM.aimodel/
├── vocab.txt
├── config.json
├── label_map.json
├── tokenizer_config.json   # when present
└── coreai_export.json
```

## Numerical validation

Conversion success alone is **not** enough:

```bash
~/.coreai-venv/bin/layoutlm-cli validate-coreai \
  --checkpoint-dir /path/to/checkpoint \
  --coreai-bundle ./coreai-out \
  --num-samples 20 \
  --output-json /tmp/coreai-parity.json
```

Compares PyTorch vs Core AI: label agreement, confidence deltas, logit RMSE,
NaN/Inf detection.

## Swift opt-in (macOS 27 / Xcode 27)

The Core AI backend is compiled only when the package is built with
`RECEIPT_OCR_COREAI=1`. Its Swift API (`AIModel`, `InferenceFunction`,
`NDArray`) comes from the `CoreAIRuntime` module that `CoreAI` re-exports; it
is beta and exists only in the macOS 27 SDK, which CI's macOS runner lacks, so
the default build never references it. Without the flag the backend throws
`backendUnavailable` at startup. To see the current API surface:
`xcrun swift-symbolgraph-extract -module-name CoreAIRuntime -target
arm64-apple-macos27.0 -sdk "$(xcrun --show-sdk-path)" -output-dir /tmp/sg`.

```bash
cd receipt_ocr_swift
RECEIPT_OCR_COREAI=1 swift build -c release
LAYOUTLM_BACKEND=coreai \
  ./.build/release/receipt-ocr \
  --process-local-image ~/test-receipt.png \
  --layoutlm-model ./coreai-out \
  --layoutlm-backend coreai \
  --output-dir /tmp/ocr-out
```

`CoreAILayoutLMBackend.swift` compiles against the macOS 27.0 SDK
(Command Line Tools, Swift 6.4) and has run a `.aimodel` exported from a tiny
random LayoutLM v1 checkpoint through `--process-local-image`; a trained
checkpoint has not been exercised yet. Default backend is still Core ML
(`LAYOUTLM_BACKEND=coreml` or omit), and the default build never compiles the
Core AI code.

## Key files

- `receipt_layoutlm/export_coreai.py`
- `receipt_layoutlm/validate_coreai.py`
- `receipt_layoutlm/validate_parity.py` (shared metrics)
- `receipt_layoutlm/model_wrappers.py` (shared LayoutLMWrapper)
- `receipt_ocr_swift/.../CoreAILayoutLMBackend.swift`

## Out of scope for the first landing

- SQS/S3 auto-export (`CoreMLExportJob` rename / `coreai/active.json`)
- LayoutLMv3 Core AI
- Quantization
- Prod stack / production pointer migration

See also: `coreml-export`, `mac-ocr-worker`.
