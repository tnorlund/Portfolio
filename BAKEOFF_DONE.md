# Round B self-report

Branch `bakeoff/B/grok`. Implementation commit `a9a8248ed`. This file is
the judge completion signal.

Install order is load-bearing: `receipt-dynamo` before
`receipt-embeddings` (unpublished local package). Commands below are the
ones that passed in this checkout.

## 1. Relocation complete

**Addressed.** `receipt_chroma.embedding.formatting` and
`receipt_chroma.embedding.openai` implementations live under
`receipt_embeddings/formatting/` and `receipt_embeddings/openai/`. Old
paths are thin re-export shims (`from receipt_embeddings.… import *` plus
explicit `__all__`). AST scan: zero `chromadb` imports in
`receipt_embeddings/receipt_embeddings/` (comments in `quotas.py` name
the Chroma filter contract; they are not imports).

**Verify**

```
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e receipt_dynamo
pip install --no-deps -e receipt_embeddings
pip install --no-deps -e receipt_dynamo_stream
pip install --no-deps -e receipt_chroma
pip install numpy boto3 chromadb "openai>=2.8.1,<3.0.0" \
  pytest pytest-mock pytest-cov pytest-xdist pytest-timeout \
  pytest-rerunfailures moto
python -m pytest receipt_embeddings/tests/test_no_chromadb.py -q
```

## 2. Shim completeness

**Addressed.** Existing importers still use
`receipt_chroma.embedding.{formatting,openai}` (including submodule
paths). Relocated unit tests import the new paths and pass.
`receipt_chroma` tests that still call the old paths go through the
shims. CI install blocks for `receipt_chroma`, `receipt_upload`,
`receipt_agent`, and `repository-tests` now install `receipt_embeddings`
after `receipt_dynamo`.

One importer was adjusted: `scripts/similarity_harness/capture_golden.py`
now imports `group_lines_into_visual_rows` from the public
`receipt_chroma.embedding.formatting` facade instead of the
`.line_format` submodule. That import already failed
`test_external_runtime_callers_use_public_facades` on the Round A
baseline; the facade import is the same shim and keeps the chroma suite
green.

**Verify**

```
# same venv as §1
cd receipt_embeddings
python -m pytest tests -q --timeout=120 \
  -m "not end_to_end and not slow and not performance and not unused_in_production"
cd ../receipt_chroma
python -m pytest tests -q --timeout=120 \
  -m "not end_to_end and not slow and not performance and not unused_in_production"
```

Local result: receipt_embeddings 122 passed; receipt_chroma 507 passed,
7 skipped.

## 3. Behavior identity

**Addressed.** `receipt_chroma/tests/unit/test_embedding_shims.py` asserts
old-path and new-path exports are the **same objects** (package + every
shimmed submodule). Swift generators were run twice; bytes matched each
other and the committed fixtures (no fixture diff).
`test_section_and_structure_generators_are_byte_stable` in
`receipt_upload` asserts `generate()` twice is identical.

**Verify**

```
# same venv as §1, plus the upload stack used by the generators
pip install --no-deps -e receipt_places -e receipt_agent -e receipt_upload
pip install pydantic pydantic-settings structlog requests tenacity httpx Pillow
python -m pytest receipt_chroma/tests/unit/test_embedding_shims.py -q
python receipt_ocr_swift/Scripts/generate_section_parity.py
python receipt_ocr_swift/Scripts/generate_receipt_structure_parity.py
python receipt_ocr_swift/Scripts/generate_section_parity.py
python receipt_ocr_swift/Scripts/generate_receipt_structure_parity.py
git diff --exit-code \
  receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/section_assignment_parity_expected.json \
  receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/receipt_structure_parity_expected.json
```

## 4. Documented reproducibility

**Addressed.** The install lines in §1 are the working set from this
machine (`python3.13` = 3.13.15). There is no hidden `PYTHONPATH`.
Editable installs use `--no-deps` for unpublished sibling packages, then
explicit PyPI pins — the same pattern as CI. Adding `receipt-embeddings`
as a chroma dependency without installing the local package first would
hit PyPI; the documented order avoids that.

**Verify:** run §1 then §2 from a clean tree with only those commands.

## 5. Lean diff

**Addressed.** Diff is moves, shims, fixture-regen (empty), identity /
no-chromadb tests, pyproject deps, and CI/install order. No formatter or
OpenAI logic rewrite. `capture_golden.py` one-line facade import as in
§2.

## 6. Final commit is this file

**Addressed.** Implementation landed in `a9a8248ed`. This file is the
subsequent commit.

## Not verified locally

- Full `receipt_upload` pytest matrix (generators + new stability test
  exercised; the rest of that package's suite was not run).
- Full `receipt_agent` pytest matrix.
- Swift compiler / `.github/workflows/swift-ci.yml` (Python generators
  only).
- Live Chroma Cloud capture (no `CHROMA_CLOUD_*` in the environment).
- `python-tests` lint job for packages other than embeddings/chroma
  shims.
