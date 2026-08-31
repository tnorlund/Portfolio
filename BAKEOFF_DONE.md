# Round C+E1 self-report

Branch `bakeoff/C/grok`. Implementation commit `7b0af66f5`. This file is
the judge completion signal.

No vector indexes were created, altered, or deleted. No prod table writes.
Live backfill was not executed here (no AWS in this environment).

## 1. Embedding-item entities (SPEC §3.1)

**Addressed.** `ReceiptLineEmbedding` / `ReceiptWordEmbedding` in
`receipt_dynamo`:

- SK: `RECEIPT#{r:05d}#LINE#{l:05d}#EMBEDDING` and
  `…#WORD#{w:05d}#EMBEDDING`
- `TYPE` = `RECEIPT_LINE_EMBEDDING` / `RECEIPT_WORD_EMBEDDING`
- vector attrs `line_vector` / `word_vector` as DynamoDB `L` of `N`
- **no GSI1–4 keys**
- filter/projection attrs: `section_type` (lines), `label_status` (words),
  plus INCLUDE fields from the judge table
- accessors: get / list-from-receipt / idempotent batch-put

**Verify**

```
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e receipt_dynamo
pip install --no-deps -e receipt_embeddings
pip install numpy boto3 "openai>=2.8.1,<3.0.0" pytest pytest-mock \
  pytest-cov pytest-xdist pytest-timeout pytest-rerunfailures moto
cd receipt_dynamo
python -m pytest tests/unit/test_receipt_line_embedding.py \
  tests/unit/test_receipt_word_embedding.py -q
```

## 2. Embed-and-put writer

**Addressed.** `receipt_embeddings/writer.py`: OpenAI realtime via
`embed_texts_checked` (rejects non-1536 dim) then
`put_embedding_items`. Existing keys are skipped. A failed item is
recorded and the rest of the batch continues.

**Verify**

```
cd receipt_embeddings
python -m pytest tests/test_writer.py -q
```

## 3. Backfill script

**Addressed.** `scripts/backfill_embedding_items.py`

- golden receipts from `line_items_golden.json` + `--extra-receipts`
- `--limit` (default 2) and `--allow-under-floor`
- refuses prod (`ReceiptsTable-d7ff76a`) and any table other than
  `ReceiptsTable-dc5be22`
- `--dry-run` does not construct a Dynamo client
- live path: embed → idempotent put → bounded SearchVectors wait
- writes embedding items only (`#EMBEDDING` SKs)

**Verify**

```
python scripts/backfill_embedding_items.py --limit 1 --allow-under-floor --dry-run
DYNAMODB_TABLE_NAME=ReceiptsTable-d7ff76a python \
  scripts/backfill_embedding_items.py --limit 1 --allow-under-floor --dry-run
# expect: refusing to write embedding items to prod
```

Judge phase 2 (not run here):

```
python scripts/backfill_embedding_items.py --limit 2 --allow-under-floor
# re-run should report written=0 for the same receipts
```

## 4. DynamoVectorSearchClient / evaluate.py --backend dynamo

**Addressed.** `receipt_embeddings/dynamo_client.py` implements
`VectorSearchClient`:

- `SearchVector` = `[{"N": "..."}, …]` (not floats, not `L`-wrapped)
- `TopK`, `SearchConditionExpression` for equality filters
- results from `SearchResults[].Item` + `Score` (COSINE 0–2, lower closer)
- `last_request_units` = `ConsumedCapacity.VectorSearchRequestBytes`
- maps harness aliases `lines-vectors`/`words-vectors` →
  `line-embeddings`/`word-embeddings`
- never calls `update_table` / `create_table`
- throttle → empty neighbors (no crash)
- missing GetItem vector → `KeyError`
- FakeVectorIndex shares `validate_search_args` (`top_k` 1–100, flat
  equality filters)

`evaluate.py --backend dynamo` already loads
`receipt_embeddings.dynamo_client.create_client_from_env`.

**Verify**

```
cd receipt_embeddings
python -m pytest tests/test_dynamo_vector_client.py -q
python ../scripts/similarity_harness/evaluate.py --backend fake \
  --out /tmp/scorecard-fake.json
```

## 5. Merchant resolution VECTOR_BACKEND

**Addressed.** `VECTOR_BACKEND=dynamodb|dynamo|chroma` (default `chroma`).
Only `_retrieve_line_neighbors` changes. Thresholds
(`MIN_SIMILARITY_THRESHOLD` 0.70, `HIGH_CONFIDENCE_THRESHOLD` 0.85,
`1 - distance/2`) and corroboration stay in `_similarity_search_impl`.
SearchVectors failures return an empty `MerchantResult`.

**Verify**

```
cd receipt_embeddings
python -m pytest tests/test_vector_backend.py -q
```

(`receipt_upload/tests/test_vector_backend.py` is the same contract for
the upload matrix, which has the heavier import graph.)

## Offline gates (phase 1)

Against committed bootstrap `tests/fixtures/similarity/golden.json`:

```
python scripts/similarity_harness/evaluate.py --backend fake --out /tmp/scorecard-fake.json
```

Local result: **recall@10 = 1.0**, **merchant agreement = 100%** (ceilings
on the small bootstrap set; canonical S3 set's fake ceiling is ≈0.87).

Full embeddings suite: **70 passed**.

```
cd receipt_embeddings
python -m pytest tests -q --timeout=120 \
  -m "not end_to_end and not slow and not performance and not unused_in_production"
```

## Not verified locally

- Live `evaluate.py --backend dynamo` vs canonical S3 fixtures (no AWS
  credentials / no canonical `golden.json` downloaded).
- Live backfill onto `ReceiptsTable-dc5be22` (would require OpenAI or
  Chroma vectors; indexes left untouched).
- Idempotent second live backfill (`written=0`).
- Full `receipt_upload` pytest matrix (VECTOR_BACKEND unit tests covered
  via importlib in `receipt_embeddings`).
- Canonical fixture download
  (`tests/fixtures/similarity/CANONICAL_POINTER.md`).
