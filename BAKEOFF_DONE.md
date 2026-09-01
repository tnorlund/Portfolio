# Card W — Wiring: dual-run ingest + freshening leg (self-report)

Branch `cards/W-wiring`, 5 commits on current main. No pulumi commands were
run, no dev/prod writes, no index operations.

## Per-item report

### 1. Dual-run ingest (DUAL_WRITE_EMBEDDINGS)

- New module `receipt_upload/receipt_upload/merchant_resolution/dynamo_embedding_write.py`:
  builds engine `EmbeddingWriteRequest`s from the ingest step's **in-memory
  vectors** and invokes `receipt_embeddings.EmbeddingWriter` (zero extra
  OpenAI calls — every request carries its vector, so the writer's embedder
  path is never reached).
- Hook: `MerchantResolvingEmbeddingProcessor._process_embeddings_impl`
  immediately after Phase 1 (vectors computed), **before** the Chroma
  pipeline legs — per the judge's preference the write is independent of
  Chroma's outcome: a Chroma pipeline failure cannot block the Dynamo write,
  and the leg never raises, so it cannot affect the receipt's ingest outcome.
- Vector fidelity: line requests are built from the *same*
  `row_line_ids_list` returned by the grouping that produced
  `row_embeddings` (rows are never re-derived, so a vector can't be paired
  with a different grouping). Word vectors zip `strict=True` against words.
- Metadata at ingest: merchant/place from the pre-fetched `ReceiptPlace`
  (may be empty for fresh receipts), `label_status` from current labels
  (same VALID→validated / PENDING→pending rule as backfill + freshening),
  `section_type=""` (sections don't exist yet), phone/address anchors via
  `enrich_row_metadata_with_anchors`. Card D's freshening leg refreshes all
  of these when the corresponding entities land — including the place the
  resolver writes later in the same ingest.
- Flag: env `DUAL_WRITE_EMBEDDINGS`, string `"true"` enables, default OFF.
- Metric/logging: per-receipt report in the processor result
  (`dual_write` key); the container handler aggregates
  `UploadLambdaDualWriteWritten` / `UploadLambdaDualWriteFailed` into its
  existing EMF batch (Failed is the detection path since the leg is
  non-fatal by design).
- Scope note: the regional re-OCR path re-embeds via
  `create_embeddings_and_compaction_run` directly and is NOT dual-run wired;
  the engine writer skips existing keys (embed-and-put writes only missing
  items), so a re-OCR dual write would be a no-op today. Re-embed-on-change
  lands with the §3.4 cutover, not this card.

### 2. Freshening wiring (card D's leg)

- `infra/chromadb_compaction/lambdas/stream_processor.py`: one call to
  `receipt_dynamo_stream.apply_vector_freshening(event["Records"], metrics)`
  after the existing message-building/publish legs; its stats fold into the
  handler's batched EMF metrics. The leg never raises and is **inert when
  `DYNAMO_TABLE_NAME` is unset** (card D's convention), so stacks that don't
  opt in are unaffected.
- The Lambda gets `receipt_dynamo_stream` from the existing
  `dynamo_stream_layer` (built from the monorepo package dir), which ships
  `vector_freshening` with no packaging change.
- Env: `DYNAMO_TABLE_NAME` added to the stream-processor Lambda in
  `infra/chromadb_compaction/components/lambda_functions.py`, derived from
  the `dynamodb_table_arn` input the component already receives
  (`arn.split("/")[-1]`).
- IAM: **no change needed** — the component's shared dynamodb policy already
  grants `dynamodb:UpdateItem` and `dynamodb:Query` on the table ARN
  (`lambda_functions.py`, `_create_shared_policies`), which is exactly what
  the leg uses.

### 3. Pulumi plumbing

- `infra/upload_images/infra.py`: `portfolio:enable-dual-write-embeddings`
  config → `DUAL_WRITE_EMBEDDINGS` env on the process-ocr container Lambda;
  absent config resolves to `"false"`.
- Stream processor `DYNAMO_TABLE_NAME`: see item 2 (table name reused from
  the component's existing ARN input).
- No pulumi preview/up was run (out of scope per card).

### 4. Tests

- `receipt_upload/tests/test_dual_write_embeddings.py` (7 tests):
  - flag off → `None` returned, zero writer-factory/writer calls;
  - flag on → writer invoked once; every request carries a pre-computed
    vector; row/word vectors are the exact in-memory lists;
  - builder maps metadata (primary line, row_line_ids, label_status,
    place fields, empty section_type);
  - writer failure → error report returned, nothing raised;
  - processor-level independence: with the Chroma pipeline legs failing
    wholesale, the dual write still runs with the Phase-1 vectors and the
    result carries its report while `success=False` for the Chroma leg.
- `infra/chromadb_compaction/lambdas/tests/test_stream_processor_freshening.py`
  (2 tests): handler invokes the leg with the event's records and folds
  `VectorFreshening*` stats into EMF; with `DYNAMO_TABLE_NAME` unset the
  real leg returns zeroed stats and the handler still returns 200.
- Adjacent suites re-run clean (no regressions):
  `test_embedding_failure_propagation.py`,
  `test_embedding_processor_label_hygiene.py`,
  `test_merchant_embedding_processor.py` → 17 passed, 7 skipped
  (pre-existing skips).

## Fresh-checkout verify commands

Requires python3.13 (repo packages pin `<3.14`).

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e receipt_dynamo
pip install --no-deps -e receipt_dynamo_stream -e receipt_embeddings \
  -e receipt_chroma -e receipt_places -e receipt_agent -e receipt_upload
pip install boto3 chromadb "openai>=2.8.1,<3.0.0" Pillow pillow-avif-plugin \
  langsmith langgraph "langchain-core>=0.3.0" "langchain-openai>=0.2.0" httpx \
  pydantic pydantic-settings structlog requests tenacity numpy \
  pytest pytest-mock pytest-timeout moto

# Dual-run unit + independence tests
python -m pytest receipt_upload/tests/test_dual_write_embeddings.py -v

# Freshening handler wiring tests (PYTEST_RUNNING=1 skips the package's
# Pulumi imports — the repo's established convention for these tests)
PYTEST_RUNNING=1 python -m pytest \
  infra/chromadb_compaction/lambdas/tests/test_stream_processor_freshening.py -v

# Adjacent regression suites
python -m pytest \
  infra/upload_images/container_ocr/handler/tests/test_embedding_failure_propagation.py \
  receipt_upload/tests/test_embedding_processor_label_hygiene.py -q

# Syntax over touched infra files
python -m py_compile \
  infra/upload_images/container_ocr/handler/handler.py \
  infra/upload_images/infra.py \
  infra/chromadb_compaction/lambdas/stream_processor.py \
  infra/chromadb_compaction/components/lambda_functions.py
```

All of the above were run locally on this branch and passed
(Python 3.13.15, macOS).

## Not verified locally

- Live dual-run behavior (actual embedding items written during a real
  ingest with the flag on) — judge/deploy validated.
- Pulumi config plumbing (`pulumi preview/up`) — no pulumi commands were
  run per card constraints; env additions are py_compile-checked only.
- The stream-processor Lambda's layer contents at runtime (layer is built
  by CI/deploy; verified only that the layer sources the monorepo
  `receipt_dynamo_stream` package dir, which contains `vector_freshening`).
- Freshening leg's own update semantics — covered by card D's existing
  unit/moto suites, not re-verified here beyond the inert-when-unset gate.
