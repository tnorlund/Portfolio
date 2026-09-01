# FIX_REPORT: receipt_embeddings missing from Lambda container images

Branch: `fix/docker-receipt-embeddings` (based on `main` @ ab9bfb067).

## Problem

The Round B relocation turned `receipt_chroma.embedding.{formatting,openai}` into
shims that import the `receipt_embeddings` package, and Round C made
`receipt_upload`'s merchant resolver import `receipt_embeddings.dynamo_client` —
but no Dockerfile copied or installed `receipt_embeddings`, so all 12 CodeBuild
image pipelines that ship `receipt_chroma`/`receipt_upload`/`receipt_agent`
failed. `receipt_chroma/pyproject.toml` also declares `receipt-embeddings` as a
hard dependency, so installing it first additionally prevents pip from trying to
resolve that requirement from PyPI.

## Fix

For each affected image:

1. **Dockerfile**: `COPY receipt_embeddings/ /tmp/receipt_embeddings/` added
   alongside the existing package COPYs, and
   `pip install --no-cache-dir --no-deps /tmp/receipt_embeddings` inserted
   immediately after the `receipt_dynamo`(`_stream`) install and **before** the
   `receipt_chroma`/`receipt_upload` installs; `/tmp/receipt_embeddings` added to
   the cleanup `rm -rf`. `--no-deps` is safe: boto3/receipt-dynamo are installed
   beforehand and numpy/openai are pulled in by `receipt_chroma`.
2. **Pulumi component**: `"receipt_embeddings"` added to the image's
   `source_paths` list so the rsynced build context contains the directory and
   the content hash rebuilds when it changes.

## Files touched (11 Dockerfiles + 11 Pulumi files)

| Image | Dockerfile | Pulumi source_paths |
|---|---|---|
| chromadb compaction | `infra/chromadb_compaction/lambdas/Dockerfile` | `infra/chromadb_compaction/components/docker_image.py` |
| fix_place | `infra/fix_place_lambda/lambdas/Dockerfile` | `infra/fix_place_lambda/infrastructure.py` |
| unified label evaluator | `infra/label_evaluator_step_functions/lambdas/Dockerfile.unified` | `infra/label_evaluator_step_functions/infrastructure.py` |
| label refresh | `infra/label_refresh_lambda/lambdas/Dockerfile` | `infra/label_refresh_lambda/infrastructure.py` |
| mcp_server | `infra/mcp_server_lambda/lambdas/Dockerfile` | `infra/mcp_server_lambda/infrastructure.py` |
| merge_receipt | `infra/merge_receipt_lambda/lambdas/Dockerfile` | `infra/merge_receipt_lambda/infrastructure.py` |
| qa_agent run-question | `infra/qa_agent_step_functions/lambdas/Dockerfile` | `infra/qa_agent_step_functions/infrastructure.py` |
| resegment_receipt | `infra/resegment_receipt_lambda/lambdas/Dockerfile` | `infra/resegment_receipt_lambda/infrastructure.py` |
| address similarity cache | `infra/routes/address_similarity_cache_generator/lambdas/Dockerfile` | `infra/routes/address_similarity_cache_generator/infra.py` |
| word similarity cache | `infra/routes/word_similarity_cache_generator/lambdas/Dockerfile` | `infra/routes/word_similarity_cache_generator/infra.py` |
| container OCR (process-ocr-results) | `infra/upload_images/container_ocr/Dockerfile` | `infra/upload_images/infra.py` (line ~634 image) |

**Deliberately untouched:** `infra/glyph_mcp_lambda` — its Dockerfile vendors a
single numpy-only module (`bitmap_font.py`) out of `receipt_agent/` and installs
neither `receipt_chroma` nor `receipt_upload` nor the `receipt_agent` package,
so it cannot hit the missing import. `infra/upload_images/infra.py` line 331
(`upload_receipt` image, `source_paths=["receipt_dynamo"]`) is a different
dynamo-only image and was left alone.

## Grep proofs (COPY present; embeddings install ordered before chroma/upload)

Line numbers from `grep -n 'receipt_embeddings\|receipt_chroma\|receipt_upload' <Dockerfile> | grep -E 'COPY|pip install'`:

```
infra/chromadb_compaction/lambdas/Dockerfile
  14:COPY receipt_embeddings/  →  20:pip install --no-deps embeddings  →  21:pip install chroma
infra/fix_place_lambda/lambdas/Dockerfile
  12:COPY receipt_embeddings/  →  25:embeddings  →  26:chroma  →  28:upload
infra/label_evaluator_step_functions/lambdas/Dockerfile.unified
  14:COPY receipt_embeddings/  →  26:embeddings  →  27:chroma
infra/label_refresh_lambda/lambdas/Dockerfile
  6:COPY receipt_embeddings/   →  9:embeddings   →  10:chroma
infra/mcp_server_lambda/lambdas/Dockerfile
  12:COPY receipt_embeddings/  →  25:embeddings  →  26:chroma  →  28:upload
infra/merge_receipt_lambda/lambdas/Dockerfile
  12:COPY receipt_embeddings/  →  20:embeddings  →  21:chroma  →  23:upload
infra/qa_agent_step_functions/lambdas/Dockerfile
  5:COPY receipt_embeddings/   →  11:embeddings  →  12:chroma
infra/resegment_receipt_lambda/lambdas/Dockerfile
  7:COPY receipt_embeddings/   →  14:embeddings  →  15:chroma  →  17:upload
infra/routes/address_similarity_cache_generator/lambdas/Dockerfile
  12:COPY receipt_embeddings/  →  20:embeddings  →  21:chroma
infra/routes/word_similarity_cache_generator/lambdas/Dockerfile
  12:COPY receipt_embeddings/  →  20:embeddings  →  21:chroma
infra/upload_images/container_ocr/Dockerfile
  12:COPY receipt_embeddings/  →  21:embeddings  →  22:chroma  →  25:upload
```

Every `pip install ... /tmp/receipt_embeddings` line is
`pip install --no-cache-dir --no-deps /tmp/receipt_embeddings`.

## Other checks

- `python3 -m py_compile` passes over all 11 touched infra `.py` files.
- Diff scope is exactly 22 files: the 11 Dockerfiles and the 11 Pulumi
  component files (source_paths lists only). No pulumi commands were run;
  nothing pushed.
