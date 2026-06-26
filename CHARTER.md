# Branch charter — `feat/merchant-intelligence-agents`

> One of three parallel branches off `integration/synthesis-next` (= PR #1003
> tax-config work + PR #994 font analysis, merged). Read this first; you are a
> fresh session with no memory of how this branch was scoped.

## Goal
Replace the **hand-validated** merchant tax / line-item / merchant-detail data
with an **agent + MCP + web-research pipeline** that regenerates that data on
demand, with provenance and confidence — still policed by the existing
**deterministic** safety gates.

Today the validated tax config
(`receipt_agent/.../label_evaluator/merchant_tax_config.py`) came from a one-off
workflow that read receipts and got hand-transcribed into a Python dict. That
doesn't scale to 200+ merchants and goes stale. Make it a repeatable,
multi-source research run.

## Organizing principle (do not violate)
**Agents PRODUCE artifacts. Deterministic gates VALIDATE and CONSUME them.**
The LayoutLM-training safety guarantees live in the deterministic gates — keep
them deterministic and testable. Research can be as smart as you like, but its
output is *data*; a low-confidence or contradictory result must never relax a
gate. The gate is the final arbiter.

## You own (file boundaries)
- NEW `receipt_agent/receipt_agent/agents/label_evaluator/merchant_research/`
- The DATA artifacts it emits: a new `merchant_intelligence/<slug>.json` set
- A thin loader hook so `merchant_tax_config` / `online_catalogs/*.json` can
  source from artifacts (keep the current hardcoded values as a fallback).

## Do NOT touch (other branches / sacred gates)
- Gate logic in `merchant_synthesis.py`: `_taxable_edit_rate_for_receipt`,
  `_consistent_validated_edit_rate`, `_receipt_effective_tax_rate`,
  `_apply_taxable_delta`, `_summarize_tax_policy`. Feed them better data.
- Loader gates in `receipt_layoutlm/.../data_loader.py`.
- Geometry/spacing/rendering (→ `feat/receipt-font-render`).
- Orchestration entrypoint (→ `feat/synthesis-orchestration`).

## Research is multi-source (decision: MCP + web + real receipts, robust)
Web search AND paid reasoning allowed for research ONLY (do NOT launch
SageMaker / Step Functions / training). Per merchant, triangulate:
1. **Receipts (ground truth)** via MCP `receipt-tools`
   `get_receipts_by_merchant` → receipt details/words. Derive taxable flags,
   effective rate (tax/subtotal from summary anchors), catalog, address/store id.
2. **Web search**: the jurisdiction's real sales-tax rate for the store's
   city/county + period; corroborate merchant facts.
3. **Places cache** (`receipt_places` Dynamo, ReceiptsTable-dc5be22, us-east-1).
Cross-check sources; emit **confidence** + **provenance** per field. The tax
artifact MUST distinguish single-jurisdiction (one validated rate) from
multi-jurisdiction (per-store rates) — the gate keys on that.

## Artifact schema (lock it; branch 3 reads it)
`merchant_intelligence/<slug>.json` ≈
    { "merchant","slug",
      "tax": { "taxable_flag","nontaxable_flags":[],"jurisdiction",
               "validated_rate","jurisdiction_rates":[],
               "can_support_taxable_edits","confidence","provenance":[] },
      "catalog": [ {"name","price","taxable","source"} ],
      "details": { "address","store_id","category" },
      "generated_at","sources":{} }

## First milestones
1. Lock the schema + a `merchant_research` package skeleton.
2. Per-merchant research agent (MCP receipts → web → Places → cross-check).
3. Emit artifacts for the 8 known merchants (Vons, Sprouts, Amazon Fresh,
   Target, Costco, Home Depot, Gelson's, Smith's); confirm tax artifacts MATCH
   the current hand-validated config (regression guard).
4. Wire the loader so `merchant_tax_config` sources from artifacts (hardcoded
   fallback kept); re-run the local pipeline; confirm the 4 validated merchants
   gate identically (5 Vons taxable adds at 7.25%).
5. Extend to NEW merchants the hand path never covered.

## Run / verify
    # Export receipts via ReceiptPlace (ReceiptMetadata is DEPRECATED — its
    # strict validated_by enum throws on blank values):
    #   get_receipt_places_by_merchant(name) -> image ids -> export_image(table,id,out)
    python3.12 scripts/verify_synthetic_replay.py local-pipeline \
      --receipt-dir <dir> --artifact-output-dir .tmp/art --bundle-output .tmp/bundle.json \
      --min-grounded-candidate-share 0.0 --max-candidates 80 \
      --max-per-merchant 100 --max-per-merchant-operation 60
    # Regression gate (keep green):
    python3.12 -m pytest receipt_agent/tests/test_merchant_tax_config.py \
      receipt_agent/tests/test_merchant_synthesis.py -q
Review as you go: `git diff | codex exec --skip-git-repo-check "<prompt>"`.

## Done when
Artifacts regenerate the current 8-merchant tax/catalog data (matching the
validated config), the pipeline still yields 5 Vons taxable adds at 7.25%, and a
few NEW merchants get researched end-to-end with confidence/provenance. Gates
unchanged; tests green; codex-reviewed.

---

## Addendum — receipt taxonomy & service receipts (M6–M8)

> Added after the milestones above. Do these AFTER the human-approval review gate
> ships. Review-first cadence (codex on each meaningful change) still applies.
> **You produce the taxonomy DATA; synthesis/orchestration CONSUME it.** Stay in
> your file boundaries: emit `merchant_intelligence/<slug>.json` structure data
> and use the contract/loader hook you own — do NOT edit `merchant_synthesis.py`
> gate logic or `data_loader.py`.

### Goal
Make synthesis work for ANY merchant we've seen — including **service receipts**
(no line-item grid: salons, car washes, parking, medical, mail/shipping,
subscriptions) — by deriving each merchant's structural ARCHETYPE from its real
receipts and emitting it as intelligence.

### M6 — Structural fingerprint + archetype clustering
Define a per-receipt structure FINGERPRINT (regions present + arrangement:
header, line-item grid vs single service line, totals block, tip line, payment;
row/column geometry signature; label-set profile — reuse the unified pattern
builder where it fits). Cluster ALL real receipts across merchants into a small
set of DATA-DRIVEN archetypes (e.g. `line_item_retail`, `service`,
`restaurant_tip`). Do NOT hard-code merchant→archetype.

### M7 — Service-receipt recognition + unblock
In each `merchant_intelligence/<slug>.json` add a `structure` block:
`{primary_archetype, archetype_mix, structure_type: line_item|service|hybrid,
applicable_operations, cluster_id, cluster_size, confidence, provenance}`.
For service, `applicable_operations = [replace_field, amount_mutation,
compose_header, hard_negative]` (NOT line-item ops). **Critically:** ensure the
source-quality contract recognizes a service receipt as VALID grounding — a
receipt with no line items must NOT be rejected for "missing line items"; it is
valid for service-type synthesis. Do this via the contract/loader hook you own,
NOT by editing `merchant_synthesis.py` gate logic or `data_loader.py`.
(Coordination: orchestration consumes `applicable_operations` to request the
right ops per merchant — flag when M7 lands.)

### M8 — Cross-merchant structural prior (borrow structure, not content)
For a THIN merchant (few receipts of its archetype), expose the archetype's
AGGREGATE structural prior (region layout, spacing signature, typical label
arrangement) — derived ONLY from receipts in the SAME cluster the merchant
belongs to — as grounded evidence in the artifact. **HARD RULE:** borrow
STRUCTURE/layout across merchants within a cluster; NEVER borrow CONTENT (items,
prices, merchant-specific text). Content stays within-merchant. The grounding is
cluster membership — the merchant's own receipts placed it there, so the
archetype's structural prior is a valid prior for it.

Apply the approval gate to STRUCTURE too: a new/low-confidence archetype
assignment → `needs_review` (don't auto-trust a structural prior you're unsure
of).

### Acceptance test
A service-type merchant from the corpus (e.g. Sparkling Image Car Wash or AIM
Mail Center) is classified `service`, is NOT blocked for "no line items," and
yields >=1 high-fidelity synthetic example via field/amount/header ops. A
line-item merchant (Vons) stays `line_item` and unaffected — the 5 taxable adds
at 7.25% still flow.
