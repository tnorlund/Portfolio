# X2 self-report

Branch `cards/X2-grok`. Implementation commit `8c98f56dc`. This file is
the judge completion signal.

No vector indexes were created, altered, or deleted. No `pulumi up`.
Previews only.

## Card

Delete the dormant `infra/embedding_step_functions/` batch tree (6 zip +
5 container Lambdas, 3 state machines, ECR, dashboard, second compaction
impl), plus `combine_receipts_step_functions` and the
`pattern_builder` Lambda. Drop each container Lambda's legacy-URN
`aliases` in the same change (SPEC §6 G). Fix the import-time breakages
this exposes (SPEC §6 A items 3, 5).

## 1. embedding_step_functions tree

**Addressed.** The directory is gone. `__main__.py` no longer constructs
`EmbeddingInfrastructure`. Legacy-URN aliases lived on the container
Lambdas at `components/lambda_functions.py` (`aliases=[self._legacy_container_lambda_urn(name)]`);
they were deleted with the resources, not left behind.

SPEC §6 A item 3 (module-scope `ChromaDBBuckets` import) and item 5
(eager `handlers/__init__.py` → `compaction` → `chromadb`) went with the
tree. Surviving bucket consumers now read
`shared_chromadb_buckets` (the value `EmbeddingInfrastructure` always
received from `__main__.py`). Historical stack outputs
`embedding_chromadb_bucket_name` / `_arn` still export that shared
bucket so existing `pulumi stack output` readers keep working.
`embedding_embed_all_v1_sf_arn` is dropped with the state machine.

**Verify**

```
test ! -d infra/embedding_step_functions
rg -n --glob '*.py' 'from embedding_step_functions|import embedding_step_functions'
# expect: no matches
rg -n '_legacy_container_lambda_urn' --glob '*.py'
# expect: no matches
```

## 2. combine_receipts_step_functions

**Addressed.** The directory is gone. `__main__.py` no longer constructs
`CombineReceiptsStepFunction`. Stack outputs `combine_receipts_sf_arn`
and `combine_receipts_batch_bucket_name` are dropped.

**Verify**

```
test ! -d infra/combine_receipts_step_functions
rg -n --glob '*.py' 'from combine_receipts_step_functions|import combine_receipts_step_functions'
# expect: no matches
```

## 3. pattern_builder Lambda

**Addressed.** Deleted
`infra/label_evaluator_step_functions/lambdas/unified_pattern_builder.py`
and `Dockerfile.unified_pattern_builder`. The evaluator Step Function
no longer invokes it: `HasReceipts` → `SkipPatterns` (empty
`pattern_results`) → `ProcessReceipts`. `unified_receipt_evaluator`
already loads patterns from S3 with `allow_missing=True`.

**Verify**

```
test ! -f infra/label_evaluator_step_functions/lambdas/unified_pattern_builder.py
rg -n 'unified_pattern_builder' infra/label_evaluator_step_functions --glob '*.py'
# expect: no matches
```

## 4. File fences

Did not touch `receipt_agent/`, `scripts/receipt_mcp_server.py`, or
`infra/mcp_server_lambda/` (E3). Did not touch X4's dead-code sweep
list except comments inside files this card already had to edit, and
the embedding-tree items that sat inside the deleted directory.

`infra/components/test_docker_package_contexts.py` still names the
deleted Dockerfiles. That file is on the X4 dead list, is not collected
by CI (`repository-tests` runs `tests/` + `scripts/test_*.py`;
`lambda-syntax` only `py_compile`s it), and was left for X4.

## Gates

### pulumi preview (both stacks, read-only)

```
cd infra
pulumi preview --stack tnorlund/portfolio/dev --non-interactive --suppress-progress
pulumi preview --stack tnorlund/portfolio/prod --non-interactive --suppress-progress
```

Both completed with exit 0. No import-time TypeError. Deletes include
`custom:embedding:Infrastructure`, `combine-receipts-*`, and
`label-evaluator-*-upb-img` (pattern_builder).

| Stack | Preview | Deletes (this card) |
|---|---|---|
| dev | https://app.pulumi.com/tnorlund/portfolio/dev/previews/4b43c62f-5611-4fde-8d5c-fc6eee17f3b5 | 154 resources (embedding tree + combine_receipts + upb-img) |
| prod | https://app.pulumi.com/tnorlund/portfolio/prod/previews/8b83a78b-82ae-4783-80ee-497e305e80ec | 154 resources (same set) |

Preview noise unrelated to this card: CodeBuild/Lambda hash updates on
unrelated images, and a NAT instance AMI replace already present as
drift (`nat-egress-*-nat`). Not deployed.

### CI-relevant suites

```
python3.13 -m py_compile $(find infra -name '*.py' -not -path '*/.venv*/*')
python -m pytest -q infra/tests/test_compaction_lock_config.py \
  infra/tests/test_email_receipt_inbox_handler.py --timeout=60
```

`py_compile` of every remaining `infra/**/*.py`: ok.
`lambda-syntax` infra tests: **14 passed**.

Label-evaluator definition check (no `pattern_builder` / `ComputeAllPatterns`
in the generated JSON): ok.

## Not verified locally

- Full GitHub Actions matrix (`python-tests` packages, repository-tests
  corpus, TypeScript, browser). This card does not change those packages.
- `pulumi up` (forbidden).
- Live label-evaluator execution after dropping pattern precompute
  (evaluator already treats missing pattern objects as optional).
