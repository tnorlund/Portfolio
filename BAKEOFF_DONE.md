# E3 self-report: QA search + MCP + similar_labeled_words

## 1. QA agent search port

- PASS: the three semantic line searches (`search_receipts` semantic,
  `semantic_search`, `search_product_lines` semantic) retrieve only
  through `VectorSearchClient.search` on `line-embeddings`, behind
  `VECTOR_BACKEND` (default `chroma`, lazy, injectable through
  `create_qa_tools(..., vector_client=)` and
  `create_qa_graph(..., vector_client=)`).
- PASS: depth is trimmed to the 100-result SearchVectors cap
  (documented at the module constant and each call site): `limit*2` /
  `limit*3` now clamp to `MAX_SEARCH_RESULTS` instead of the old Chroma
  300 ceiling. Default limits (20/100) sit at 40/100 requested results,
  so default-parameter behavior is depth-identical.
- PASS: the QA agent never hard-fails. An unbuildable backend and a
  throwing `search()` both degrade to empty results with a logged
  reason (previously a throwing Chroma call surfaced as
  `{"error": ...}`; degraded responses now come back as well-formed
  empty result sets with a `note`, which the agent can act on).
- PASS: `text`, `label`, and `label_lines` modes are untouched (their
  DynamoDB rewrite is X3 scope). `search_product_lines` text mode keeps
  its direct Chroma path; only the eager `get_collection("lines")` call
  moved into the text branch so semantic mode works without Chroma.
- DOCUMENTED DELTA: `search_product_lines` semantic replaced the
  `$nin` non-item-section pre-filter (inside the ANN query) with the
  same exclusion applied after retrieval — the seam takes equality
  filters only. Rows with no section label are still kept. Both
  backends' metadata spellings are handled (`section_label` /
  `section_type`).
- PASS: the selector is E2's, promoted to `receipt_embeddings.backend`
  (receipt_agent and the MCP servers cannot import receipt_upload);
  `receipt_upload.vector_search` re-exports it unchanged and every E2
  consumer/test is untouched and green.

## 2. MCP servers — both copies, equivalent edits, not unified

- PASS: `search_receipts` / `search_product_lines` SEMANTIC modes in
  BOTH `scripts/receipt_mcp_server.py` and
  `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`
  retrieve through the seam (`get_vector_search_client()`), trimmed to
  the 100-result cap, with the same post-retrieval non-item-section
  exclusion as the QA port. A cross-server test pins
  `inspect.getsource` equality for all six similarity functions, so
  the vendored fork cannot silently drift.
- PASS: with `VECTOR_BACKEND=dynamodb` and no Chroma credentials, the
  dispatch lets exactly the two semantic modes proceed (embed_fn from
  OpenAI); text modes and the default backend keep the structured
  `chroma_not_configured` error — the pre-existing lazy-Chroma test
  suite passes unmodified.
- PASS: `validate_word_similarity` is retired. Its dead `label_{X}`
  query paths (both polarities plus the suggested-labels loop) are
  deleted; the impl now returns a deprecation pointer naming
  `similar_labeled_words` instead of silently-empty results, and the
  tool description is marked `[DEPRECATED]`. It deliberately stays in
  `CHROMA_TOOLS`, so unconfigured-Chroma behavior (structured error)
  is byte-identical and the existing tests pass unmodified.
- PASS: NEW `similar_labeled_words` tool registered in both copies with
  identical schema/description: GetItem the stored word vector (no
  OpenAI call), `SearchVectors` `word-embeddings` with
  `label_status=validated`, similarity cut, BatchGetItem the neighbors'
  `ReceiptWordLabel` rows, and return evidence for/against with each
  neighbor's `reasoning` + provenance (`proposed_by`,
  `timestamp_added`, `validation_status`), alternative-label
  candidates, and the old weighted consensus. A word with no stored
  vector gets a graceful "no vector" answer.

## 3. similar_labeled_words engine (SPEC §3.7)

- PASS: implemented once in `receipt_embeddings.label_consensus`
  (library code both server copies call — the two server FILES remain
  separate and unmerged per the card). Thresholds are the old
  validator's: MIN_SIMILARITY 0.80 with the `1 - d/2` conversion,
  MIN_MATCHES 3, CONSENSUS_THRESHOLD 0.80, same-merchant boost 0.10
  (applied only when the target's merchant is known; surfaced as
  `same_merchant: true/false/null`).
- PASS: both polarities fall out of the join — a neighbor's VALID row
  for the candidate label is evidence for; an INVALID row is a
  counter-example with its reasoning. Neighbors validated as other
  labels aggregate into `alternative_labels`.
- PASS: fully graceful — missing vector, throttled search/get, and a
  failed label join each return structured PENDING answers with a
  reason; the function never raises.

## 4. Tests

- Seam selection: `receipt_embeddings/tests/test_backend.py` (default
  chroma, lazy dynamo, injection wins, protocol pass-through, unknown
  rejected); E2's `receipt_upload/tests/test_vector_search.py` passes
  unchanged against the re-export.
- Degradation: QA (`receipt_agent/tests/test_qa_search_vector_backend.py`)
  covers unbuildable backend + throwing search for all three semantic
  modes; engine tests cover missing vector / throttle / empty index /
  failed join; MCP tests cover the no-stored-vector answer and the
  chroma-unconfigured paths.
- similar_labeled_words join logic:
  `receipt_embeddings/tests/test_label_consensus.py` (11 tests) with
  `FakeVectorIndex` + stubbed label rows: polarity + reasoning
  surfaced, similarity cut, self-exclusion, alternatives, consensus
  thresholds, merchant boost, top-k clamp, validated filter.
- MCP registration sanity for both files:
  `tests/test_receipt_mcp_similarity_tools.py` (schema validity,
  cross-server schema/description equality, byte-equal plumbing,
  deprecation pointer, seam usage + cap, section post-filter,
  dynamodb-without-chroma dispatch, text-still-requires-chroma).
- Suites: receipt_embeddings 111 passed; receipt_agent 397 passed /
  22 skipped (387 pre-existing + 10 new; no pre-existing test was
  modified or removed); receipt_upload 947 passed / 13 skipped from
  the repo root; repository tests 831 passed / 1 skipped
  (includes all five cross-server MCP test files, 112 tests).
- Pre-existing quirk (not this card): running the receipt_upload suite
  with `cd receipt_upload` (as the CI matrix leg does) fails collection
  of `tests/test_line_item_worker_consistency.py` and
  `tests/test_section_assignment_evaluation.py` with
  `No module named 'infra'` / `'scripts...'` — they import repo-root
  packages and need the repo root on `sys.path` (E2 hit the same and
  ran with `PYTHONPATH=.` from the root, as below). Both files pass
  from the repo root; no unrelated code was touched.

## 5. Hard rules / lean scope

- No table writes, no index operations, no OpenAI call without a key
  (similar_labeled_words uses the stored vector; the
  dynamodb-without-chroma dispatch builds embed_fn only when invoked,
  and a missing key surfaces as a structured error).
- Chroma default byte-identical up to the two documented deltas the
  card itself mandates (100-result trim; equality-only filters forcing
  the post-retrieval section exclusion). All existing chroma-path
  tests pass unmodified — zero pre-existing test files were edited.
- Lean diff: no opportunistic refactors; the only prior-winner file
  touched is `receipt_upload/vector_search.py`, reduced to a
  re-export per the card's "reuse/extend rather than duplicate".

## Fresh-checkout verification

Run verbatim from the repository root:

```bash
.cursor/install.sh
source .venv/bin/activate
python -m pytest receipt_embeddings/tests -q
PYTHONPATH=. python -m pytest receipt_upload/tests \
  -m "not end_to_end and not slow and not performance and not unused_in_production"
(cd receipt_agent && python -m pytest tests -q \
  -m "not end_to_end and not slow and not performance and not unused_in_production")
python -m pytest tests -q
python -m py_compile scripts/receipt_mcp_server.py \
  infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py
black --check --line-length=79 \
  receipt_embeddings/receipt_embeddings/backend.py \
  receipt_embeddings/receipt_embeddings/label_consensus.py \
  receipt_embeddings/tests/test_backend.py \
  receipt_embeddings/tests/test_label_consensus.py \
  receipt_upload/receipt_upload/vector_search.py \
  receipt_agent/receipt_agent/agents/question_answering/tools/search.py \
  receipt_agent/receipt_agent/agents/question_answering/graph.py \
  receipt_agent/tests/test_qa_search_vector_backend.py
isort --check-only --profile=black --line-length=79 \
  receipt_embeddings/receipt_embeddings/backend.py \
  receipt_embeddings/receipt_embeddings/label_consensus.py \
  receipt_embeddings/tests/test_backend.py \
  receipt_embeddings/tests/test_label_consensus.py \
  receipt_upload/receipt_upload/vector_search.py \
  receipt_agent/receipt_agent/agents/question_answering/tools/search.py \
  receipt_agent/receipt_agent/agents/question_answering/graph.py \
  receipt_agent/tests/test_qa_search_vector_backend.py
```

Observed locally on Python 3.13.15 (venv built exactly like the CI
repository-tests leg):

- `receipt_embeddings`: 111 passed.
- `receipt_upload` (repo root, `PYTHONPATH=.`): 947 passed, 13 skipped
  (960 collected — identical to E2's baseline).
- `receipt_agent`: 397 passed, 22 skipped.
- repository tests (`tests/`): 831 passed, 1 skipped.
- Both MCP files compile; scoped Black and isort checks pass.

## Not verified locally

- No live dev-table `SearchVectors` or Chroma Cloud calls were made;
  E3 requires no table writes and this work needs none.
- The Lambda fork was validated by import, compile, cross-server
  source-equality, and impl tests — not by deploying the container.
- `VECTOR_BACKEND=dynamodb` end-to-end against the populated dev index
  (real embeddings + real label rows) is judge-run phase-2 territory;
  everything above it is covered by fakes/stubs pinned to the Round A/C
  contracts.
