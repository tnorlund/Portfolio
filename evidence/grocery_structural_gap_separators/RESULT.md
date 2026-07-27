# Grocery structural-gap separator evidence

The renderer now recognizes generic POS section transitions in whitespace that
the source OCR preserved, and prints dash glyphs at the measured gap midpoint.
It does not add line pitch or shift any OCR row.

## Metric proof

| Receipt | Before | After |
|---|---|---|
| Vons `678a7c94-4948-4ebf-b8e9-9a17c13051ec#2` | FAIL: real 3, synth 0 | PASS: 3/3 matched; y deltas 0.0015, 0.0044, 0.0000 |
| Trader Joe's `4c262079-4fec-4724-a8e1-2886f38ea454#1` | FAIL: real 1, synth 0 | PASS: 1/1 matched; y delta 0.0010 |

Both sides of this pair were re-derived on 2026-07-27 after the rebase.
`before/` is measured at `1d0dd32f5` (main without this branch); `after/` is
measured at `63cc32906`, this branch's renderer commit. The two runs share a
worktree, atlas (`atlas_hash`), and inputs (`inputs_hash`), so the renderer
change is the only variable.

Every check section is byte-identical before and after except `separators`:
`columns`, `style`, `tokens`, `graphics`, `logo`, `arithmetic`, and
`coverage_gaps` all match exactly. Trader Joe's `overall` moves FAIL ->
PASS_WITH_GAPS purely because its separator lane clears. Vons stays FAIL because
its `style` lane owns a pre-existing red metric this branch does not touch.

`graphics` was measured with the Swift `receipt-ocr` barcode detector built and
present, so the barcode inventory is genuinely compared on both sides rather
than skipped.

## Guard proof

- Focused renderer tests: `test_receipt_gap_separators.py` (this branch),
  `test_receipt_renderer_separators.py` (#1258 / #1243), and
  `tests/test_render_measured_separators.py` — 10 passed together, so the
  structural-gap source coexists with the measured-inventory and
  inferred-policy sources rather than replacing them.
- Full `receipt_agent` suite: 340 passed, 22 skipped. Five `test_llm_factory.py`
  failures seen locally come from a missing `langchain_openai` in the local venv
  and reproduce identically on unmodified `origin/main`.
- `synthesis_loop/corpus_regression_gate.py check --json`: `ok: true`, zero
  findings, re-run on the rebased head.

`render_regression_guard.py check` is deliberately NOT claimed here. On this
machine that guard reports all four committed pins as CHANGED on unmodified
`origin/main`, so its baseline is stale independently of this branch. #1260
recaptures it; this branch cannot be measured against it until that lands.

## Rebase note (2026-07-27)

Rebased onto `origin/main` after #1258 landed measured separator inventories in
the same renderer region. Conflicts were resolved by keeping every separator
source side by side. The structural-gap source is now gated on
`config.separators is None`, matching the rule #1258 established for the other
heuristic sources: a measured merchant-truth inventory wins over any heuristic.

All full-fidelity and gate runs used read-only dev table `ReceiptsTable-dc5be22`;
no gate record was written.
