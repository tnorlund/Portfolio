# Round C+E1 rubric self-report

Implementation commit:

- `779c4ecfc` — embedding entities, embed-and-put writer, this-run-keyed
  backfill, `DynamoVectorSearchClient`, and `VECTOR_BACKEND` merchant
  retrieval

The judge's completion signal is this file. Nothing here was committed
before the engine landed.

## 1. Embedding-item entities (SPEC §3.1)

`RECEIPT_LINE_EMBEDDING` / `RECEIPT_WORD_EMBEDDING` live in
`receipt_dynamo` with house-style accessors.

- SK under the `RECEIPT#` prefix:
  `RECEIPT#{r:05d}#LINE#{l:05d}#EMBEDDING` and
  `RECEIPT#{r:05d}#LINE#{l:05d}#WORD#{w:05d}#EMBEDDING`
- `TYPE` set; vector attrs named exactly `line_vector` / `word_vector`
  (L-of-N, 1536-dim)
- **No GSI1–4 keys** (asserted in `to_item`)
- Filter/projection attrs: `section_type` on lines; `label_status` on
  words (`validated` / `pending` / `none`); plus text, merchant_name,
  place_id, image_id, receipt_id, line_id, row_line_ids / word_id
- `put_embedding_items_idempotent` BatchGets **only the keys this call
  attempted** (never a table scan) and skips existing same-SK items —
  prior run or another entrant on the shared table

## 2. Embed-and-put writer

`receipt_embeddings/writer.py`: optional OpenAI realtime for missing
texts, then `put_embedding_items_idempotent`. Re-run writes nothing for
keys that already exist. Per-item failures skip-and-report; the rest of
the batch continues.

## 3. Backfill script

`scripts/backfill_embedding_items.py` — golden receipts plus Round A
`--extra-receipts` / `--limit` / `--allow-under-floor`. Dev table only
(`ReceiptsTable-dc5be22`). Prefers Chroma `receipt_dev` reuse, then
`--fixture` corpus, then `--embed-missing` OpenAI. Ends with a
written/skipped report **scoped to this run's keys**
(`scope: this_run_keys_only`) and a searchability wait that polls
SearchVectors for the exact sampled key this run wrote. Foreign
neighbors on the shared table are counted and ignored. Timeout is
reported, not a crash. Graded runs are judge-sequential on a wiped
table.

## 4. `DynamoVectorSearchClient` + `evaluate.py --backend dynamo`

`receipt_embeddings/dynamo_client.py` implements the Round A protocol.

Wire format (judge-verified 2026-08-31):

- `SearchVector` is a list of AttributeValue dicts (`[{"N": "0.01"}, …]`)
- Results under `SearchResults`, not `Items`
- `ReturnConsumedCapacity` reports `VectorSearchRequestBytes`

Protocol indexes `lines-vectors` / `words-vectors` map onto the
provisioned `line-embeddings` / `word-embeddings`. Result keys are
harness keys derived from PK+SK (fallback: projected ids). Service
quotas (`MAX_SEARCH_RESULTS=100`, 1536-dim, equality-only filters) are
constants; the fake is pinned to the same top-k cap. Prod table names
are refused.

`create_client_from_env()` is what `evaluate.py --backend dynamo` loads.

## 5. Merchant resolution behind `VECTOR_BACKEND`

`VECTOR_BACKEND=dynamodb|dynamo|chroma` (default `chroma`). Retrieval
swaps via `VectorSearchClient` only; thresholds, phone/address boosts,
tier logic, and corroboration gating in `resolver.py` are otherwise
unchanged.

## Gates — phase 1 (offline)

### Fresh-checkout verify commands

From a fresh checkout at this commit, run setup once. It installs
Python 3.13, the local editable stack, pinned `boto3>=1.43.64,<1.44.0`,
and test extras. No extra `PYTHONPATH` is required.

```bash
./.cursor/install.sh
```

Then run the package gates verbatim from the repository root:

```bash
.venv/bin/python -m pytest receipt_embeddings/tests \
  receipt_dynamo/tests/unit/test_receipt_line_embedding.py \
  receipt_dynamo/tests/unit/test_receipt_word_embedding.py \
  receipt_upload/tests/test_vector_backend.py \
  receipt_upload/tests/test_merchant_resolver.py \
  -m "not end_to_end and not slow" \
  -q --tb=short

.venv/bin/python scripts/similarity_harness/evaluate.py \
  --backend fake \
  --out fake-scorecard.json
```

Local result of those pytest paths: 80 embedding/entity tests passed;
53 merchant-resolution / `VECTOR_BACKEND` tests passed.

Fake-backend evaluate against the **committed bootstrap** fixture
(`canonical: false`, 81 receipts, 243 queries):

- neighbor recall@10 overall: **1.0**
- merchant agreement: **100%**
- gates: `merchant_agreement_at_least_98_percent=true`

### Graceful degradation

`receipt_embeddings/tests/test_graceful_degradation.py` covers missing
vector (skip-and-report), SearchVectors throttle (retry then
`VectorSearchThrottled`), and absent GetItem (`KeyError`). Backfill
skips missing receipts without aborting.

## Not verified locally

- Live SearchVectors against `ReceiptsTable-dc5be22` (no AWS credentials
  in this environment)
- `scripts/backfill_embedding_items.py --limit 5 --allow-under-floor`
  (dev-table writes; judge-run)
- `evaluate.py --backend dynamo` vs the **canonical** S3 fixtures
  (`tests/fixtures/similarity/CANONICAL_POINTER.md`, sha256
  `199d7f4fc16858e1bf6aaea0a748edb6822145a4b2af6fa9078c6f5fd7420144`).
  The card's recall@10 ≥ 0.85 / merchant ≥ 98% gate is against that
  set; this checkout only has the small bootstrap golden.json
- Second backfill idempotency on a wiped table (judge-sequential)
- OpenAI-free Chroma reuse (`CHROMA_CLOUD_*` + `receipt_dev`)
- Cold-call SearchVectors latency / `VectorSearchRequestBytes` ~40KB
  (judge-verified; client sends `ReturnConsumedCapacity=TOTAL`)

Live behavior is the heavy grade. The backfill reports skip/fail per
this-run key and does not crash on a searchability timeout.
