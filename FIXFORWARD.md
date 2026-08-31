# Round A fix-forward (applied to the codex winner)

Judged fix-forward on top of implementation commit `001380380`. Six
commits, offline-only; no live captures were run (the judge runs the
live gate). Test counts: **35 before, 53 after** (both the bare-python3
repo-root run and the `.venv` recipe).

## 1. Skip-don't-crash in live capture

`fix(harness): skip per-receipt failures in live capture instead of crashing`

A word with no stored vector used to raise
`ValueError('Chroma has no vector for ...')` and abort the whole run.
Live capture is restructured around a per-receipt `_capture_receipt`
helper whose corpus/query output merges only on success, so per-item
failures — missing vectors, Chroma quota/rate errors, receipts absent
from Chroma or DynamoDB — print a `SKIP <key>: [<reason>] <detail>` line
and the run continues. All skips are collected; the run ends with a
count-by-reason skip report, and the minimum-receipts floor check moved
to end-of-run, failing alongside the skip report (no fixture written)
when the surviving count is under the floor.

## 2. Configurable floor, top-up, and cost cap

`feat(harness): configurable floor, --extra-receipts top-up, and --limit cap`

New `capture_golden.py` flags:

- `--min-receipts` (default 40) — the end-of-run floor.
- `--extra-receipts FILE` — JSON `[{image_id, receipt_id}]` top-up for
  the golden set, deduplicated against the base set (pattern from the
  claude entrant).
- `--limit N` — cap on receipts processed, for judge cost control
  (pattern from the grok entrant). `--limit` under `--min-receipts` is
  rejected unless `--allow-under-floor` is passed explicitly, which also
  waives the end-of-run floor failure.

Note: `--offline-bootstrap` needs `--limit >= 30` (the schema requires
exactly 30 word neighbors and the bootstrap corpus is one word per
receipt); live capture has no such coupling.

## 3. Chroma Cloud quota constants + filter-contract tests

`feat(embeddings): Chroma Cloud quota constants and filter-contract tests`

`receipt_embeddings/receipt_embeddings/quotas.py` pins the Chroma Cloud
limits verified live 2026-08-31: `MAX_QUERY_EMBEDDINGS_PER_CALL = 20`
(the NumQueryEmbeddings quota) and `MAX_GET_LIMIT = 250`, with guard
helpers enforced on every Chroma-issuing path in the live capture source
(one embedding per query call; guarded get batches). It also adds
`build_chroma_where`, the adapter-side where-builder: bare `{key: val}`
for one filter, `{"$and": [...]}` for two or more — the shape real
chromadb requires. `FakeVectorIndex` now rejects `$`-prefixed operator
keys so pre-built where syntax cannot pass against the fake and fail
against the real client; contract tests pin the constants, both guard
limits, the where shapes, the fake's AND-of-equalities semantics, and
(via a recording stub) the live source's call shapes.

## 4. Cherry-picks

- `fix(tests): evict namespace stub shadowing receipt_embeddings from
  repo root` — from claude commit `3af7d18b2`. The outer
  `receipt_embeddings/` directory imports as an empty namespace package
  from the repo root and shadows the real package; the package suite's
  `tests/conftest.py` and both repo-root entry points now put the
  package root first on `sys.path` and evict any cached stub.
- `feat(harness): merchant_truth_agreement metric from known-truth
  manifests` — from the grok entrant. Fixture receipt rows carry
  `merchant_truth` when the manifest names the merchant; `evaluate.py`
  scores resolved merchant names against it
  (`merchant_truth_agreement_percent`, null when no truths, plus
  `merchant_truth_sample_count`). The committed bootstrap fixture is
  regenerated with the field (38 of 81 receipts carry a truth).
- `test(harness): in-process offline-evaluate runtime self-gate (< 60s)`
  — from the cursor entrant: times `evaluate_fixture` itself, not just
  the CLI subprocess.

## Judge verification

Fresh checkout, offline suite (both environments must pass 53 tests):

```bash
# bare interpreter, repo root
python3 -m pytest receipt_embeddings/tests -q

# BAKEOFF_DONE venv recipe
cd receipt_embeddings
../.venv/bin/python -m pytest tests -n auto --timeout=120 --tb=short \
  --maxfail=5 --reruns 1 --reruns-delay 2 \
  -m 'not end_to_end and not slow and not performance and not unused_in_production' \
  --cov --cov-report=xml
```

Lint (formatted with the package config; there is no repo-root config):

```bash
.venv/bin/black --config receipt_embeddings/pyproject.toml --check \
  scripts/similarity_harness/ receipt_embeddings/
.venv/bin/isort --settings-path receipt_embeddings/pyproject.toml --check \
  scripts/similarity_harness/ receipt_embeddings/
```

Expected live-capture invocation (judge-run; dev creds + dev table only),
with top-up and skip report:

```bash
python3 scripts/similarity_harness/capture_golden.py \
  --extra-receipts may26_batch.json \
  --out tests/fixtures/similarity/golden.json
# per-item failures print "SKIP <key>: [<reason>] <detail>" and the run
# continues; it ends with "skip report: N receipts skipped" plus
# count-by-reason lines, and fails (exit 1, nothing written) only if
# fewer than --min-receipts (default 40) receipts survive.

# cost-capped smoke run:
python3 scripts/similarity_harness/capture_golden.py \
  --limit 10 --allow-under-floor --out /tmp/smoke.json
```
