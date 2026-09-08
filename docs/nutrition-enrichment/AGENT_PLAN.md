# Nutrition implementation and evaluation plan

Revised 2026-09-08 against main dd737e154. Contract: [SPEC.md](SPEC.md).
Evidence ledger: [EVALUATION.md](EVALUATION.md).

## Working rules

- Branch codex/nutrition-enrichment; no main commits or force pushes.
- Keep milestone diffs small. Follow codex-diff-review over all new/staged/
  unstaged files, resolve HIGH/MEDIUM findings, then commit and push.
- Root/nested AGENTS.md apply. No prod evaluation or owner-only commands.
  Dev deploy/live tests need explicit authorization, after a concrete packet.
- Preserve decoders, Swift parity, existing indexes, observed merchant truth.
- One implementation writer; bounded read-only independent reviews.
- If stacked: parent merges use merge commits; only leaves squash. Run local
  checks for child PRs that lack the main-target CI matrix.
- Do not commit screenshots/logs/bulk catalogs/scratch scripts. Curated
  publishable fixture evidence and scorecard summaries are intentional tests
  and documentation, not raw execution logs.

## Evaluation tiers

| Tier | Proves | Does not prove |
|---|---|---|
| Pure/unit | schema, dimensions, abstention, contracts | provider/model quality |
| Moto | conditions, publication, corrections, stream | actual AWS IAM/runtime |
| Source-backed offline | pinned imported facts and labelled queries | untested live-provider behavior |
| Real model | held-out accuracy/token cost/latency | deployed pipeline |
| Authorized dev | actual upload/refine/correction/read/cache | prod rollout |
| Browser/public | rendered behavior and export policy | consumption or dietary outcomes |

## P — corrected contract

Deliver SPEC, this plan, research amendments, and evaluation ledger.
Review units, correction invalidation, alias scope, deletion/concurrency,
real model gates, public policy, and rollout boundaries.

## A — evidence models, quantity math, honest harness

Create receipt_nutrition typed models, source-basis validation, Decimal
costing, narrow raw-text unit evidence adapter, and validated manual inputs.
Never modify decoded names/prices or guess missing package count.
Create scripts/nutrition_harness/evaluate.py with contract/offline/live modes
and fixture validation separating synthetic, source-backed, and held-out
examples. Motivating products stay unverified until label evidence exists.

Gate: ≥30 independent arithmetic/unknown/conflict cases; supported costs
cent-exact; missing nutrients never zero-filled; invalid fixture terminates
nonzero; contract mode cannot pass a real-quality gate. Add package CI/install
wiring immediately.

Commands once implemented:

```sh
.venv/bin/python -m pytest receipt_nutrition/tests
.venv/bin/python scripts/nutrition_harness/evaluate.py --mode contract
.venv/bin/python -m black --check --line-length=79 receipt_nutrition
.venv/bin/python -m isort --check-only --profile=black --line-length=79 receipt_nutrition
```

## B — conditional persistence and atomic publication

Add FoodProduct immutable revisions, conditional ProductAlias,
generation-scoped ReceiptLineNutrition and summary/control manifest.
Follow receipt-dynamo-integration-tests.

Gate: CRUD/error mappings/pagination; immutable facts; expected-revision
conflicts; confirmations protected from model races; active-generation
reverse lookups with parent checks; lease fencing/parent existence; staging
failure and cleanup-between-verification/publication; read/cleanup retry;
empty receipt/idempotency. Every DynamoDB call stays in receipt_dynamo.

```sh
.venv/bin/python -m pytest receipt_dynamo/tests/unit -k nutrition
.venv/bin/python -m pytest receipt_dynamo/tests/integration -k nutrition
```

## C — sources, retrieval, and independently verified pilot

One adapter produces full/mini catalogs from pinned FDC data, optional
private OFF and complete TJ/manual labels. Add manifest hashes, SQLite,
deterministic n-grams, national brands, and package distractors.
Configurable candidate-only model interface uses budget/time limits,
versioned prompts/replay and pending review by default.

Gate: 20–30 recurring products with independent evidence or explicit pending
status; no truth injection after failed retrieval. Expand to ≥150 lines/
≥6 merchants before general quality claims. Held-out real-import recall@10
≥0.85. Real LLM acceptance requires ≥0.98 precision over ≥100 accepted
held-out cases. If truth/credentials are unavailable, ship runnable evaluation
with automatic acceptance off and the real gate pending.

```sh
.venv/bin/python scripts/nutrition_harness/evaluate.py --mode offline
.venv/bin/python scripts/nutrition_harness/evaluate.py --mode live --max-calls 150
```

## D — private enrichment, corrections, MCP pilot

Connect matching/costing/DAL with shared orchestration and thin adapters in
both existing MCP servers. Include read/search/manual import/worklist/
confirm/reject/spend, dev write guards, and expected alias revisions.
Propagate corrections through past observations of every status.

Gate: motivating purchases supported or honestly unknown/estimated; one
correction updates multiple receipts and summary versions; stale model
writes cannot undo confirmation; worklist pagination and evidence tests.
Complete a private pilot before UI expansion.

## E — stream, queues, packaging, bounded repair

Route line INSERT/MODIFY/REMOVE, parent REMOVE, and alias changes through
existing fan-out. Parent-only deletion requests cache invalidation even when
the nutrition manifest is already gone. Periodic full cache replacement
removes all contributions from absent parents/manifests.
Guard product/nutrition writes from loops. Add queue/DLQ, consumer/IAM,
partial failures, metrics, fan-out pagination/checkpoints, and repair.
Include receipt_nutrition in every relevant layer/import/source-hash path.

Gate: existing stream regressions plus duplicates/reordering, simultaneous
workers, lease expiry, cleanup races, zero/delete-only lines, parent-only
deletion and merge cache removal,
interrupted fan-out/staging and repair. Actual ARM64 artifact import/size/
memory/startup measured separately from mocks.

## F — dev backfill and end-to-end

Implement dev-only dry-run-default backfill; explicit apply limit, resume,
version checks and coverage. Prepare reviewed deployment packet with
expected resource/IAM changes and rollback/disable steps.
Once explicitly authorized, verify AWS account 681647709217 and run from infra:

```sh
pulumi preview --stack tnorlund/portfolio/dev
pulumi up --stack tnorlund/portfolio/dev
```

Refuse unrelated deletes/replacements and do not interrupt shared updates.
Gate: real upload/refine/correct/delete → current read/cache within five
minutes after source quiescence; fixed-denominator coverage and correctness;
identical rerun changes zero business content.

## G — public projection, cache/route, visualization

One public projection precedes receipt/monthly serialization. Exclude
OFF/private facts and recompute totals from permitted rows. Define cache
producer triggers, repair schedule, version/age, route invalidation, and
correction propagation. Extend the existing line-item visualization story.

Gate: excluded facts absent from output bytes/aggregates; 0/some/all plus
unknown/estimated states; API tests; frontend lint/type-check/tests; browser
QA/screenshots per pr-screenshots; owner visual review before merge.
Prepare draft PR; do not merge/publish without required authorization.

## Reporting

Update EVALUATION.md at every milestone: base/commit, fixture/catalog hashes,
commands, counts, metrics with denominators, evidence tier, review findings
and resolutions, limitations. Status is NOT RUN, PASS, FAIL, or PENDING
EXTERNAL. Offline progress is not completion of the full plan.
