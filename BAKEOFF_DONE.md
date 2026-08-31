# Round B rubric self-report — receipt_embeddings relocation

Branch `bakeoff/B/claude`, base `7a2aadfb3` (merged Round A winner).
Implementation commits: `cef986c35` (relocation + shims + deps),
`85d4d8ffe` (identity/guard tests), `4eb8e544c` (CI install wiring).

All verification ran offline against the local checkout; nothing touched dev
or prod tables, no vector index was created or altered, nothing was pushed.

## Environment for every verify command below

From a fresh checkout of this branch, at the repo root (macOS,
Homebrew `python3.13`; the repo's CI Python minor):

```bash
python3.13 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e receipt_dynamo
.venv/bin/pip install --no-deps -e receipt_embeddings -e receipt_dynamo_stream \
  -e receipt_chroma -e receipt_places -e receipt_agent -e receipt_upload
.venv/bin/pip install numpy "openai>=2.8.1,<3.0.0" boto3 chromadb Pillow \
  pillow-avif-plugin langsmith langgraph "langchain-core>=0.3.0" \
  "langchain-openai>=0.2.0" httpx pydantic pydantic-settings structlog \
  requests tenacity segno python-barcode
.venv/bin/pip install pytest pytest-asyncio pytest-mock pytest-cov \
  pytest-xdist pytest-timeout pytest-rerunfailures moto responses
```

(Local siblings are installed `--no-deps` because they are unpublished —
the same pattern the CI matrix jobs use.) The full sequence, including every
command below, was executed verbatim against a fresh `git clone` of this
branch before this file was committed.

## 1. Relocation complete; zero chromadb imports

`receipt_chroma.embedding.formatting` and `.openai` now live at
`receipt_embeddings/receipt_embeddings/{formatting,openai}` (moved with
`git mv`; git records 8 of the 10 files as renames at 99–100% similarity —
the two `__init__.py` files pair with the shims left at the old paths).
`receipt_embeddings` imports no chromadb anywhere, enforced permanently by
an AST-scan test.

```bash
# exits 0 iff no chromadb import exists (Round A's quotas.py mentions
# chromadb in comments; imports are what the rubric bans, and the AST test
# below checks them structurally)
test -z "$(grep -rnE '^[[:space:]]*(import chromadb|from chromadb)' receipt_embeddings/receipt_embeddings || true)"
cd receipt_embeddings && ../.venv/bin/python -m pytest tests/test_no_chromadb.py -q
```

## 2. Shim completeness; suites green

The old paths are star-re-export shims with explicit `__all__`, plus
`sys.modules` aliases so *submodule* imports
(`receipt_chroma.embedding.formatting.line_format`, `.openai.realtime`, …)
resolve to the identical relocated module objects. Zero importer files
changed: every one of the 30+ cross-package import sites (infra lambdas,
receipt_upload, receipt_agent, scripts, Swift parity generators,
receipt_chroma internals, tests) still imports the old paths.

Suite results in the environment above (also reproduced with venvs built
exactly like the `receipt_chroma` and `receipt_embeddings` CI matrix jobs):

- `receipt_chroma`: **580 passed, 7 skipped**, 1 pre-existing failure
  (`test_public_api.py::test_external_runtime_callers_use_public_facades`) —
  it fails *identically on the merge-base* because Round A's
  `scripts/similarity_harness/capture_golden.py:1031` imports a banned
  internal module, and the latest `main` CI run's `Python (receipt_chroma)`
  job is red for the same reason. Not caused by, and per the standing
  don't-touch-prior-winners rule not fixed by, this round. The command
  carries CI's own `--reruns 1 --reruns-delay 2` because
  `test_delta_merging.py::test_merge_multiple_deltas` (untouched compaction
  code) is load-sensitive — main.yml's comment documents exactly this
  moto-S3/Chroma-lock flake class as the reason those flags exist; see §6.
- `receipt_embeddings`: 54 passed (includes the CI-style
  `--cov --cov-report=xml` run).
- `receipt_upload`: green, with 4 files `--ignore`d that fail *collection on
  the merge-base too* in a local checkout (they import the repo-root `infra`
  package, which only resolves in the CI job's environment; that CI job is
  green on `main`).
- `receipt_agent`: 387 passed, 22 skipped.

```bash
cd receipt_chroma && ../.venv/bin/python -m pytest tests -q -n auto --timeout=120 \
  --reruns 1 --reruns-delay 2 \
  --deselect tests/unit/test_public_api.py::test_external_runtime_callers_use_public_facades
cd receipt_embeddings && ../.venv/bin/python -m pytest tests -q -n auto --timeout=120
cd receipt_upload && ../.venv/bin/python -m pytest tests -q -n auto --timeout=180 \
  -m "not end_to_end and not slow and not performance and not unused_in_production" \
  --ignore=tests/test_line_item_boundary_extension.py \
  --ignore=tests/test_line_item_reocr_trigger.py \
  --ignore=tests/test_line_item_worker_consistency.py \
  --ignore=tests/test_section_assignment_evaluation.py
cd receipt_agent && ../.venv/bin/python -m pytest tests -q -n auto --timeout=120 \
  -m "not end_to_end and not slow and not performance and not unused_in_production"
```

(Each `cd` is from the repo root.) Because the shims import
`receipt_embeddings`, the CI matrix jobs that install `receipt_chroma`
editable now install `receipt_embeddings` too, and `receipt_embeddings` got
its own install case (its new `receipt-dynamo` dependency is unpublished, so
the default `pip install -e pkg[test]` resolve would reach for PyPI and
fail) — commit `4eb8e544c`, the minimum CI wiring for the relocation.

## 3. Behavior identity

`receipt_chroma/tests/unit/test_embedding_relocation_shims.py` asserts, for
all 8 submodules, `import_module(old) is import_module(new)` — the same
module object, the strongest form of "old path is new path" — and that both
shim packages re-export every `__all__` name as the identical object. The
pre-existing `test_public_api.py` identity assertions
(`build_receipt_rows is internal_build_receipt_rows`, …) also still pass
through the shims.

Swift parity: both generators were run against the relocated code **twice**;
the two runs are byte-identical, and the output is byte-identical to the
fixtures already committed on `main` (`git diff` clean) — the relocation
provably changed no formatting behavior, so the fixture files carry no diff
in this PR.

```bash
cd receipt_chroma && ../.venv/bin/python -m pytest tests/unit/test_embedding_relocation_shims.py -q
cd ..
.venv/bin/python receipt_ocr_swift/Scripts/generate_section_parity.py
.venv/bin/python receipt_ocr_swift/Scripts/generate_receipt_structure_parity.py
cp receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/section_assignment_parity_expected.json /tmp/section.run1.json
cp receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/receipt_structure_parity_expected.json /tmp/structure.run1.json
.venv/bin/python receipt_ocr_swift/Scripts/generate_section_parity.py
.venv/bin/python receipt_ocr_swift/Scripts/generate_receipt_structure_parity.py
cmp receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/section_assignment_parity_expected.json /tmp/section.run1.json
cmp receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/receipt_structure_parity_expected.json /tmp/structure.run1.json
git diff --exit-code receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/
```

swift-ci's existing Python parity leg was also reproduced with its exact
venv recipe:

```bash
python3 -m venv .venv-parity
.venv-parity/bin/pip install --no-deps -e receipt_dynamo -e receipt_upload
.venv-parity/bin/pip install boto3 Pillow
.venv-parity/bin/python receipt_ocr_swift/Scripts/generate_line_items_parity.py --check
```

## 4. Documented reproducibility

The environment block above plus every command in §§1–3 and §5 were run
verbatim, in order, in a fresh `git clone` of this branch before this file
was committed (single scripted pass, exit 0). No undocumented PYTHONPATH,
no undocumented installs; the two documented deviations (one deselect, four
ignores) are pre-existing on the merge-base and explained inline.

## 5. Lean diff

17 files, +277/−64 (before this report): the 10 moved files (8 tracked as
renames; intra-package import paths rewritten, plus the import re-grouping
isort itself demands for the moved files in the receipt_embeddings CI lint
context), 2 shim `__init__.py`s, 2 pyproject dependency declarations
(`receipt_embeddings` ← openai + receipt-dynamo; `receipt_chroma` ←
receipt-embeddings), the minimum main.yml install wiring, and 2 test files.
No importer was touched, no refactors, no fixture churn. Lint verified with
the exact CI commands:

```bash
.venv/bin/pip install "black==26.5.1" "isort==8.0.1"
.venv/bin/black --check --line-length=79 receipt_embeddings
.venv/bin/isort --check-only --profile=black --line-length=79 receipt_embeddings
.venv/bin/black --check --line-length=79 receipt_chroma
.venv/bin/isort --check-only --profile=black --line-length=79 receipt_chroma
```

## 6. Not verified locally

- **swift-ci on a GitHub runner** (swift build + `swift test` + its parity
  regeneration step): this machine has no full Xcode toolchain. The
  Python side of the gate — both generators against relocated code, twice,
  byte-identical, matching committed fixtures — is verified above; no Swift
  source or fixture byte changed, so the Swift tests see identical inputs.
- **The `receipt_upload` CI job's `import infra` resolution** and the four
  test files behind it (pre-existing local-environment gap, detailed in §2).
- **The pre-existing `receipt_chroma` CI red on `main`** (capture_golden
  facade violation) remains red here for the same single test; fixing it
  means editing Round A's file, which the standing rules reserve.
- **Live OpenAI/DynamoDB behavior** of the relocated `openai` subpackage:
  exercised only through the existing unit suites (which mock both), same
  as before the move.
- **The `test_merge_multiple_deltas` flake is not deterministically
  attributable**: single-shot runs of that one test intermittently fail
  under load on this machine (`total_merged` 0 or 1 of 2; errors swallowed
  into per-run results by design). It exercises compaction/delta code this
  round does not touch, slows to 100% pass when a real logger is attached,
  and CI's `--reruns 1` exists precisely for this flake class per the
  main.yml comment. An interleaved 10×-each base-vs-branch sample under
  identical quiet conditions was 10/10 green on both sides (the observed
  failures coincided with heavy parallel pip installs on this machine), but
  an exhaustive flake-rate equivalence proof was not attempted.
