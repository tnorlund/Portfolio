# full_fidelity_eval — metric validation record (#1188 P1)

Standing rule (issue #1188): **a metric only counts once it has FAILED its
known historical defect and PASSED the real-vs-real null.** This file is the
in-repo record of that table; the unit-test leg lives in
`tests/test_full_fidelity_eval.py` (synthetic fixtures + the adversarial
fixtures from the PR #1192 independent review).

## Historical-defect table (live receipts, dev table `ReceiptsTable-dc5be22`)

| metric | defect state | verdict |
|---|---|---|
| columns | current Gelson's render, 223c03e2…#1 | **FAIL** — synth amount lane 22.2% off-lane outliers vs real 0% (real residual IQR 0.67px); amount↔flag lane gap 18.5px vs real 42.6px (Δ24.1 > 0.75 cell) |
| columns (null) | real-vs-real, 223c03e2 vs 5985d2dd (runs the production `metric_columns` with receipt B as the synth side) | **PASS** |
| tokens | address-eraser: 223c03e2 rendered at pre-fix `20a0bc7d4` with pre-hygiene labels (INVALID included, MERCHANT_NAME preferred) | **FAIL** — ink_recall 0.953; missing = exactly the six address tokens |
| graphics | The Stand v0 phantom-barcode sheet | **FAIL** — phantom code128 in synth footer |
| style | pre-stylemap Gelson's (HEAD render, `stylemap` dropped from typography) | **FAIL** — section_header bold 0/4 + underline 0/4 vs real 3/4 + 3/4 |
| arithmetic | stale 01:25 DT composer (`canonical_words` at `bbbed27c6`) on d10ba8bf…#1 | **FAIL** — 5 violations (Σ 4.00≠13.75; total 0.90≠tender 14.90; …) |
| arithmetic (confirm) | current DT composer (post "Recover all 11 pinned-receipt items") | **PASS** — 11 items, Σ 13.75, 13.75+1.15=14.90=tender |

Reproduction: `run` / `real-real` subcommands with the standard render env
(`DYNAMODB_TABLE_NAME`, `AWS_REGION`, `BITMATRIX_DIR`, PYTHONPATH incl.
receipt_agent/receipt_dynamo/receipt_upload). The eraser state needs a
detached worktree at `20a0bc7d4` and the label-state driver documented in
the campaign record (`render_eraser_state_20a0bc7d4.py`).

## Adversarial fixtures (PR #1192 independent review)

Each of these was a constructed case that previously PASSed; all are
permanent tests:

| fixture | metric | test |
|---|---|---|
| blank composed canvas | tokens | `test_adversarial_blank_composed_image_fails_tokens` |
| every token a 2×2 dot | tokens | `test_adversarial_dot_tokens_fail_ink_check` |
| linearly sheared lane (±10px) | columns | `test_adversarial_sheared_synth_lane_fails` |
| typed lane over decoy ink | columns | `test_adversarial_typed_lane_needs_typed_carriers` |
| uniformly doubled strokes | style | `test_adversarial_doubled_stroke_fails_style` |
| dash→solid separator substitute | separators | `test_adversarial_solid_separator_substitute_fails` |
| 1px-line logo of correct height | logo | `test_adversarial_thin_line_logo_fails` |
| whole-lane 2-cell translation | columns | `test_column_metric_fails_on_uniform_lane_shift` |
| synth lane ink absent | columns | `test_column_metric_fails_on_missing_synth_lane` |
| singleton total_line lane | columnscan | `test_singleton_total_line_lane_survives_pooling` |

## Documented non-gates (declined findings, with rationale)

- **Separator dash↔double kind**: a real dotted rule blurs between the two
  at eval resolution (the approved Gelson's matched pairs read
  dash-vs-double at identical y). Solid↔patterned IS gated.
- **Barcode payload equality**: reported per match (`payload_match`) but
  not gated — on the fallback render path the synth payload is a
  deterministic stand-in by design; only detected-entity barcodes carry
  the real payload through.
- **Fine absolute stroke weight / glyph size**: the existing scorecard
  gate (`h_ratio` / `wpc` / `density`) owns those; the style metric gates
  per-class agreement plus a gross uniform-re-weighting backstop
  (body stroke-per-height ratio ≤ 1.6×).
