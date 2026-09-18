# RFC: Python package consolidation

Status: proposal. Evidence gathered 2026-09-17 against `origin/main`
(`4d69d30b4`). Line numbers cite that revision.

## Summary

The monorepo ships ten Python distributions plus `infra/` and `scripts/`.
None of them is unambiguously dead, so this RFC removes no package. It
records the dependency graph, classifies every package with evidence, and
proposes a staged plan that would take the PR-gating Python matrix from
thirteen legs to nine without moving any code in this PR.

Changes landed alongside this document:

- Removed three dead files at the root of `receipt_places/`
  (`health_check.py`, `monitoring.py`, `rollout_strategy.py`). They sit
  outside the wheel (`receipt_places/pyproject.toml:65-67` packages only
  `receipt_places/receipt_places`), nothing imports them, and no test
  references them.
- Added `/receipt_nutrition` to the pip directories in
  `.github/dependabot.yml`. The package has had a `pyproject.toml` since
  2026-09-08 and was the only distribution missing from the list.

`receipt_langsmith`, named in the original task, was already deleted on
2026-09-11 (`23aa3873e`, "retire unused Spark analytics and export code").
The only remaining mention is a `sys.modules` stub tuple in
`infra/components/test_native_receipt_cache.py:226`, which is harmless.

## Method

1. Import graph: every `from <pkg>` / `import <pkg>` across `*.py` under
   the repo (excluding `node_modules`, venvs, build output), bucketed by
   the top-level directory of the importing file.
2. Deployment references: every `Dockerfile` under `infra/`, the Pulumi
   `source_paths` / layer definitions that reference package directories,
   `.github/workflows/*.yml`, `.github/dependabot.yml`, `Makefile`,
   `scripts/*.sh`, the Mac mini launch agents, and `~/.claude.json` MCP
   registrations.
3. Size: non-test lines of code, `def test_` count, last commit that was
   not a repo-wide formatter or runtime bump.

## Dependency graph

Declared dependencies come from each `pyproject.toml`. "Runtime
importers" counts non-test files outside the package itself.

| Package | Declared local deps | Runtime importers (files) | Lambda images that install it |
|---|---|---|---|
| `receipt_dynamo` | none | receipt_agent 23, receipt_upload 36, receipt_embeddings 7, receipt_layoutlm 7, receipt_nutrition 5, receipt_dynamo_stream 4, receipt_places 1, infra 38 (12 components), scripts 99, portfolio 12, synthesis_loop 14, glyph-studio 14, receipt_ocr_swift 2 | 12 of 13 (every image except glyph-mcp) |
| `receipt_dynamo_stream` | receipt-dynamo | `infra/receipt_update_queues/lambdas/stream_processor.py`; `tests/test_merge_receipt_lambda.py:35` | stream processor via Lambda layer (`infra/components/lambda_layer.py:1401-1407`, attached at `infra/receipt_update_queues/__init__.py:467`); copied into resegment image but never imported there (see Findings) |
| `receipt_embeddings` | receipt-dynamo | receipt_upload 8, receipt_agent 2, infra 5, scripts 7, receipt_ocr_swift 2, glyph-studio 1 (optional extra) | fix_place, mcp_server, merge, qa_agent, resegment, address_similarity, word_similarity, container_ocr |
| `receipt_places` | receipt-dynamo | `receipt_agent/clients/factory.py:21`, `receipt_agent/tools/places.py:28`, `receipt_upload/merchant_resolution/embedding_processor.py:427,1062` (all four are function-local imports); scripts 3 | fix_place, mcp_server, merge, qa_agent, resegment, container_ocr |
| `receipt_nutrition` | receipt-dynamo | `scripts/nutrition_harness/evaluate.py:12`, `scripts/seed_nutrition_pilot.py:52-65` | none |
| `receipt_upload` | receipt-dynamo, receipt-embeddings, receipt-places (`[test]`: receipt-agent) | receipt_agent 4, infra 8, scripts 33, portfolio 2, synthesis_loop 2, receipt_ocr_swift 3 | fix_place, mcp_server, merge, resegment, container_ocr; summary/line-item updaters cherry-pick individual modules as file assets (`infra/receipt_update_queues/__init__.py:488-607`) |
| `receipt_agent` | receipt-dynamo, receipt-embeddings, receipt-places | receipt_upload 4, infra 4, scripts 13, synthesis_loop 4, glyph-studio 11 | fix_place, mcp_server, merge, qa_agent, container_ocr; glyph image ships a one-file shim (`infra/glyph_mcp_lambda/lambdas/Dockerfile:24-34`) |
| `receipt_layoutlm` | none (receipt_dynamo imported but not declared) | `infra/routes/layoutlm_inference_cache_generator/lambdas/index.py`, `infra/sagemaker_training/train.py`, `scripts/agent_dataset_stats.py`; Mac mini CoreML export venv | layoutlm_inference_cache_generator, sagemaker_training |
| `receipt_logo` | none | `scripts/receipt_logo_mcp_server.py` (launcher only); SVG assets read by `tools/glyph-studio/server/mcp.mjs:796-813` | none |
| `glyphstudio` (`tools/glyph-studio/py`) | optional receipt-embeddings | `infra/glyph_mcp_lambda/lambdas/glyph_mcp_server_server.py`, `scripts/build_variant_layout.py`, synthesis_loop 3 | glyph-mcp |

Two edges are undeclared and form a cycle:

- `receipt_agent` imports `receipt_upload` at module top level in
  `receipt_agent/receipt_agent/utils/combination_selector.py:40-42`,
  `receipt_agent/receipt_agent/utils/receipt_coordinates.py:11`,
  `receipt_agent/receipt_agent/agents/label_evaluator/rendering/font_profile.py:70`,
  and `.../rendering/glyph_atlas.py:71,76`. `receipt_agent/pyproject.toml`
  does not list `receipt-upload`.
- `receipt_upload` imports `receipt_agent` at module top level in
  `receipt_upload/receipt_upload/label_validation/llm_validator.py:25-26`
  and `receipt_upload/receipt_upload/merchant_resolution/embedding_processor.py:34`.
  `receipt_upload/pyproject.toml` lists `receipt-agent` only under `[test]`.

CI hides this: both legs install the same six packages editable with
`--no-deps` (`.github/workflows/main.yml:167-172` and `:182-187`), so pip
never resolves the metadata.

## Per-package classification

Sizes are non-test source lines; "last change" excludes repo-wide
formatter and runtime bumps.

| Package | Source LOC | Tests | Last change | Class | Evidence |
|---|---|---|---|---|---|
| `receipt_dynamo` | 40,628 | 1,672 | 2026-09-13 | core | Imported by every other package and 12 infra components. |
| `receipt_dynamo_stream` | 1,801 | 132 | 2026-09-04 | thin | One deployed consumer (stream processor Lambda). Half the package (`vector_freshening.py`, 500 lines) is used only by that handler. |
| `receipt_embeddings` | 3,750 | 107 | 2026-09-08 | core | Eight images, 20 runtime importers across four packages. |
| `receipt_places` | 3,185 | 155 | 2026-07-29 | thin | Three lazy importers in two packages, all wrapping `PlacesClient`. `structlog` is declared (`receipt_places/pyproject.toml`) but never imported. |
| `receipt_nutrition` | 3,880 | 212 | 2026-09-10 | leaf, keep | Created 2026-09-08; no image, no infra reference, two scripts. Active work (nutrition enrichment plan), so not dead. |
| `receipt_upload` | 27,281 | 746 | 2026-09-11 | core | Five images plus two updater Lambdas; 50 importing files outside the package. |
| `receipt_agent` | 40,317 | 380 | 2026-09-13 | core | Five images plus the glyph shim; MCP server implementation. |
| `receipt_layoutlm` | 10,664 | 113 | 2026-09-06 | core, untested in CI | Two images and the Mac mini export path. No leg in `python-tests`; its 113 tests never run on a PR. |
| `receipt_logo` | 1,137 | 9 | 2026-07-31 | dormant tool | No CI leg, no image, not registered in `~/.claude.json`. Kept alive by a launcher script and by glyph-studio reading its five SVG assets. |
| `glyphstudio` | 17,462 | 286 | 2026-09-04 | core | Deployed in the glyph MCP image; tests run inside the `receipt_agent` leg (`main.yml:233-243`). |

Nothing meets the removal bar (zero importers, zero deployment
references, zero workflow or script references). `receipt_logo` is the
closest, but it has a live launcher (`scripts/receipt_logo_mcp_server.py`)
and glyph-studio's MCP server reads its asset directory, so deleting it is
an owner decision, not a mechanical cleanup.

## CI matrix today

`python-tests` (`main.yml:87-254`) runs seven packages on Python 3.14 (until 2026-09-17 it ran seven on 3.13 and six of them again on
3.14 (all but `receipt_nutrition`): thirteen legs. Glyph Studio tests run
as an extra step of the `receipt_agent` legs. `repository-tests`
(`main.yml:256-314`) installs the whole stack once more for root `tests/`
and `scripts/test_*.py`.

| Leg | 3.13 (retired) | 3.14 | Notes |
|---|---|---|---|
| receipt_dynamo | yes | yes | |
| receipt_dynamo_stream | yes | yes | Installs only receipt_dynamo. Would vanish under Stage 3. |
| receipt_embeddings | yes | yes | |
| receipt_places | yes | yes | Would vanish under Stage 2. |
| receipt_nutrition | yes | no | Correct: not shipped in any image. |
| receipt_upload | yes | yes | Same six-package install as receipt_agent. |
| receipt_agent | yes | yes | Same six-package install as receipt_upload; also runs glyph tests. |
| receipt_layoutlm | no | no | Gap: 113 tests never run in CI. |
| receipt_logo | no | no | Nine tests never run in CI. |

## Findings worth fixing regardless of consolidation

1. **Resegment image ships an unused package.**
   `infra/resegment_receipt_lambda/lambdas/Dockerfile:6,12,16` copies and
   installs `receipt_dynamo_stream`, `infra/resegment_receipt_lambda/infrastructure.py:196`
   hashes it into the build, and `scripts/lambda_image_import_check.py:68`
   asserts it imports. `resegment_receipt.py` never imports it. Every
   stream package change therefore rebuilds and redeploys the resegment
   Lambda for nothing.
2. **Undeclared mutual dependency** between `receipt_agent` and
   `receipt_upload` (see graph). Any consumer that installs one without the
   other gets an import error at module load; the qa_agent image installs
   `receipt_agent` without `receipt_upload` and works only because its
   handler path never reaches the four affected modules.
3. **`receipt_layoutlm` has no CI leg.** The comment at `main.yml:105-109`
   explains why it stays on 3.13, but nothing runs its unit tests on that runtime
   either.
4. **`structlog`** is declared by `receipt_places` and never imported.
5. **`infra/upload_images/container_ocr/pyproject.toml`** contains only a
   pytest config (no `[project]` table), yet `.github/dependabot.yml` lists
   the directory. Dependabot has nothing to update there.
6. `scripts/dev_workflow.sh:37-47,89-90` still offers a `receipt_label`
   package that no longer exists.

## Recommended target layout

```
receipt_dynamo/          core data layer; absorbs receipt_dynamo_stream as
                         receipt_dynamo.stream (Stage 3)
receipt_embeddings/      unchanged
receipt_agent/           absorbs receipt_places as receipt_agent.places
                         (Stage 2); declares receipt-upload
receipt_upload/          unchanged; declares receipt-agent, or the cycle
                         is broken by moving CORE_LABELS + llm_factory down
receipt_nutrition/       unchanged until it ships in an image
receipt_layoutlm/        unchanged (torch / coremltools pins are deliberate)
tools/glyph-studio/py/   unchanged
tools/receipt-logo/      receipt_logo moved beside glyph-studio, or deleted
                         by owner decision
```

Resulting `python-tests` matrix: receipt_dynamo, receipt_embeddings,
receipt_upload, receipt_agent, receipt_nutrition on 3.14,
plus a new receipt_layoutlm 3.13 unit leg: eight legs including the new one,
nine without it.

## Staged migration plan

### Stage 0 (this PR)

Docs, dependabot entry, dead-file removal. No import path, image, or
workflow leg changes.

### Stage 1: declare reality (low risk)

- Add `receipt-upload` to `receipt_agent/pyproject.toml` dependencies and
  `receipt-agent` to `receipt_upload/pyproject.toml` dependencies, or break
  the cycle by moving `receipt_agent.constants.CORE_LABELS` and
  `receipt_agent.utils.llm_factory` into `receipt_upload` (the two symbols
  `receipt_upload` needs at import time). Breaking the cycle is preferred;
  declaring it is the fallback.
- Drop `receipt_dynamo_stream` from the resegment image: Dockerfile lines
  6, 12, 16; `infrastructure.py:196`; `lambda_image_import_check.py:68`.
  This triggers one rebuild of the resegment Lambda.
- Remove `structlog` from `receipt_places` dependencies and from the
  explicit pip lines at `main.yml:159,175,191,287`.
- Remove `/infra/upload_images/container_ocr` from dependabot, or give it a
  real manifest.
- Add a `receipt_layoutlm` 3.13 leg (its runtime carve-out) that installs torch from the CPU index
  and runs `tests/unit`. Cost: one more leg, roughly two to three minutes
  for the torch wheel.

Risk: pip resolution changes only affect local installs and the
`lambda-images.yml` import check; CI installs are `--no-deps`.

### Stage 2: fold `receipt_places` into `receipt_agent` (medium risk)

Move `receipt_places/receipt_places/*` to
`receipt_agent/receipt_agent/places/` and leave a one-release shim package
`receipt_places` whose `__init__` re-exports from the new location, so the
four lazy import sites and three scripts keep working while they are
updated.

Touch points:

- Six Dockerfiles copy and install `receipt_places` (fix_place, mcp_server,
  merge, qa_agent, resegment, container_ocr). Each `infrastructure.py`
  lists it in `source_paths` (for example
  `infra/resegment_receipt_lambda/infrastructure.py:194-199`).
- `main.yml:101,114,156-161,170,185,280`; `native-tracing-quality.yml:61`.
- `.github/dependabot.yml` directory entry.
- `scripts/backfill_receipt_place.py`, `scripts/receipt_mcp_server.py`,
  `scripts/verify_deployment_readiness.py`.

Risk: every image that ships `receipt_places` rebuilds once. The
`build_context_path="."` plus `source_paths` hashing means the rebuild is
automatic but also unavoidable. `receipt_upload` would then depend on
`receipt_agent` for Places, which is already true in practice (Finding 2).

### Stage 3: fold `receipt_dynamo_stream` into `receipt_dynamo` (medium risk)

Move to `receipt_dynamo/receipt_dynamo/stream/` with a shim as above.

Touch points:

- `infra/components/lambda_layer.py:1401-1407` (drop the second layer),
  `infra/receipt_update_queues/__init__.py:34,467`,
  `infra/receipt_update_queues/lambdas/stream_processor.py` imports.
- `tests/test_merge_receipt_lambda.py:35`.
- `main.yml:100,112,151-155,168,183,279`; `native-tracing-quality.yml:60`.
- Dependabot entry; the resegment references if Stage 1 did not already
  remove them.

Risk: the `receipt-dynamo` Lambda layer grows by the stream code, and its
content hash changes, redeploying the stream processor. The layer build
script installs one `package_dir` per layer, so the merged package must
install cleanly as a single distribution.

### Stage 4 (not recommended now): merge `receipt_agent` and `receipt_upload`

They are mutually dependent, install identically in CI, and together are
67k lines. Merging them would delete two matrix legs but grow the qa_agent
image (agent without upload today) and the resegment image (upload without
agent today) by the other half. Break the cycle in Stage 1 first, then
revisit once image sizes are measured.

### `receipt_logo` and `receipt_nutrition`

- `receipt_logo`: owner decision. If kept, move it to
  `tools/receipt-logo/` and update `scripts/receipt_logo_mcp_server.py:8`,
  `tools/glyph-studio/server/mcp.mjs:797`, `AGENTS.md:18,32`, and the
  dependabot entry. If dropped, the glyph MCP server already degrades
  gracefully when the directory is missing (`mcp.mjs:813`).
- `receipt_nutrition`: keep separate. It has no deployment surface yet;
  folding it into `receipt_upload` or `receipt_dynamo` before it ships
  would only move the target.

## Risks common to every stage

- **Import paths inside deployed Lambdas.** Handlers import
  `receipt_places`, `receipt_dynamo_stream`, `receipt_upload.combine`,
  `receipt_agent.lifecycle.receipt_manager` and friends by name
  (`scripts/lambda_image_import_check.py:29-100` is the authoritative
  list). Shim packages must stay until every image has rebuilt on the new
  path, and the import-check list must be updated in the same PR.
- **Docker build contexts.** Every image builds from the repo root with a
  `source_paths` hash. Renaming or removing a directory listed there
  changes the hash and forces a rebuild; forgetting to update the list
  silently stops rebuilds for the moved code.
- **`pip install -e` chains in `main.yml`.** `receipt_dynamo` is installed
  with dependencies first; every other local package is `--no-deps` with
  external dependencies spelled out by hand (`main.yml:167-176`,
  `:182-195`, `:277-290`). Any moved external dependency must be added to
  those lists or the leg fails at import time, not at install time.
- **Editable-install ordering.** Shim packages that re-export from a new
  home must be installed after the home, or `pip install -e` of the shim
  fails to import during hatchling metadata generation.
- **`receipt_layoutlm` pins.** `torch>=2.6.0,<=2.7.0` and
  `coremltools==9.0` are deliberate (`receipt_layoutlm/pyproject.toml`);
  the package must stay a separate distribution so those pins never enter
  the Lambda image resolves.
