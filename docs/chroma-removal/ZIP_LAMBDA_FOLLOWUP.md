# Zip-Lambda follow-up (after Chroma teardown)

Chroma is why most receipt Lambdas are container images. Dropping it is what
makes zip packaging possible again. This is a **follow-up after Phase 4**, not
part of the teardown itself, and it does not flip `PackageType` while images
still `pip install receipt_chroma`.

## Why they are images today

AWS Lambda zip packages (function + layers) are capped at **250 MB unzipped**.
`infra/LAMBDA_LAYER_SIZE_ANALYSIS.md` recorded that Chroma's native tree
(ONNX Runtime, Rust bindings, Kubernetes client, …) exceeded that ceiling.

Re-measured 2026-09-02 in a CPython 3.13 x86_64 venv (directional; conversion
must re-measure on Amazon Linux 2023 Python 3.13 arm64):

| Tree | Unzipped |
|---|---|
| Chroma-adjacent (`chromadb`, ONNX Runtime, `chromadb_rust_bindings`, Kubernetes, gRPC, tokenizers) | ~252 MB |
| Post-Chroma fat path (`numpy`, Pillow + AVIF, OpenAI, LangGraph/LangChain, pydantic; boto3/botocore excluded as runtime-provided) | ~153 MB |
| Zip ceiling | 250 MB |

Chroma alone is the 250 MB blocker. The remaining fat path fits with ~100 MB
of headroom on this venv.

## Classifier

```bash
python scripts/lambda_zip_budget.py
```

Buckets:

| Bucket | Meaning |
|---|---|
| `chroma_blocked` | Dockerfile still copies/installs `receipt_chroma`. Zip would ship the native tree. Wait for Phase 4. |
| `already_slim` | No Chroma, no PyTorch. Zip-sized today (e.g. `upload_receipt`, `trigger_reocr`). |
| `stay_image` | PyTorch (LayoutLM inference). Never a zip. |
| `not_lambda` | SageMaker training image. |

## Convert after Phase 4 (once `receipt_chroma` is gone from the image)

- `process_ocr` / container OCR
- merge, resegment, fix-place, receipt MCP, QA `run_question`
- word-similarity and address-similarity cache generators
- label refresh (if the component still exists)

Slim the install to what the handler actually imports (`receipt_agent` +
`receipt_upload` is more than several of these need) and measure unzipped size
on AL2023 arm64 before flipping `PackageType`. Target **< 200 MB** unzipped so
the next native wheel bump does not slam the ceiling.

Optional today, independent of Chroma: `upload_receipt` and `trigger_reocr`
already install only `receipt_dynamo` (~5 MB). Glyph MCP (numpy + PIL) and GA
extract (`google-analytics-data` ~52 MB) are also zip-sized; they are images
for the shared `CodeBuildDockerImage` path, not because of size.

## Do not convert

- LayoutLM inference cache — CPU PyTorch, still hundreds of MB
- SageMaker training — not a Lambda

## Pulumi mechanics

`CodeBuildDockerImage` hard-codes `package_type: Image`. Zip conversion is a
**function replacement**, not a quiet field edit. Same class of 409 / name
collision as the original zip → image migration. Drop any legacy-URN `aliases`
in the same change (SPEC §6 G). Preview on `tnorlund/portfolio/dev` before
apply; do not touch prod from an agent.

`scripts/lambda_zip_budget.py` and `tests/test_lambda_zip_followup.py` fail if
a Chroma-installing Dockerfile is treated as zip-ready, or if LayoutLM loses
its PyTorch marker.
