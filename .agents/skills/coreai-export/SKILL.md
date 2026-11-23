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
| `~/.coreml-venv` | legacy | Core ML (`[coreml]`; see `coreml-export`) |
| `~/.coreai-py314-venv` | 3.14 | Core AI (`[coreai]`, torch 2.13.0) |

The Core AI extra pins the verified exporter stack: `coreai-core==1.0.0`,
`coreai-torch==0.4.4`, `torch==2.13.0` and `transformers==4.57.3`.
Use standard CPython 3.14, not the free-threaded build. The Core AI wheel
targets Apple Silicon on macOS 26+ or x86-64 Linux with glibc 2.34+.
The Swift backend has a separate macOS 27+ requirement.

## Create the Core AI venv

```bash
# From the repository root. Use a fresh venv, not an older Core AI venv.
python3.14 -m venv ~/.coreai-py314-venv
~/.coreai-py314-venv/bin/python -m pip install -e receipt_dynamo -e 'receipt_layoutlm[coreai]'
```

Do **not** install `[training]` or `[coreml]` into this venv.

## Local export

```bash
~/.coreai-py314-venv/bin/layoutlm-cli export-coreai \
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
env -u DYNAMO_TABLE_NAME ~/.coreai-py314-venv/bin/layoutlm-cli validate-coreai \
  --checkpoint-dir /path/to/checkpoint \
  --coreai-bundle ./coreai-out \
  --num-samples 20 \
  --output-json /tmp/coreai-parity.json
```

Compares PyTorch with the Python Core AI runtime: label agreement, confidence
deltas, logit RMSE and NaN/Inf detection. Without a DynamoDB table, validation
uses synthetic inputs. This command does not validate the Swift backend.

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
  --layoutlm-model ../coreai-out \
  --layoutlm-backend coreai \
  --output-dir /tmp/ocr-out
```

On macOS 27.2, the trained dev LayoutLM v1 bundle matched PyTorch using
identical tensors from synthetic inputs. That verifies the Swift backend,
not Swift tokenization, OCR, or real-receipt accuracy. The default backend
is still Core ML (`LAYOUTLM_BACKEND=coreml` or omit), and the default build
never compiles the Core AI code.

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
