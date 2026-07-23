# Grocery structural-gap separator evidence

The renderer now recognizes generic POS section transitions in whitespace that
the source OCR preserved, and prints dash glyphs at the measured gap midpoint.
It does not add line pitch or shift any OCR row.

## Metric proof

| Receipt | Before | After |
|---|---|---|
| Vons `678a7c94-4948-4ebf-b8e9-9a17c13051ec#2` | FAIL: real 3, synth 0 | PASS: 3/3 matched; y deltas 0.0015, 0.0044, 0.0000 |
| Trader Joe's `4c262079-4fec-4724-a8e1-2886f38ea454#1` | FAIL: real 1, synth 0 | PASS: 1/1 matched; y delta 0.0010 |

For both receipts, the complete non-separator portions of the check JSON are
byte-identical before and after. The exact full-fidelity evaluations still
report overall FAIL only because other focused lanes own their pre-existing red
metrics.

## Guard proof

- Focused renderer tests: 26 passed
  (`test_receipt_gap_separators.py` + `test_glyph_renderer.py`).
- `synthesis_loop/corpus_regression_gate.py check --json`: `ok: true`,
  zero findings.
- `synthesis_loop/render_regression_guard.py check`: both Costco pins and the
  Sprouts pin are byte-identical. The only changed pin is the intended Vons
  target whose missing separators this lane fixes.

All full-fidelity and guard runs used read-only dev table
`ReceiptsTable-dc5be22`; no gate record was written.
