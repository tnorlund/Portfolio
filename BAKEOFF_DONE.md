# E3 self-report: QA search + MCP + similar_labeled_words (round 3)

Round 3 of this report: the three round-2 re-review findings
(P1-A/P1-B/P2-C) are fixed on top of the round-1 fixes and the original
five E3 commits. Newest findings first, then round 1, then the original
per-item report (still accurate unless amended).

## Round-2 re-review findings

- **P1-A (ndarray truthiness broke the default backend) FIXED**:
  `ChromaVectorSearchClient.get_vector` boolean-tested chromadb's numpy
  responses (`or []` / `if not`), raising ambiguous-truth ValueError
  that the engine's broad degrade converted into
  PENDING/vector_search_failed on EVERY default-backend consensus call.
  `get_vector` AND `search` now use explicit None/length checks (the
  same hazard existed on `ids`/`distances`). Regression tests: an
  ndarray-returning chroma stub yields real evidence end-to-end
  through `similar_labeled_words`, and an empty ndarray produces the
  graceful no-vector answer.
- **P1-B (pre-rule backfills never reclassified) FIXED**:
  `scripts/backfill_receipt_embeddings.py --repair-label-status` is a
  bounded metadata-repair mode: for the script's normal receipt scope
  (golden fixture + `--extra-receipts`, `--limit`, dev-table refusal,
  dry-run unless `--apply`, `--apply` requires `--limit`), it
  re-aggregates each word's current `ReceiptWordLabel` rows with the
  writer's own `_label_statuses` rule and UpdateItems ONLY the
  `label_status` attribute where it differs. Idempotent (a second run
  plans zero updates), no vector writes, `attribute_exists` so items
  are never created, per-receipt skip-and-report, fail-closed exit
  when every planned update errors. Moto test covers dry-run/apply,
  vector byte-identity, exclusion of line embeddings and
  already-correct words, and the idempotent rerun.
- **P2-C (similarity scale) FIXED**: `label_consensus` now computes
  similarity as `1 - distance` — `ScoredItem.distance` is cosine
  distance per the VectorSearchClient contract on every backend
  (Chroma collections are cosine; the Round A fixture parity is the
  evidence, so no adapter normalization is needed — if a raw-L2
  chroma configuration ever appears, the adapter is where it gets
  normalized). The old `1 - d/2` halving inflated a 0.60-sim neighbor
  to 0.80. Affected tests were updated to hand-computed cosine values
  (boost case: `[0.8, 0.6]` against `[1, 0]` → sim 0.8, boosted 0.9;
  far neighbor at distance 1.0 → sim 0.0), and a new regression proves
  a 0.60-sim neighbor (distance 0.40) is excluded at
  `min_similarity=0.8`.

## Round-1 review findings (all still in place)

- **P1-1**: `dynamodb:SearchVectors` on both scoped Lambda-role
  policies (MCP + QA), table + `index/*` resources.
- **P1-2**: both `label_status` writers treat terminal INVALID like
  VALID (validated/pending/none rule); regression tests in the moto
  stream suite, the backfill suite, and the engine suite
  (invalid-only neighbor → `evidence_against`, both join paths).
- **P1-3**: `backend.py` accepts the caller's `table_name` (+ optional
  boto3 client); QA tools and both MCP copies thread their configured
  `DynamoClient` through; `from_env` is a logged last resort naming
  the chosen table.
- **P2-4**: single label-join — the Dynamo adapter's hydration
  projects reasoning/provenance and attaches `label_rows`;
  `similar_labeled_words` reuses them, loader only for un-hydrated
  neighbors and non-core candidate labels.
- **P2-5**: `has_price_label` is tri-state — `"unknown"` under the
  Dynamo backend, Chroma values byte-identical — in the QA tool and
  both MCP copies in lockstep.

## 1. QA agent search port

- PASS: the three semantic line searches (`search_receipts` semantic,
  `semantic_search`, `search_product_lines` semantic) retrieve only
  through `VectorSearchClient.search` on `line-embeddings`, behind
  `VECTOR_BACKEND` (default `chroma`, lazy, injectable through
  `create_qa_tools(..., vector_client=)` and
  `create_qa_graph(..., vector_client=)`).
- PASS: depth is trimmed to the 100-result SearchVectors cap;
  default limits (20/100) sit at 40/100 requested results, so
  default-parameter behavior is depth-identical.
- PASS: the QA agent never hard-fails — unbuildable backend and
  throwing searches degrade to empty results with a logged reason.
- PASS: `text`, `label`, and `label_lines` modes untouched (X3 scope).
- DOCUMENTED DELTA: the `$nin` non-item-section pre-filter became the
  same exclusion applied after retrieval (the seam takes equality
  filters only); rows with no section label are still kept.
- PASS: the selector is E2's, promoted to `receipt_embeddings.backend`;
  `receipt_upload.vector_search` re-exports it unchanged and every E2
  consumer/test is untouched and green.

## 2. MCP servers — both copies, equivalent edits, not unified

- PASS: `search_receipts` / `search_product_lines` SEMANTIC modes in
  BOTH copies retrieve through the seam (`get_vector_search_client()`,
  threading the session table), trimmed to the 100-result cap, with
  the same post-retrieval non-item-section exclusion. A cross-server
  test pins `inspect.getsource` equality for all six similarity
  functions.
- PASS: with `VECTOR_BACKEND=dynamodb` and no Chroma credentials, only
  the two semantic modes proceed; text modes and the default backend
  keep the structured `chroma_not_configured` error — the pre-existing
  lazy-Chroma test suite passes unmodified.
- PASS: `validate_word_similarity` retired to a deprecation pointer
  naming `similar_labeled_words`; kept in `CHROMA_TOOLS` so
  unconfigured behavior is byte-identical.
- PASS: NEW `similar_labeled_words` tool in both copies with identical
  schema/description: stored-vector GetItem (no OpenAI call),
  validated-neighbor SearchVectors, similarity cut on the cosine
  contract, single label-row join, evidence for/against with each
  neighbor's reasoning + provenance, alternative labels, weighted
  consensus, graceful no-vector answer.

## 3. similar_labeled_words engine (SPEC §3.7)

- PASS: one implementation in `receipt_embeddings.label_consensus`
  called by both server copies (files remain separate). Thresholds:
  MIN_SIMILARITY 0.80 (on `1 - distance` cosine similarity),
  MIN_MATCHES 3, CONSENSUS_THRESHOLD 0.80, same-merchant boost 0.10
  when the target's merchant is known.
- PASS: both polarities from the join — VALID rows for the candidate
  are evidence for, INVALID rows evidence against (and those words are
  retrievable per P1-2); other validated labels aggregate into
  `alternative_labels`.
- PASS: fully graceful — missing vector, throttled search/get, and a
  failed label join return structured PENDING answers; never raises.

## 4. Tests

- receipt_embeddings suite (now 120): seam selection + threading +
  fallback warning; engine join/degradation/polarity/threshold/ndarray
  (17 tests in test_label_consensus.py); adapter contract incl.
  `label_rows` hydration and join-failure omission; backfill rules +
  the moto repair test; writer/quotas/harness/fake unchanged.
- receipt_dynamo_stream suite (175): the terminal-verdict rule and its
  moto regression, everything else unchanged.
- QA suite additions (13 tests): seam usage, depth trim, degradation,
  threading, tri-state.
- Cross-server MCP suite: registration parity, byte-equal plumbing,
  deprecation pointer, seam + cap, section post-filter,
  dynamodb-without-chroma dispatch, table threading, tri-state.
- Modified pre-existing test files, all forced by judge-accepted
  fixes: `test_dynamo_client.py` (P2-4 projection/`label_rows` pins)
  and `test_label_consensus.py`'s own earlier expectations (P2-C
  corrected to hand-computed cosine values — not re-pinned outputs).
- Suites (post-round-2 run): receipt_embeddings 120 passed;
  receipt_dynamo_stream 175 passed; receipt_agent 400 passed /
  22 skipped; repository tests 837 passed / 1 skipped; receipt_upload
  E2 contract files 35 passed this round and the full suite 947
  passed / 13 skipped in the round-1 run (nothing in round 2 touches
  receipt_upload).
- Pre-existing quirks (documented, untouched): the two receipt_upload
  repo-root-import test files (run from the repo root with
  `PYTHONPATH=.`); `test_message_builder.py` black drift on main;
  package `tests/` dirs collide if multiple package suites are mixed
  into ONE pytest invocation (CI never does).

## 5. Hard rules / lean scope

- No table writes from any test or verification run (the repair mode
  writes only when explicitly `--apply`'d against the dev table, which
  no run here did); no index operations; no OpenAI call without a key.
- Chroma default byte-identical outside the fixed adapter bug and the
  card-mandated deltas (100-result trim; post-retrieval section
  exclusion). The P2-C scale change affects only the NEW
  similar_labeled_words evidence quality — no pre-existing consumer
  used the engine.
- Both MCP copies changed in lockstep everywhere (getsource-parity
  test green); lean diffs confined to review-named surfaces + tests.

## Fresh-checkout verification

Run verbatim from the repository root:

```bash
.cursor/install.sh
source .venv/bin/activate
python -m pytest receipt_embeddings/tests -q
python -m pytest receipt_dynamo_stream/tests -q
PYTHONPATH=. python -m pytest receipt_upload/tests \
  -m "not end_to_end and not slow and not performance and not unused_in_production"
(cd receipt_agent && python -m pytest tests -q \
  -m "not end_to_end and not slow and not performance and not unused_in_production")
python -m pytest tests -q
python -m py_compile scripts/receipt_mcp_server.py \
  infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py \
  scripts/backfill_receipt_embeddings.py \
  infra/mcp_server_lambda/infrastructure.py \
  infra/qa_agent_step_functions/infrastructure.py
python scripts/backfill_receipt_embeddings.py --help | grep repair-label-status
black --check --line-length=79 \
  receipt_embeddings/receipt_embeddings \
  receipt_embeddings/tests \
  scripts/backfill_receipt_embeddings.py \
  receipt_dynamo_stream/receipt_dynamo_stream \
  receipt_dynamo_stream/tests/integration/test_vector_freshening_with_moto.py \
  receipt_upload/receipt_upload/vector_search.py \
  receipt_agent/receipt_agent/agents/question_answering/tools/search.py \
  receipt_agent/receipt_agent/agents/question_answering/graph.py \
  receipt_agent/tests/test_qa_search_vector_backend.py \
  tests/test_receipt_mcp_similarity_tools.py
isort --check-only --profile=black --line-length=79 \
  receipt_embeddings/receipt_embeddings/backend.py \
  receipt_embeddings/receipt_embeddings/chroma_client.py \
  receipt_embeddings/receipt_embeddings/label_consensus.py \
  receipt_embeddings/receipt_embeddings/dynamo_client.py \
  receipt_dynamo_stream/receipt_dynamo_stream/vector_freshening.py \
  receipt_upload/receipt_upload/vector_search.py \
  receipt_agent/receipt_agent/agents/question_answering/tools/search.py \
  receipt_agent/receipt_agent/agents/question_answering/graph.py \
  receipt_agent/tests/test_qa_search_vector_backend.py \
  tests/test_receipt_mcp_similarity_tools.py
```

Observed locally on Python 3.13.15 (venv built exactly like the CI
repository-tests leg):

- `receipt_embeddings`: 120 passed.
- `receipt_dynamo_stream`: 175 passed.
- `receipt_upload` (repo root, `PYTHONPATH=.`): 947 passed, 13 skipped
  in the round-1 run; the E2 contract files re-ran green (35) in
  round 2, which touches nothing in receipt_upload.
- `receipt_agent`: 400 passed, 22 skipped.
- repository tests (`tests/`): 837 passed, 1 skipped.
- All five files compile; the new flag registers; scoped Black and
  isort checks pass (modulo the pre-existing `test_message_builder.py`
  drift on main).

## Not verified locally

- No live dev-table `SearchVectors`, Chroma Cloud, or repair `--apply`
  runs were made; the moto suite stands in for the repair's DynamoDB
  behavior. Running `--repair-label-status --apply --limit N` on dev
  after merge is the judge-side step that actually reclassifies
  pre-rule rows.
- IAM policy additions validated by py_compile/shape review, not
  `pulumi preview` (no Pulumi credentials in this checkout).
- The Lambda fork was validated by import, compile, cross-server
  source-equality, and impl tests — not by deploying the container.
- Whether any live Chroma collection reports raw L2 distances was not
  probed; Round A fixture parity says cosine, and the adapter is the
  designated place to normalize if that ever proves wrong.
