# E2 self-report: distribution mode

## 1. Section verifier port

- PASS: `verify_receipt_sections` retrieves line neighbors only through
  `VectorSearchClient.search` and retrieves each selected training vector only
  through `VectorSearchClient.get_vector`.
- PASS: the cross-receipt exclusion, VALID-section lookup, row-line vote,
  `KNN_NEIGHBORS = 15`, `propagate_knn`, confidence gate, and non-overriding
  verification writes are unchanged.

## 2. Semantic PRODUCT_NAME proposer port

- PASS: the proposer retrieves validated word neighbors only through
  `VectorSearchClient.search` on `word-embeddings` with
  `label_status = validated`.
- PASS: `k + 8` retrieval depth, same-receipt exclusion, the existing
  `1 - distance / 2` similarity threshold, first-`k` vote cap, and
  PRODUCT_NAME plurality logic are unchanged.
- PASS: the Dynamo adapter fetch-joins the returned word identities to their
  core `ReceiptWordLabel` rows and exposes the Chroma-compatible
  `valid_labels_array` consumed by the unchanged vote.

## 3. Backend switch

- PASS: both consumers share one lazy backend selector behind
  `VECTOR_BACKEND=chroma|dynamodb`.
- PASS: `chroma` remains the default. The default path does not initialize an
  AWS client, and callers may inject an existing `VectorSearchClient` for
  isolated tests.

## 4. Graceful degradation

- PASS: backend construction errors, search throttles, missing neighbor
  vectors, malformed neighbor evidence, and incomplete Dynamo label joins all
  abstain. They do not discard the deterministic section assignment or crash
  PRODUCT_NAME proposal.
- PASS: an unprocessed or failed label join exposes no partial vote metadata.

## 5. Cross-backend contracts

- PASS: real `ChromaVectorSearchClient` and botocore-validated
  `DynamoVectorSearchClient` paths are tested for the section verifier's
  `image_id` / `receipt_id` / `row_line_ids` shape.
- PASS: both real adapters are tested for the semantic proposer's word
  identity, `label_status`, and `valid_labels_array` shape.
- PASS: Dynamo request-shape tests pin the exact strongly consistent
  BatchGetItem join, service batch limit, validated-filter gating, and
  fail-soft behavior.

## 6. Lean scope

- PASS: no dependencies, schemas, indexes, thresholds, or unrelated ingest
  stages changed. Production changes are limited to the two consumers, one
  shared selector, and the minimum Dynamo metadata normalization needed by the
  existing proposer vote.

## Fresh-checkout verification

Run verbatim from the repository root:

```bash
.cursor/install.sh
PYTHONPATH=. .venv/bin/pytest -q receipt_embeddings/tests
PYTHONPATH=. .venv/bin/pytest -q receipt_upload/tests
(
  cd receipt_embeddings
  ../.venv/bin/black --check receipt_embeddings/dynamo_client.py \
    tests/test_dynamo_client.py tests/test_metadata_contract.py
  ../.venv/bin/isort --check-only receipt_embeddings/dynamo_client.py \
    tests/test_dynamo_client.py tests/test_metadata_contract.py
)
(
  cd receipt_upload
  ../.venv/bin/black --check receipt_upload/vector_search.py \
    receipt_upload/section_verifier.py \
    receipt_upload/line_items/semantic_proposer.py \
    tests/test_section_pipeline_contract.py tests/test_section_verifier.py \
    tests/test_semantic_proposer.py tests/test_vector_search.py
  ../.venv/bin/isort --check-only receipt_upload/vector_search.py \
    receipt_upload/section_verifier.py \
    receipt_upload/line_items/semantic_proposer.py \
    tests/test_section_pipeline_contract.py tests/test_section_verifier.py \
    tests/test_semantic_proposer.py tests/test_vector_search.py
)
git diff --check
```

Observed locally on Python 3.13.15:

- `receipt_embeddings`: 95 passed.
- `receipt_upload`: 960 collected; all runnable tests passed, with 13 existing
  documented skips.
- Both scoped Black and isort checks passed.

## Not verified locally

- Live dev `SearchVectors` and Chroma Cloud calls were not made; this checkout
  has no AWS credentials and E2 requires no live writes.
- No deployment, push, or production change was performed.
