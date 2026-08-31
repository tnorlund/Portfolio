# Round B rubric self-report

Implementation commit: `64932a15d`

All verification ran offline against a freshly created Python 3.13 venv in
this worktree (no pre-existing `.venv`). No live AWS, Chroma Cloud, or
OpenAI calls. The moved OpenAI helpers keep their existing empty-result
and missing-file guards; this round does not add new live-system paths.

Swift parity fixtures were regenerated twice from the relocated formatting
surface (via the back-compat shims). Both runs were byte-identical to each
other and to the files already committed, so the fixtures did not change.

## 1. Relocation complete

`receipt_chroma.embedding.formatting` and `receipt_chroma.embedding.openai`
now live at `receipt_embeddings.formatting` and `receipt_embeddings.openai`
(`git mv`; `git log --follow` on `line_format.py` still reaches the chroma
history). Internal imports in the moved tree point at
`receipt_embeddings.*`. An AST scan of
`receipt_embeddings/receipt_embeddings/**/*.py` finds zero `chromadb`
imports.

Verify with:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e receipt_dynamo
pip install -e "receipt_embeddings[test]"
pip install -e "receipt_embeddings[lint]"
black --check --line-length=79 receipt_embeddings
isort --check-only --profile=black --line-length=79 receipt_embeddings
.venv/bin/pytest receipt_embeddings/tests/test_relocation.py \
  -q -k package_has_zero_chromadb_imports
```

## 2. Shim completeness

Old paths are thin re-exports (`from receipt_embeddings.… import *` plus
explicit `__all__`) at both the package and submodule level, so
`receipt_chroma.embedding.formatting.line_format` and
`receipt_chroma.embedding.openai.batch_status` keep working. Existing
callers were not rewritten onto `receipt_embeddings` except one Round A
harness import that the chroma public-facade contract already forbids
(see below).

The full `receipt_chroma` suite was run through the shims: **576 passed,
7 skipped**. `receipt_upload` anti-drift parity tests that import
`build_receipt_rows` through the old path: **6 passed**. Agent has no
direct `embedding.{formatting,openai}` imports.

CI install order now installs `receipt_dynamo` then `receipt_embeddings`
before chroma/upload/agent so a matrix job from a clean checkout can
import the shims. `.cursor/install.sh` matches that order.

Verify with:

```bash
source .venv/bin/activate
pip install --no-deps -e receipt_dynamo_stream
pip install --no-deps -e receipt_chroma
pip install boto3 chromadb "openai>=2.8.1,<3.0.0"
pip install pytest pytest-mock pytest-cov pytest-xdist pytest-timeout \
  pytest-rerunfailures moto
pip install -e "receipt_chroma[lint]"
black --check --line-length=79 receipt_chroma
isort --check-only --profile=black --line-length=79 receipt_chroma
cd receipt_chroma
python -m pytest tests -n auto --timeout=120 --tb=short --maxfail=5 \
  --reruns 1 --reruns-delay 2 \
  -m "not end_to_end and not slow and not performance and not unused_in_production"
cd ..
```

One Round A live-capture helper imported the internal
`formatting.line_format` module and failed
`test_external_runtime_callers_use_public_facades`. It now imports
`group_lines_into_visual_rows` from the public
`receipt_chroma.embedding.formatting` facade (still the old package, not
`receipt_embeddings`). That is the only importer edit.

## 3. Behavior identity

`receipt_chroma/tests/unit/test_embedding_relocation_identity.py` asserts
old-path and new-path package `__all__` names are the same function
objects, and that `format_row_embedding_input` on a fixed two-line row
returns the identical bytes through both paths.
`receipt_embeddings/tests/test_relocation.py` repeats the formatting and
OpenAI-helper outputs on those fixed inputs.

Swift section and structure fixtures were generated twice:

```bash
source .venv/bin/activate
pip install --no-deps -e receipt_upload
pip install "Pillow>=11.2.1"
python receipt_ocr_swift/Scripts/generate_section_parity.py \
  --output /tmp/section_parity_1.json
python receipt_ocr_swift/Scripts/generate_section_parity.py \
  --output /tmp/section_parity_2.json
cmp /tmp/section_parity_1.json /tmp/section_parity_2.json
cmp /tmp/section_parity_1.json \
  receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/section_assignment_parity_expected.json
python -c "
import sys
from pathlib import Path
sys.path.insert(0, 'receipt_ocr_swift/Scripts')
import generate_receipt_structure_parity as structure
first = structure.generate()
second = structure.generate()
assert first == second
committed = Path(
    'receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures'
    '/receipt_structure_parity_expected.json'
).read_text(encoding='utf-8')
assert first == committed
print('structure twice and vs committed: byte-identical', len(first))
"
.venv/bin/pytest receipt_chroma/tests/unit/test_embedding_relocation_identity.py -q
.venv/bin/pytest receipt_upload/tests/test_swift_line_item_parity_fixture.py -q
```

Both generators matched the committed fixtures (38 receipts; structure
116437 bytes). Nothing to rewrite in-tree.

## 4. Documented reproducibility

The commands above are the CI matrix install blocks plus the two
generator scripts. They assume:

- Python 3.13 (`python3.13 -m venv .venv`), same pin as CI
- `pip install -e receipt_dynamo` **before**
  `pip install -e "receipt_embeddings[test]"` so pip does not look for
  unpublished `receipt-dynamo` on PyPI
- chroma/upload jobs install `receipt_embeddings` explicitly because
  chroma is installed `--no-deps` and the shims import it at runtime

No extra `PYTHONPATH` is required once those editable installs are done.
`.cursor/install.sh` uses the same dynamo-then-embeddings order.

## 5. Lean diff

Moves, shims, pyproject/CI/install order, identity tests, and the one
facade-import fix in `capture_golden.py`. No formatting drive-by on
Round A files. Swift fixtures unchanged after regen. Lambda Dockerfiles
were not edited (see not-verified).

## 6. Final commit

This file. Do not treat earlier commits as the completion signal.

## Final package gates (CI-shaped)

```bash
cd receipt_embeddings
../.venv/bin/python -m pytest tests -n auto --timeout=120 --tb=short \
  --maxfail=5 --reruns 1 --reruns-delay 2 \
  -m "not end_to_end and not slow and not performance and not unused_in_production" \
  --cov --cov-report=xml
```

56 passed (this worktree). Then the chroma command in §2: 576 passed,
7 skipped.

## Not verified locally

- Full `receipt_upload` and `receipt_agent` matrix jobs. Those CI legs
  install langgraph/langchain/places; two `test_section_pipeline_contract`
  cases that import `embedding_processor` failed here only with
  `ModuleNotFoundError: langchain_core` in a slimmer venv. Tests that
  import `build_receipt_rows` through the shim (parity + the rest of
  that file) passed.
- Lambda/CodeBuild images that `COPY receipt_chroma/` and
  `pip install /tmp/receipt_chroma`. `receipt-chroma` was **not** given
  a `receipt-embeddings` PyPI dependency, so those Dockerfiles still
  resolve; a runtime import of formatting/openai inside an image that
  does not also install `receipt_embeddings` will `ImportError` until a
  follow-up copies that package into the build context. This round does
  not deploy.
- Live OpenAI, Chroma Cloud, or DynamoDB. No new test doubles were
  added; existing chroma openai tests still pin the moved helpers
  through the shims.
