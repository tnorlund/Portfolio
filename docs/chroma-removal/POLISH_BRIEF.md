# Chroma-Removal Polish Brief (Cursor cloud-agent handoff)

Branch: `polish/chroma-removal-cleanup` (off `main` @ #1531).
Owner of record: this brief was written by the Claude session that built the
migration; a parallel session is still executing infra teardown. **Stay inside
the scope fence below or you will collide with live teardown work.**

## Mission

The DynamoDB-vector-search migration (PRs #1503–#1531) was built at sprint
pace with a review loop focused on correctness, not craft. Polish it:

1. **Typing** — eliminate `Any`-heavy signatures; introduce Protocols for the
   duck-typed seams; the packages below should pass `mypy --strict`-ish (match
   each package's existing mypy config; don't invent a new one).
2. **Exceptions** — replace *sloppy* broad catches with typed exceptions.
   ⚠️ Some broad catches are CONTRACTUAL (see Invariants) — annotate, don't
   remove.
3. **Pylint** — clear warnings without contorting the code; per-package
   configs are authoritative.
4. **Deduplication** — the concrete inventory below; each item should end as
   ONE canonical implementation with the copies importing it.
5. **Performance tests** — add a small benchmark suite (see §Perf).
6. **Test depth** — the new code's unit tests are contract-pins; add edge
   cases (pagination boundaries, UnprocessedItems paths, empty inputs).

## Scope fence (HARD)

**In scope** (the migration's new/heavily-modified surfaces):

- `receipt_embeddings/` (whole package: writer, dynamo_client, backend,
  label_consensus, quotas, service_limits, formatting/, testing)
- `receipt_dynamo/receipt_dynamo/entities/receipt_embedding.py`
- `receipt_dynamo_stream/receipt_dynamo_stream/{vector_freshening,sqs_publisher,message_builder}.py`
- `receipt_upload/receipt_upload/merchant_resolution/dynamo_embedding_write.py`
  (and its call sites' shared-helper extraction, see dedup #2/#3)
- `scripts/backfill_receipt_embeddings.py`, `scripts/similarity_harness/`
- `infra/routes/word_similarity_cache_generator/lambdas/index.py`
- `infra/routes/address_similarity_cache_generator/lambdas/index.py`
- `infra/merge_receipt_lambda/lambdas/merge_receipt.py`
- `infra/resegment_receipt_lambda/lambdas/resegment_receipt.py` (the
  `_dual_write_outputs_native_only` + embed sections only)
- `infra/upload_images/container_ocr/handler/ocr_processor.py` (the re-OCR
  native-refresh section only)
- `infra/fix_place_lambda/lambdas/fix_place.py` (tier-gate section only)
- Tests for all of the above.

**Out of scope — DO NOT TOUCH:**

- Any Pulumi program file: `infra/__main__.py`, `infra/receipt_update_queues/`,
  `infra/chromadb_shared_buckets.py`, `infra/chromadb_buckets.py`, any
  `infrastructure.py`/`infra.py`, `Pulumi.*.yaml`, `.github/workflows/`.
  Live teardown PRs are landing here concurrently.
- `receipt_chroma/` — it is the rollback path and is scheduled for deletion;
  polishing it is wasted churn. Do not refactor its public interfaces, do not
  delete Chroma code paths anywhere (the dual-run design keeps them on
  purpose until teardown finishes).
- No deploys, no `pulumi` commands, no AWS mutations. Tests must not hit real
  AWS (moto only, like the existing suites).
- Do not rename modules, config keys, env var spellings, queue/Lambda names,
  or anything in §Invariants.

## Deduplication inventory (concrete)

1. **Word label_status rule ×3** — "any terminal VALID/INVALID → validated,
   else PENDING → pending, else none" exists in:
   - `receipt_upload/.../dynamo_embedding_write.py::_word_label_statuses`
   - `scripts/backfill_receipt_embeddings.py::_label_statuses`
   - `receipt_dynamo_stream/.../vector_freshening.py` (label aggregation ~line 378)
   → One canonical function in `receipt_embeddings` (it is the corpus
   contract); all three import it. A drifted copy already caused a prod bug
   once (#1513 class).
2. **Native `#EMBEDDING` item sweeper ×2** —
   `merge_receipt.py::_delete_native_embedding_items` and the inline sweep in
   `ocr_processor.py`'s re-OCR refresh (query prefix + endswith filter +
   25-chunk BatchWrite + UnprocessedItems retry). → Shared helper in
   `receipt_embeddings` (note: the merge copy LACKS the UnprocessedItems
   retry the re-OCR copy has — unify on the retrying version).
3. **EmbeddingWriteRequest builders ×3** —
   `dynamo_embedding_write.py::build_ingest_embedding_requests`,
   `scripts/backfill_receipt_embeddings.py::build_requests`, and
   `resegment_receipt.py::_dual_write_outputs_native_only`. All construct
   line requests from `get_row_embedding_inputs` + anchors and word requests
   from label statuses. → One canonical builder (vectors optional).
4. **Canonical key strings** — `label_consensus.word_vector_key`, ad-hoc
   f-strings in cache generators/harnesses, `_canonical_key` in
   `dynamo_client.py`, SK regex parsers in several scripts. → One module of
   key builders/parsers in `receipt_embeddings`.
5. **`_incomplete(report)` checks** — duplicated in `ocr_processor.py` and
   as inline `error or failed` checks in merge/reseg. → method or helper on
   the write-report type.
6. **`_DynamoLinesClient`** (address cache generator) duplicates
   `DynamoVectorSearchClient` semantics with raw boto3. Keep it raw-boto3
   (image has no receipt_embeddings dependency by design) but shrink it and
   document WHY it exists; or add the package to that image and reuse the
   seam — your call, but changing the image's deps means Dockerfile +
   `source_paths` edits which are OUT of scope, so likely: document + tidy.

## Invariants — behavior that must NOT change

- **Similarity scale**: `similarity = 1 − cosine_distance`;
  `MIN_SIMILARITY = 0.60` (recalibrated from the retired validator's halved
  scale — see #1513). Don't "fix" either.
- **Address cache distances** are Chroma's historical squared-L2 scale:
  `2 × cosine_distance`. Frontend data continuity depends on it.
- **Word cache matching is case-INSENSITIVE** on purpose (fixed a latent
  Chroma `$contains` bug; see #1518/#1521).
- **Writer contract** (`receipt_embeddings/writer.py`): skip-existing via
  strongly-consistent BatchGet; embeds `embedding_input or text` realtime
  when `vector is None`; REFUSES empty text (correct — blank OCR rows);
  fail-closed report semantics.
- **Never-raise vs fail-closed is deliberate per call site**: ingest
  (`maybe_dual_write_embeddings` at process-OCR) never raises; merge aborts
  retryably BEFORE source deletion; reseg raises BEFORE commit; re-OCR
  retries then surfaces via routing-decision error. Keep the asymmetry; the
  docstrings explain each.
- **sqs_publisher contract** (pinned by tests): missing LINES/WORDS queue
  URLs are SKIPPED (retired legs); missing SUMMARY/LINE_ITEM URLs RAISE.
- **Env spellings**: `DYNAMODB_TABLE_NAME` and `DYNAMO_TABLE_NAME` both
  exist on process-OCR deliberately (different consumers). `VECTOR_BACKEND`
  values `chroma|dynamodb`, default chroma. Config keys:
  `portfolio:vector-backend`, `portfolio:enable-dual-write-embeddings`,
  `chromadb:enable-line-item-refine` — spellings frozen.
- **Deterministic cross-resource names** in code:
  `trigger-reocr-{stack}-trigger-reocr`, `upload-images-{stack}-ocr-queue` —
  frozen.
- The 51-per-stack "text must not be empty" embed failures on prod backfills
  are EXPECTED (blank OCR rows) — don't chase them.

## Performance tests to add (§Perf)

Create `receipt_embeddings/tests/perf/` (skipped by default, opt-in marker):

- Writer throughput: requests/sec vs batch size against moto; assert the
  25-item BatchGet/Write chunking stays.
- `DynamoVectorSearchClient.search` overhead: serialize/parse cost for a
  1536-dim query (`SearchVector` builds ~40KB — measure, document).
- Word-cache GSITYPE sweep: measure full-index paginated scan on a synthetic
  10k-item moto table; if a parallel-segments variant wins, propose it in a
  comment (no behavior change without a live A/B — the harnesses in
  `scripts/similarity_harness/` + the session scratchpad's
  `word_harness.py` pattern are how live A/Bs were done).
- The live-latency harness already exists:
  `scripts/similarity_harness/evaluate.py --backend dynamo
  --measure-wall-latency` — wire the perf docs to it rather than duplicating.

## Verification (run before every commit)

Per touched package (CI mirrors this; from the package dir):

    black --check --line-length=79 <pkg>/ tests/
    isort --check-only --profile=black --line-length=79 <pkg>/ tests/
    pytest tests -q

- `receipt_dynamo_stream` tests need `PYTHONPATH=../receipt_dynamo:../receipt_embeddings`.
- `receipt_upload` tests need `PYTHONPATH=../receipt_dynamo:../receipt_chroma:../receipt_embeddings`;
  several unrelated test modules fail collection locally with
  `No module named 'infra'` — pre-existing, ignore, CI handles it (use
  `--continue-on-collection-errors` or target files).
- Changed files are linted ONE BY ONE in CI (`black --check --line-length=79
  "$file"`) — a pre-existing nonconforming line in a file you touch becomes
  YOUR failure; format the whole file.
- Root `tests/` runs in CI (`python -m pytest tests`); keep it green.

## Process

- Work on THIS branch; open PR(s) against `main`. Small, reviewable PRs
  beat one megadiff. Reference this brief in each PR body.
- The repo's other automation (codex review loop, teardown PRs) is running
  concurrently on `main` — rebase early, rebase often.
- History/context: `docs/chroma-removal/SPEC.md` is the design of record;
  PRs #1503–#1531 carry the review-loop rationale for every invariant above.
