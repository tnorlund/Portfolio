# Shared context — synthetic-receipt augmentation (read with your CHARTER.md)

This is the distilled context from the session that built the per-merchant tax
gate and set up these three branches. It exists because the CHARTER alone is a
cold brief; this is everything we learned so you don't rediscover it. All three
branches (`feat/merchant-intelligence-agents`, `feat/receipt-font-render`,
`feat/synthesis-orchestration`) share this; your CHARTER.md is your specific job.

## The goal
Synthesize accurate, train-only synthetic receipts across merchants for LayoutLM
training. Generate FEWER, HIGHER-fidelity examples grounded in real receipt
structure, validated by deterministic gates, never entering the validation split.

## Organizing principle (all branches)
**Agents PRODUCE artifacts; deterministic gates VALIDATE and CONSUME them.** The
LayoutLM-training safety guarantees live in the deterministic gates — keep them
deterministic + testable. Research/rendering/orchestration can be smart and
non-deterministic, but their output is DATA; a low-confidence or contradictory
result must never relax a gate. The gate is the final arbiter.

## What shipped this session (the foundation you build on)
A receipt-validated per-merchant tax config gating TAXABLE item edits (add/remove
a taxable line, which recomputes TAX):
`receipt_agent/.../label_evaluator/merchant_tax_config.py`.

8-merchant validation (from a workflow that READ each merchant's receipts):
- Cleared for taxable edits: **Vons T@7.25%, Sprouts T@7.25%, Amazon Fresh
  T@7.25%, Target T per-state (NV 8.375% / CA ~9.5%)**.
- Blocked: Costco + Home Depot (A flag, per-item OCR too sparse to trust),
  Gelson's (only 1 taxed receipt), Smith's (NV groceries exempt).
- Config unit MUST be (merchant × jurisdiction) — Target proves a global rate
  would corrupt NV vs CA.

The gate (`_taxable_edit_rate_for_receipt` in merchant_synthesis.py): merchant
cleared → for a SINGLE-jurisdiction merchant apply the config rate directly; for
MULTI-jurisdiction require a clean per-receipt observation that snaps to one
jurisdiction + no "blind" positive-tax receipt. Readiness mirrors this in
`_summarize_tax_policy`.

End-to-end proof: 5 Vons taxable add_line_item candidates land in the accepted
bundle at 7.25% (high-fidelity, loader-validated). THIS IS THE ACCEPTANCE TEST —
reproduce it through your work.

## Hard-won facts / gotchas (these cost hours; don't rediscover them)
- **ReceiptMetadata is DEPRECATED.** Its strict `validated_by` enum throws on
  blank values, so `get_receipt_metadatas_by_merchant` blows up. Use
  **ReceiptPlace**: `DynamoClient.get_receipt_places_by_merchant(name)` →
  (image_id, receipt_id) + `formatted_address` (jurisdiction!). The MCP
  receipt-tools use ReceiptPlace under the hood (that's why MCP works locally).
- **Export for the local pipeline**: `export_image(table, image_id, out_dir)`
  (`receipt_dynamo.data.export_image`) — Dynamo-only, emits the
  `receipt_words` + `receipt_word_labels` shape the local loader is tuned for.
  ReceiptPlace gives the image ids; export_image dumps them; point
  `local-pipeline --receipt-dir` at the dir.
- **Per-item taxable-flag OCR is SPARSE.** `tax / detected_taxable_subtotal` per
  receipt is garbage (observed Vons ~147%, Target ~19%) because most taxable
  items aren't flagged. The EFFECTIVE rate (tax / subtotal, from GRAND_TOTAL +
  TAX summary anchors) IS reliable. That's why single-jurisdiction merchants use
  the config rate, not a re-derived one.
- **Real Vons/Sprouts receipts have ZERO SUBTOTAL labels.** `_apply_taxable_delta`
  reconstructs subtotal = GRAND_TOTAL − TAX.
- **Taxable edits rank BELOW non-taxable adds**, so they need adequate
  `--max-candidates` (80) + per-op cap to surface; at defaults they're crowded
  out. (A prioritization tweak is a known future improvement.)
- Dev table `ReceiptsTable-dc5be22`, region `us-east-1`. The inference-viz
  worktree's gitignored `.env` holds `RECEIPT_AGENT_GOOGLE_PLACES_API_KEY`,
  `DYNAMO_TABLE_NAME`, `AWS_REGION`. NEVER commit secrets; never put the key on
  argv/process list.

## Invariants (sacred — do not weaken)
- Synthetic examples never enter validation (real-only validation).
- Add-item candidates use observed same-merchant catalog evidence + a category
  present on the base receipt.
- Non-taxable add/remove reconciles subtotal/grand, freezes TAX.
- Tax-changing mutations require a receipt-validated rate (the config gate).
- DATE/TIME replacement only for stable format + geometry + multiple values.
- Geometry quality = aggregate structure similarity + component thresholds
  (price column, row spacing, category order/match, token count).

## YOU MUST self-review with codex along the way (non-negotiable)
We hardened the tax gate through SIX rounds of codex review; every round caught a
real issue (loose matching, rate tolerance, batch-median hole, blind-receipt
hole, jurisdiction safety, over-removal). Do the same:
- After each meaningful change, run:
  `git diff | codex exec --skip-git-repo-check "<focused review prompt naming the
   change, the invariant it must hold, and asking only for real HIGH/MEDIUM issues>"`
- Address every HIGH/MEDIUM (or justify why not), then RE-RUN codex to confirm
  it's resolved. Iterate until codex returns no unresolved HIGH/MEDIUM.
- Codex calls run ~50–190k tokens each; that's expected and worth it.
- Be honest in comments — if a guard is conservative/heuristic, say so; codex
  will (correctly) flag an overclaim.

## Run / verify (the proven local loop)
    # Export 4 validated merchants via ReceiptPlace -> export_image -> a dir, then:
    python3.12 scripts/verify_synthetic_replay.py local-pipeline \
      --receipt-dir <dir> --artifact-output-dir .tmp/art --bundle-output .tmp/bundle.json \
      --min-grounded-candidate-share 0.0 --max-candidates 80 \
      --max-per-merchant 100 --max-per-merchant-operation 60
    python3.12 scripts/verify_synthetic_replay.py report --bundle-file .tmp/bundle.json
    # Focused regression suite (keep green):
    python3.12 -m pytest \
      receipt_agent/tests/test_merchant_tax_config.py \
      receipt_agent/tests/test_merchant_synthesis.py \
      receipt_agent/tests/test_sprouts_parameterization.py \
      receipt_agent/tests/test_synthetic_augmentation_audit.py \
      receipt_layoutlm/tests/unit/test_synthetic_training_examples.py \
      tests/test_verify_synthetic_replay_preflight.py \
      tests/test_job_training_metrics_synthesis.py \
      tests/test_unified_pattern_builder_confusion_targets.py -q

## Key files
- Gate + synthesis: `receipt_agent/.../label_evaluator/merchant_synthesis.py`
- Tax config: `receipt_agent/.../label_evaluator/merchant_tax_config.py`
- Loader gates (sacred): `receipt_layoutlm/receipt_layoutlm/data_loader.py`
- Local pipeline + report: `scripts/verify_synthetic_replay.py`
- Font analysis (from #994): `receipt_upload/receipt_upload/font_analysis.py`,
  `font_letter_analysis.py` (import: `from receipt_upload import font_analysis`)
- Store/Places + catalogs: `receipt_agent/.../label_evaluator/store_profile.py`,
  `.../online_catalogs/*.json`

## Branch boundaries (so the three don't collide)
- merchant-intelligence-agents: NEW `merchant_research/` + intelligence artifacts;
  feeds config/catalog DATA. Don't touch gate logic, rendering, orchestration.
- receipt-font-render: NEW renderer + font→geometry hooks (spacing helpers only).
  Don't touch tax-gate logic or orchestration.
- synthesis-orchestration: NEW entrypoint/glue; CALLS everything, edits nothing
  internal.
