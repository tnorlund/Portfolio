# Claude Code Context

## Current Focus

Build out synthetic receipt augmentation for LayoutLM training. The target state
is a local-first system that uses existing receipt structure to generate fewer,
higher-fidelity train-only examples across merchants, validates them with
merchant contracts and geometry/arithmetic gates, records accepted mix metrics
to Dynamo, and surfaces the evidence in the frontend training metrics view.
Sprouts can be used as the first dense merchant case, but synthesis, gates, and
audits must stay merchant-generic.

## Cost Constraint

Default to no-spend local work:

```bash
export RECEIPT_AGENT_DISABLE_PAID_LLM=1
export DISABLE_PAID_LLM=1
```

Do not launch SageMaker jobs, Step Functions, OpenRouter/OpenAI calls, Chroma
cloud syncs, or other paid infrastructure unless the user explicitly approves
that action in the current conversation.
OpenAI latest-model metadata was verified against official OpenAI developer
docs on 2026-06-23; recheck before changing model defaults.

## Main Files

- `receipt_agent/receipt_agent/agents/label_evaluator/merchant_synthesis.py`
  generic merchant synthesis from observed items, categories, geometry, totals,
  mutable DATE/TIME fields, and tax-rate evidence.
- `receipt_agent/receipt_agent/agents/label_evaluator/sprouts_parameterization.py`
  Sprouts-specific receipt parameterization and arithmetic-safe mutations.
- `scripts/verify_synthetic_replay.py` local artifact, merchant contract,
  bundle, preflight, and synthesis quality report builder.
  Its bundle `selection.accepted_candidate_examples` is the first place to
  inspect why specific candidates were accepted: rank, merchant, operation,
  structure similarity, candidate quality, preview lines, and selection reason.
- `receipt_layoutlm/receipt_layoutlm/data_loader.py` final LayoutLM quality gate
  for train-only synthetic examples.
- `receipt_layoutlm/receipt_layoutlm/trainer.py` Dynamo metrics writer for the
  accepted synthetic mix.
- `receipt_agent/receipt_agent/agents/label_evaluator/augmentation_audit.py`
  baseline-vs-augmented audit that uses accepted candidate quality from Dynamo
  as a promotion guardrail.
- `infra/routes/job_training_metrics/handler/index.py`,
  `portfolio/types/api.ts`, and
  `portfolio/components/ui/Figures/TrainingMetricsAnimation/index.tsx` API and
  UI surface for synthesis evidence.

## Expected Invariants

- Synthetic examples never enter validation.
- Add-item candidates must use observed same-merchant catalog evidence from
  another receipt and must be inserted into a category present on the base
  receipt.
- Non-taxable add/remove mutations must reconcile subtotal/grand-total changes
  while leaving tax unchanged.
- Tax-changing mutations remain blocked even when stable tax-rate evidence is
  observed; the tax contract is evidence, not permission to train taxable edits.
- DATE/TIME replacement is allowed only for stable format, stable geometry, and
  multiple observed values.
- Geometry quality is checked by aggregate structure similarity and component
  thresholds such as price column, row spacing, category order/match, and token
  count.
- Frontend metrics should show enough evidence to answer why a candidate is
  considered accurate, including grounding, contracts, geometry, preview shape,
  arithmetic, and whether the run was local-only or LLM-assisted.

## Local No-Spend Data Path

When real receipt JSON is available, prefer this all-merchant local validation
loop before any cloud/API run. The local loader accepts grouped merchant
exports, ungrouped receipt/line exports, and exports with separate top-level
`words` plus `word_labels`; it attaches matching records, normalizes bounding
boxes, ignores invalid labels, and infers merchant names from `MERCHANT_NAME`
labels before using header text as a fallback.

```bash
export RECEIPT_AGENT_DISABLE_PAID_LLM=1
export DISABLE_PAID_LLM=1

python3.12 scripts/verify_synthetic_replay.py local-pipeline \
  --receipt-dir ./local_receipt_json \
  --artifact-output-dir ./.tmp/synthetic-artifacts \
  --bundle-output ./.tmp/synthetic-bundle.json \
  --min-grounded-candidate-share 0.4 \
  --max-per-merchant 5 \
  --max-per-merchant-operation 2

python3.12 scripts/verify_synthetic_replay.py report \
  --bundle-file ./.tmp/synthetic-bundle.json
```

Do not infer quality from candidate count alone. Check accepted candidate
examples, merchant contracts, rejection reasons, structure components,
arithmetic reconciliation, `source_receipt_quality`, `accepted_mix_balance`, and
the real-only validation policy. The balance object is the local
overtraining/concentration signal: top merchant share, top operation share,
entropy, risk level, and reasons. The synthetic augmentation audit treats
medium/high balance risk as a promotion hold even when target confusions and F1
improve. If `source_receipt_quality` is blocked or limited, fix the exported
receipt/word/label data before tuning synthesis. A blocked
`source_receipt_quality` now marks that merchant's synthesis contract blocked,
so its generated candidates cannot enter the LayoutLM training bundle even if
older readiness fields look ready. The synthesis quality report and
training-metrics API preserve the source-quality status, label counts, and
operation blockers for downstream review. Candidate quality also records the
nearest-real structure gate; inspect
`structure_similarity.nearest_real_receipt_key`, `shape_deltas`, and
`match_summary.shape_checks` before trusting a candidate. The structure evidence
also includes `real_baseline_comparison`, which compares the synthetic score
with the merchant's real-to-real receipt variation. `high_fidelity` requires
the same minimum structure similarity and component thresholds that the LayoutLM
loader uses.

## Focused Verification

```bash
python3.12 -m pytest \
  receipt_agent/tests/test_merchant_synthesis.py \
  receipt_agent/tests/test_sprouts_parameterization.py \
  receipt_agent/tests/test_synthetic_augmentation_audit.py \
  receipt_layoutlm/tests/unit/test_synthetic_training_examples.py \
  tests/test_verify_synthetic_replay_preflight.py \
  tests/test_job_training_metrics_synthesis.py \
  tests/test_unified_pattern_builder_confusion_targets.py -q

(cd portfolio && npm run type-check -- --pretty false)
(cd portfolio && npx eslint components/ui/Figures/TrainingMetricsAnimation/index.tsx)
```
