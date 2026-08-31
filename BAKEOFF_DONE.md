# Card X4 rubric self-report

Implementation commit:

- `2164c6dea` — dead-code sweep per `docs/chroma-removal/inventory-infra.md` §G

The judge's completion signal is this file. It was not committed with the
sweep.

BAKEOFF.md Mode switch assigns Cursor **X4**: dead-code sweep per
"SPEC §6 G / inventory-infra §G". SPEC §6 G is the legacy-URN alias landmine
(X2). The actual dead list is **inventory-infra §G**. This card followed that
list and nothing else.

Fences held: `git diff --name-only 94816300f...HEAD` contains no path under
`receipt_agent/`, `scripts/receipt_mcp_server.py`, or
`infra/mcp_server_lambda/`.

## Gates

- CI-relevant local suites green (commands below).
- Grep proof per deleted item (this file).
- `pulumi preview` is an **X2** gate, not X4; not run (AGENTS.md: no local
  `pulumi`).

## Scope judgment (kept on purpose)

Inventory §G lists `chromadb_circuit_breaker` next to `protect_chromadb_call`
as "orphaned … once compaction goes". Compaction is not going in this card.
`chromadb_circuit_breaker` and `trace_chromadb_delta_save` are live in
`line_polling.py` / `word_polling.py`. They stay.

Compaction copies of `track_chromadb_operation` /
`trace_chromadb_operation` / `protect_chromadb_call` live under
`infra/chromadb_compaction/lambdas/utils/` — different files than the listed
`unified_embedding/utils/*` paths. `trace_chromadb_operation` is re-exported
by `utils/__init__.py` and used by the live stream processor. They stay.

Live `DualChromaClient` is the nested class at
`receipt_agent/receipt_agent/clients/factory.py:125`. Untouched (fenced, and
inventory says it is unrelated).

`CLAUDE.md`, `AGENTS.md`, `.github/pull_request_template.md` mention the
still-live `receipt_chroma` package. Not edited.

`infra/embedding_step_functions/tests/README.md` still cites
`simple_lambdas/prepare_merge_pairs/test_handler.py`. That README is not on
the dead list; left in place.

Live `simple_lambdas/{backfill_control,find_unembedded,list_pending,mark_batches_complete,normalize_poll_batches_data}` remain. Live container handlers
`unified_embedding/handlers/{split_into_chunks,create_chunk_groups}.py` remain.

Delete-consequence edits only (not extra dead items): drop the
`receipt_chroma/README.md` links in root `README.md` and `docs/README.md`;
drop `readme = "README.md"` from `receipt_chroma/pyproject.toml` so
`pip install -e receipt_chroma` still works; drop the echo in
`get_embedding_metrics.sh` that pointed at deleted `get_chromadb_metrics.sh`;
remove `test_processor_imports` from uncollected
`test_lambda_imports.py` (inventory: the only importer of `processor/`).

## Grep proofs (per deleted item)

Run from repo root. `rg` respects `.gitignore` (`.venv` excluded). Inventory
and archive docs are excluded so the dead *list itself* does not count as a
live hit.

```bash
# paths gone from the tree
python3.13 - <<'PY'
from pathlib import Path
root = Path('.')
items = [
    "infra/chromadb_compaction/lambdas/processor",
    "infra/embedding_step_functions/unified_embedding/utils/dual_chroma_client.py",
    "infra/embedding_step_functions/simple_lambdas/prepare_chunk_groups",
    "infra/embedding_step_functions/simple_lambdas/prepare_merge_pairs",
    "infra/embedding_step_functions/simple_lambdas/split_into_chunks",
    "infra/embedding_step_functions/simple_lambdas/create_chunk_groups",
    "infra/components/test_docker_package_contexts.py",
    "infra/embedding_step_functions/unified_embedding/handlers/tests/test_close_chromadb_client.py",
    "infra/embedding_step_functions/unified_embedding/handlers/tests/standalone_test_close_client.py",
    "infra/chromadb_compaction/tests/conftest.py.bak",
    "infra/fix_chromadb_buckets.md",
    "infra/README_CHROMADB_METRICS.md",
    "infra/VALIDATION_PIPELINE_CHROMADB_MIGRATION.md",
    "infra/chromadb_compaction/README.md",
    "infra/chromadb_compaction/README_stream_processor.md",
    "infra/chromadb_compaction/QUEUE_STRATEGY.md",
    "infra/chromadb_compaction/get_chromadb_metrics.sh",
    "infra/embedding_step_functions/MEMORY_OPTIMIZATION.md",
    "infra/embedding_step_functions/MIGRATION_COMPLETE_SUMMARY.md",
    "infra/embedding_step_functions/WORD_INGEST_MIGRATION_GUIDE.md",
    "infra/embedding_step_functions/WORKFLOW_STEPS_REFERENCE.md",
    "receipt_chroma/README.md",
]
for p in items:
    assert not (root / p).exists(), p
print("all listed paths absent")
PY
```

Result (this commit): `all listed paths absent`.

### 1. `infra/chromadb_compaction/lambdas/processor/`

```bash
rg -n 'from processor import|import processor|lambdas/processor' \
  --glob '!docs/chroma-removal/**' --glob '!docs/archive/**'
```

No matches. Directory does not exist. Live stream processor remains
`infra/chromadb_compaction/lambdas/stream_processor.py` (does not import
`processor/`).

### 2. `unified_embedding/utils/dual_chroma_client.py`

```bash
rg -n 'dual_chroma_client' --glob '!docs/chroma-removal/**' --glob '!docs/archive/**'
```

No matches. Live DualChromaClient is still
`receipt_agent/receipt_agent/clients/factory.py:125` (`class DualChromaClient`).

### 3. Four unwired `simple_lambdas`

```bash
rg -n 'simple_lambdas/(prepare_chunk_groups|prepare_merge_pairs|split_into_chunks|create_chunk_groups)' \
  --glob '!docs/chroma-removal/**' --glob '!docs/archive/**'
```

Remaining hits (2), both in a file **not** on the dead list:

```
infra/embedding_step_functions/tests/README.md:37:pytest …/prepare_merge_pairs/test_handler.py -v
infra/embedding_step_functions/tests/README.md:115:    pytest …/prepare_merge_pairs/test_handler.py -v
```

Those four directories are gone. Live siblings
`backfill_control`, `find_unembedded`, `list_pending`,
`mark_batches_complete`, `normalize_poll_batches_data` remain.

### 4. `infra/components/test_docker_package_contexts.py`

```bash
rg -n 'test_docker_package_contexts|CHROMA_IMAGE_CONTEXTS' \
  --glob '!docs/chroma-removal/**' --glob '!docs/archive/**'
```

No matches. File absent.

### 5. `test_close_chromadb_client.py` + `standalone_test_close_client.py`

```bash
rg -n 'test_close_chromadb_client|standalone_test_close_client' \
  --glob '!docs/chroma-removal/**' --glob '!docs/archive/**'
```

No matches. Both files absent.

### 6. Superseded monitoring builders

```bash
rg -n '_create_lambda_widgets\(|_create_step_function_widgets\(' \
  --glob '!docs/chroma-removal/**' --glob '!docs/archive/**'
```

No matches (unresolved names). Live `_create_lambda_widgets_resolved` /
`_create_step_function_widgets_resolved` remain and are still called from
`_create_dashboard`.

### 7. `infra/chromadb_compaction/tests/conftest.py.bak`

Path absent.

### 8. Orphaned unified_embedding emitters + dashboard widget

```bash
rg -n 'track_chromadb_operation|protect_chromadb_call' \
  infra/embedding_step_functions/unified_embedding
rg -n 'def trace_chromadb_operation' \
  infra/embedding_step_functions/unified_embedding
rg -n 'ChromaDBOperationDuration' infra/embedding_step_functions
```

All three: no matches.

Kept (live, not orphaned):

```
infra/embedding_step_functions/unified_embedding/utils/circuit_breaker.py
  def chromadb_circuit_breaker()
infra/embedding_step_functions/unified_embedding/handlers/line_polling.py:35,892
infra/embedding_step_functions/unified_embedding/handlers/word_polling.py:35,992
infra/embedding_step_functions/unified_embedding/utils/tracing.py
  def trace_chromadb_delta_save(...)
```

Compaction-utils copies (not the listed paths) still define
`track_chromadb_operation` / `trace_chromadb_operation` /
`protect_chromadb_call`.

### 9. Listed dead docs

```bash
rg -n 'fix_chromadb_buckets|README_CHROMADB_METRICS|VALIDATION_PIPELINE_CHROMADB_MIGRATION|README_stream_processor|get_chromadb_metrics|MEMORY_OPTIMIZATION.md|MIGRATION_COMPLETE_SUMMARY|WORD_INGEST_MIGRATION_GUIDE|WORKFLOW_STEPS_REFERENCE' \
  --glob '!docs/chroma-removal/**' --glob '!docs/archive/**'
```

No matches. `receipt_chroma/README.md` is gone;

```bash
rg -n 'receipt_chroma/README' --glob '!docs/chroma-removal/**' --glob '!docs/archive/**'
```

No matches (root README bullet and `docs/README.md` link removed).

## Verify commands (fresh checkout)

`.cursor/install.sh` on this tree runs `pip install -e receipt_embeddings`
before `receipt_dynamo`. That fails because `receipt-embeddings` depends on
unpublished `receipt-dynamo` (pre-existing on `main`; not this card). Use
the CI `python-tests` / `repository-tests` order instead:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e receipt_dynamo
pip install --no-deps -e receipt_embeddings
pip install --no-deps -e receipt_dynamo_stream
pip install --no-deps -e receipt_chroma
pip install --no-deps -e receipt_places
pip install --no-deps -e receipt_agent
pip install --no-deps -e receipt_upload
pip install boto3 chromadb "openai>=2.8.1,<3.0.0" Pillow \
  pillow-avif-plugin langsmith langgraph \
  "langchain-core>=0.3.0" "langchain-openai>=0.2.0" httpx \
  pydantic pydantic-settings structlog requests tenacity \
  segno python-barcode numpy
pip install pytest pytest-asyncio pytest-mock pytest-cov pytest-xdist \
  pytest-timeout pytest-rerunfailures moto responses
pip install black isort
```

Then, from the repo root:

```bash
# CI lambda-syntax job
python -m py_compile $(find infra -name '*.py' -not -path '*/.venv*/*' \
  -not -path '*/node_modules/*')

# CI python-tests receipt_chroma (readme field dropped)
python -m pytest receipt_chroma/tests -n auto --timeout=120 --tb=short \
  --maxfail=5 --reruns 1 --reruns-delay 2 \
  -m "not end_to_end and not slow and not performance and not unused_in_production"

# CI lambda-syntax compaction lock suite
python -m pytest -q infra/tests/test_compaction_lock_config.py
```

Results on this machine (2026-08-31):

- `py_compile`: `PY_COMPILE_OK`
- `receipt_chroma/tests`: **581 passed, 7 skipped**
- `infra/tests/test_compaction_lock_config.py`: **6 passed**
- black/isort `--check` on the five edited Python files: clean

## Not verified locally

- Full GitHub Actions matrix (`python-tests` for packages this card did not
  touch, TypeScript, repository-tests, e2e).
- `pulumi preview` on either stack (X2 gate; AGENTS.md forbids local
  `pulumi`).
- `./.cursor/install.sh` end-to-end (embeddings-before-dynamo order fails on
  current `main`; Node 22/`npm ci` unused by this Python-only sweep).
- Collection of uncollected `test_lambda_imports.py` (still uncollected;
  processor test removed so a future collection cannot import deleted code).
