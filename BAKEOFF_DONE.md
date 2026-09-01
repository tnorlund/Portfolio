# E3 self-report: QA search + MCP + similar_labeled_words (post-review)

Round 2 of this report: the five codex-review findings (P1-1..P2-5) are
fixed in commits 17635c1ca, 2266746d8, 4b8a0bfad, 702b9e280 on top of
the original five E3 commits. Per-finding status first, then the
original per-item report (still accurate unless amended here).

## Review findings

- **P1-1 (IAM) FIXED**: `dynamodb:SearchVectors` added to the scoped
  DynamoDB policies of the MCP Lambda role
  (`infra/mcp_server_lambda/infrastructure.py`) and QA Lambda role
  (`infra/qa_agent_step_functions/infrastructure.py`) — same table +
  `index/*` resources as the existing actions.
- **P1-2 (INVALID-only words unretrievable) FIXED**: both
  `label_status` writers — the stream freshener's
  `_compute_word_label_status` and the backfill's `_label_statuses` —
  now treat a terminal INVALID verdict like VALID: any VALID/INVALID →
  `validated`, else PENDING → `pending`, else `none`. Invalid-only
  words therefore stay inside the `label_status = validated` search
  population, and their INVALID `ReceiptWordLabel` rows surface as
  `evidence_against`. Regression tests: moto stream test
  (`test_invalid_only_word_stays_in_validated_population`), backfill
  test (`test_build_requests_marks_invalid_only_words_validated`), and
  engine test (`test_invalid_only_neighbor_surfaces_as_evidence_against`,
  proving evidence_against via hydrated rows AND the loader path).
  Side effect (accepted): invalid-only words enter the semantic
  proposer's candidate pool with an empty `valid_labels_array`, where
  its existing no-valid-labels guard skips them.
- **P1-3 (dev-table fallback) FIXED**: `backend.py`'s dynamodb branch
  accepts `table_name` (and optionally a low-level `dynamodb_client`);
  the QA tools thread their `DynamoClient`'s table/client through, and
  both MCP copies' `get_vector_search_client` does the same from
  `get_dynamo_client()`. `from_env` remains a documented last resort
  that logs a warning naming the table it chose. Tests pin the
  threading in all three consumers and the fallback warning.
- **P2-4 (double label-join) FIXED**: the Dynamo adapter's
  validated-filter hydration now also projects
  `reasoning`/`label_proposed_by`/`timestamp_added` (same BatchGetItem
  count) and attaches the rows as `label_rows` neighbor metadata;
  `similar_labeled_words` reuses them and calls the loader only for
  un-hydrated neighbors (Chroma backend or a degraded join) and
  non-core candidate labels. Test proves hydrated neighbors never
  touch the loader; the adapter contract test pins the `label_rows`
  shape and that a failed join omits it.
- **P2-5 (has_price_label false under Dynamo) FIXED**: the
  `search_product_lines` semantic mode (QA tool and both MCP copies,
  in lockstep — the getsource-parity test still passes) reports
  `has_price_label: "unknown"` when the resolved backend is the Dynamo
  adapter, since Dynamo line metadata never carries Chroma's
  `label_LINE_TOTAL` flag; Chroma-path values are byte-identical.
  Tests pin "unknown" under a Dynamo-typed backend and the unchanged
  False off it, in both the QA suite and the cross-server suite.

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
  reason.
- PASS: `text`, `label`, and `label_lines` modes are untouched (their
  DynamoDB rewrite is X3 scope).
- DOCUMENTED DELTA: `search_product_lines` semantic replaced the
  `$nin` non-item-section pre-filter (inside the ANN query) with the
  same exclusion applied after retrieval — the seam takes equality
  filters only. Rows with no section label are still kept.
- PASS: the selector is E2's, promoted to `receipt_embeddings.backend`
  (receipt_agent and the MCP servers cannot import receipt_upload);
  `receipt_upload.vector_search` re-exports it unchanged and every E2
  consumer/test is untouched and green.

## 2. MCP servers — both copies, equivalent edits, not unified

- PASS: `search_receipts` / `search_product_lines` SEMANTIC modes in
  BOTH `scripts/receipt_mcp_server.py` and
  `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`
  retrieve through the seam (`get_vector_search_client()`, threading
  the session table per P1-3), trimmed to the 100-result cap, with the
  same post-retrieval non-item-section exclusion as the QA port. A
  cross-server test pins `inspect.getsource` equality for all six
  similarity functions, so the vendored fork cannot silently drift.
- PASS: with `VECTOR_BACKEND=dynamodb` and no Chroma credentials, the
  dispatch lets exactly the two semantic modes proceed (embed_fn from
  OpenAI); text modes and the default backend keep the structured
  `chroma_not_configured` error — the pre-existing lazy-Chroma test
  suite passes unmodified.
- PASS: `validate_word_similarity` is retired. Its dead `label_{X}`
  query paths are deleted; the impl returns a deprecation pointer
  naming `similar_labeled_words`, and the tool description is marked
  `[DEPRECATED]`. It deliberately stays in `CHROMA_TOOLS`, so
  unconfigured-Chroma behavior (structured error) is byte-identical.
- PASS: NEW `similar_labeled_words` tool registered in both copies with
  identical schema/description: GetItem the stored word vector (no
  OpenAI call), `SearchVectors` `word-embeddings` with
  `label_status=validated`, similarity cut, single label-row join
  (adapter hydration reused per P2-4), and evidence for/against with
  each neighbor's `reasoning` + provenance, alternative-label
  candidates, and the old weighted consensus. A word with no stored
  vector gets a graceful "no vector" answer.

## 3. similar_labeled_words engine (SPEC §3.7)

- PASS: implemented once in `receipt_embeddings.label_consensus`
  (library code both server copies call — the two server FILES remain
  separate and unmerged per the card). Thresholds are the old
  validator's: MIN_SIMILARITY 0.80 with the `1 - d/2` conversion,
  MIN_MATCHES 3, CONSENSUS_THRESHOLD 0.80, same-merchant boost 0.10
  (applied only when the target's merchant is known).
- PASS: both polarities fall out of the join — a neighbor's VALID row
  for the candidate label is evidence for; an INVALID row is a
  counter-example with its reasoning (and per P1-2 those words are now
  actually retrievable). Neighbors validated as other labels aggregate
  into `alternative_labels`.
- PASS: fully graceful — missing vector, throttled search/get, and a
  failed label join each return structured PENDING answers with a
  reason; the function never raises.

## 4. Tests

- Seam selection + threading: `receipt_embeddings/tests/test_backend.py`
  (default chroma, lazy dynamo, injection wins, protocol pass-through,
  unknown rejected, threaded client/table wins, fallback warns naming
  the table); E2's `receipt_upload/tests/test_vector_search.py` passes
  unchanged against the re-export.
- Degradation: QA
  (`receipt_agent/tests/test_qa_search_vector_backend.py`, 13 tests)
  covers unbuildable backend + throwing search for all three semantic
  modes, plus threading and the P2-5 tri-state; engine tests cover
  missing vector / throttle / empty index / failed join; MCP tests
  cover the no-stored-vector answer and the chroma-unconfigured paths.
- similar_labeled_words join logic:
  `receipt_embeddings/tests/test_label_consensus.py` (13 tests) with
  `FakeVectorIndex` + stubbed label rows, including single-join reuse
  and invalid-only evidence_against.
- MCP registration sanity for both files:
  `tests/test_receipt_mcp_similarity_tools.py` (schema validity,
  cross-server schema/description equality, byte-equal plumbing,
  deprecation pointer, seam usage + cap, section post-filter,
  dynamodb-without-chroma dispatch, table threading, P2-5 tri-state).
- Status-rule regressions: moto stream test + backfill test pin the
  P1-2 writer rule in both writers.
- Suites (post-review-fix run): receipt_embeddings +
  receipt_dynamo_stream 291 passed; receipt_agent 400 passed /
  22 skipped; receipt_upload 947 passed / 13 skipped from the repo
  root (960 collected — E2's baseline); repository tests 837 passed /
  1 skipped. Zero pre-existing test files modified except the two
  adapter-contract tests whose pinned request/metadata shapes the
  judge-accepted P2-4 fix changes
  (`test_dynamo_client.py`: extended projection + `label_rows`
  assertions).
- Pre-existing quirks (not this card, documented): (a) running the
  receipt_upload suite with `cd receipt_upload` fails collection of two
  files that import repo-root packages — run from the repo root with
  `PYTHONPATH=.` as below; (b)
  `receipt_dynamo_stream/tests/unit/test_message_builder.py` is
  black-unclean on main with black 26.5.1 — untouched here.

## 5. Hard rules / lean scope

- No table writes, no index operations, no OpenAI call without a key.
- Chroma default byte-identical up to the card-mandated deltas
  (100-result trim; equality-only filters forcing the post-retrieval
  section exclusion). The P2-5 tri-state changes only the
  Dynamo-backend value; Chroma-path values are unchanged.
- Lean diff: fixes touch exactly the surfaces the review named, plus
  their tests; prior-winner files touched only where the accepted fix
  direction requires (E2 selector re-export; card C/D `label_status`
  writers per P1-2; Round C adapter per P2-4).

## Fresh-checkout verification

Run verbatim from the repository root:

```bash
.cursor/install.sh
source .venv/bin/activate
python -m pytest receipt_embeddings/tests receipt_dynamo_stream/tests -q
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
black --check --line-length=79 \
  receipt_embeddings/receipt_embeddings \
  receipt_embeddings/tests \
  receipt_dynamo_stream/receipt_dynamo_stream \
  receipt_dynamo_stream/tests/integration/test_vector_freshening_with_moto.py \
  receipt_upload/receipt_upload/vector_search.py \
  receipt_agent/receipt_agent/agents/question_answering/tools/search.py \
  receipt_agent/receipt_agent/agents/question_answering/graph.py \
  receipt_agent/tests/test_qa_search_vector_backend.py \
  tests/test_receipt_mcp_similarity_tools.py
isort --check-only --profile=black --line-length=79 \
  receipt_embeddings/receipt_embeddings/backend.py \
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

- `receipt_embeddings` + `receipt_dynamo_stream`: 291 passed.
- `receipt_upload` (repo root, `PYTHONPATH=.`): 947 passed, 13 skipped
  (960 collected — identical to E2's baseline).
- `receipt_agent`: 400 passed, 22 skipped.
- repository tests (`tests/`): 837 passed, 1 skipped.
- All five files compile; scoped Black and isort checks pass (modulo
  the pre-existing `test_message_builder.py` drift noted above, which
  predates this card).

## Not verified locally

- No live dev-table `SearchVectors` or Chroma Cloud calls were made;
  E3 requires no table writes and these fixes need none.
- The IAM policy additions were validated by JSON/py_compile and shape
  review, not by `pulumi preview` (no Pulumi credentials in this
  checkout) or a deployment.
- The Lambda fork was validated by import, compile, cross-server
  source-equality, and impl tests — not by deploying the container.
- Words already backfilled under the old status rule keep their stored
  `label_status` until the backfill re-runs or a label event freshens
  them; re-running the backfill after merge picks up the P1-2 rule
  (judge/phase-2 territory, no writes from this card).
