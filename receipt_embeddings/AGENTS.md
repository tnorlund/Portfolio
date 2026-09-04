# receipt_embeddings/ (vector storage and search)

Deltas to the root `AGENTS.md`. Prod runs `VECTOR_BACKEND=dynamodb`.

- The DynamoDB vector index is asynchronous: never assume read-after-write in
  code or tests. `SearchVectors` returns at most 100 results and supports
  equality filters only; paginate and post-filter in Python for anything else.
- Never create, alter, or delete a vector index from package code or scripts;
  indexes are provisioned by infra only.
- Backfills and batch embeds skip-and-report per-item failures; never abort the
  batch on one bad item. Write `...#EMBEDDING` sort keys only for golden and
  extras items, never speculatively.
- Verify commands must work from a fresh checkout; if a step needs
  `PYTHONPATH` or an extra install, write it in the PR, not in your head.
- Tests: `pytest receipt_embeddings/tests -m unit` is offline.
  `vector_integration` hits a live backend and `performance` is opt-in
  (`RECEIPT_EMBEDDINGS_PERF=1`); agents run neither. CI installs
  `receipt_dynamo` editable first because this package depends on it.
