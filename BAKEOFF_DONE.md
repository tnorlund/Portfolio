# Round C+E1 completion — Codex

All five card deliverables are implemented on `bakeoff/C/codex`. No branch was
pushed and no pull request was opened.

## Delivered

1. `ReceiptLineEmbedding` and `ReceiptWordEmbedding` use deterministic
   receipt-prefixed `#EMBEDDING` sort keys, the required TYPE and vector
   attributes, projected/filter metadata, and no GSI1-GSI4 keys. House-style
   Dynamo accessors are mixed into `DynamoClient`.
2. `EmbeddingWriter` checks exact keys with consistent `BatchGetItem`, embeds
   only missing items, writes batches of at most 25, retries unprocessed items,
   and isolates validation, read, embedding, and write failures per item.
3. `scripts/backfill_receipt_embeddings.py` selects golden plus optional extra
   receipts, supports bounded `--limit` runs, reuses exact 1536-dimensional
   fixture vectors, and generates only uncovered vectors through OpenAI
   realtime. Dry run is the default. `--apply` requires an explicit limit and
   the script refuses every table except `ReceiptsTable-dc5be22`.
4. `DynamoVectorSearchClient` implements `VectorSearchClient` with the live
   `SearchVectors` contract: bare AttributeValue vector list, `SearchResults`,
   equality-only inline filters, top-k at most 100, and
   `VectorSearchRequestBytes` cost telemetry. The real botocore service model
   validates the request and response in tests.
5. Merchant retrieval is selected by `VECTOR_BACKEND=chroma|dynamodb`, default
   `chroma`, and uses `VectorSearchClient` in both modes. Similarity thresholds,
   tier logic, and corroboration gates are unchanged.

Backfill verification samples only canonical keys reported as written by that
invocation. It never uses a table-wide count, so deterministic items left by a
different entrant are ignored. There are no vector-index lifecycle calls.

## Fresh-checkout verification commands

Run from the repository root exactly as follows:

```bash
.cursor/install.sh
source .venv/bin/activate

pytest -q receipt_dynamo/tests/unit
pytest -q receipt_embeddings/tests
PYTHONPATH=. pytest -q receipt_chroma/tests/unit
PYTHONPATH=. pytest -q receipt_upload/tests -m 'not integration and not end_to_end'

aws s3 cp s3://raw-image-bucket-c779c32/similarity-fixtures/canonical-2026-08-31/golden.json.gz /tmp/codex-canonical-golden.json.gz --no-progress
gunzip -kf /tmp/codex-canonical-golden.json.gz
shasum -a 256 /tmp/codex-canonical-golden.json
python scripts/similarity_harness/evaluate.py --backend fake --fixture /tmp/codex-canonical-golden.json --out /tmp/codex-round-c-canonical-scorecard.json

DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 python scripts/backfill_receipt_embeddings.py --fixture /tmp/codex-canonical-golden.json --limit 1
```

The canonical artifact must hash to:

```text
199d7f4fc16858e1bf6aaea0a748edb6822145a4b2af6fa9078c6f5fd7420144
```

Judge-only bounded apply, exact-key idempotency rerun, and post-index score:

```bash
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 python scripts/backfill_receipt_embeddings.py --fixture /tmp/codex-canonical-golden.json --limit 1 --apply
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 python scripts/backfill_receipt_embeddings.py --fixture /tmp/codex-canonical-golden.json --limit 1 --apply
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 AWS_REGION=us-east-1 python scripts/similarity_harness/evaluate.py --backend dynamo --fixture /tmp/codex-canonical-golden.json --out /tmp/codex-round-c-live-scorecard.json
```

## Observed results

- Dynamo unit suite: 2,455 passed.
- Embedding suite: 67 passed.
- Chroma unit suite: 442 passed, 5 existing skips.
- Upload suite: passed with its existing skips after setting `PYTHONPATH=.`.
- Canonical fake replay: recall@10 `0.86627907`, merchant identity agreement
  `100%`, tier decision agreement `100%`, and all offline gates true. Exact
  branch/place-ID agreement is separately reported as `91.860465%` rather than
  being hidden inside the merchant identity metric.
- One-receipt dev dry run: 164 planned embedding keys, 11 fixture-vector
  reuses, 153 realtime embeddings, zero receipt skips, and zero writes.
- Read-only live dev search across 258 canonical queries exercised the real
  wire format without errors: mean `40,458` request bytes, estimated
  `$0.000000080916` per query, p50 `241.065687 ms`, p95 `261.319798 ms`.
  The currently wiped/sparse corpus returned no neighbors, so this is wire and
  degradation evidence, not a passing post-backfill score.

No applied backfill was run locally because neither OpenAI nor Chroma
credentials were present. No DynamoDB item was written, updated, or deleted by
the verification run, and nothing touched production.
