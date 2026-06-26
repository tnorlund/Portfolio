# Branch charter — `feat/synthesis-orchestration`

> One of three parallel branches off `integration/synthesis-next` (= PR #1003
> tax-config work + PR #994 font analysis, merged). You are a fresh session.

## Goal
Automate the whole synthetic-receipt pipeline as an **agent / Workflow + MCP
harness**: research → synthesize → render → validate → bundle → metrics. Start as
a skeleton that wraps TODAY's deterministic pipeline so it runs immediately, then
make it agent-driven and wire in the other two branches behind feature flags.

## Organizing principle (do not violate)
You are the INTEGRATION layer. You do NOT edit synthesis internals or gates — you
CALL them. The deterministic gates remain the source of truth; the agent decides
*what to run and when*, not *what passes*.

## You own (file boundaries)
- NEW orchestration entrypoint + glue, e.g.
  `scripts/run_synthesis_pipeline.py` and/or a Workflow script.
- A per-run report aggregator (per merchant: research confidence, accepted
  synthetic count by op, taxable edits + rates, render artifacts, mix-balance,
  rejection reasons).

## Do NOT touch (other branches / sacred gates)
- `merchant_synthesis.py` internals, the tax-config gate, the loader gates,
  rendering internals, research internals. Consume their public outputs.

## Interfaces you build against (stub until branches 1 & 2 land)
- **Branch 1** (`feat/merchant-intelligence-agents`): reads
  `merchant_intelligence/<slug>.json` (tax/catalog/details + confidence). Until
  it exists, use the current hardcoded `merchant_tax_config` + `online_catalogs`.
- **Branch 2** (`feat/receipt-font-render`): a renderer
  `render(receipt_dict, font_profile) -> PNG` and a font-profile extractor. Until
  it exists, skip the render step behind a `--render` flag.

## The deterministic backbone that already works (wrap this first)
1. Export receipts per merchant via **ReceiptPlace** (ReceiptMetadata is
   DEPRECATED — strict validated_by enum throws on blank values):
   `get_receipt_places_by_merchant(name)` → image ids → `export_image(table,id,out)`.
2. `scripts/verify_synthetic_replay.py local-pipeline ... ` → artifacts + bundle.
3. `scripts/verify_synthetic_replay.py report --bundle-file ...` → summary.
Proven result on the 4 validated merchants: 5 Vons taxable add_line_item
candidates accepted at 7.25%; mix-balance low risk. Reproduce that through your
orchestrator as the acceptance test.

## First milestones
1. **Skeleton orchestrator**: one entrypoint, given a merchant list, runs
   export → local-pipeline → report and prints a consolidated per-merchant
   summary. Must reproduce the 5 Vons taxable adds.
2. **Agent-driven**: an MCP-connected agent (or `Workflow` script) that picks
   merchants, handles per-merchant failures, retries, and loops until coverage
   targets (e.g. N accepted synthetic rows / merchant, mix-balance under
   threshold). Use the `Workflow` tool for deterministic fan-out where possible.
3. **Wire branch 1**: prefer `merchant_intelligence/<slug>.json` when present.
4. **Wire branch 2**: add a `--render` step that emits QA images per accepted
   candidate.
5. **Metrics**: surface the run (accepted mix, taxable edits, balance) — hook the
   existing Dynamo training-metrics path / training-metrics UI where it fits.

## Run / verify
    export RECEIPT_AGENT_DISABLE_PAID_LLM=1 DISABLE_PAID_LLM=1   # backbone is local
    python3.12 scripts/verify_synthetic_replay.py local-pipeline \
      --receipt-dir <dir> --artifact-output-dir .tmp/art --bundle-output .tmp/bundle.json \
      --min-grounded-candidate-share 0.0 --max-candidates 80 \
      --max-per-merchant 100 --max-per-merchant-operation 60
    python3.12 scripts/verify_synthetic_replay.py report --bundle-file .tmp/bundle.json
Review with `git diff | codex exec --skip-git-repo-check "<prompt>"`.

## Done when
A single command runs the full loop for a merchant list and emits a per-merchant
report reproducing the validated result (5 Vons taxable adds at 7.25%), is
agent-driven with failure handling, and has flagged hooks for research (1) and
render (2). No synthesis/gate internals edited; codex-reviewed.
