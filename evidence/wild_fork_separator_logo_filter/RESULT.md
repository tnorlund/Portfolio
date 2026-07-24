# Wild Fork separator/logo classification proof

Golden receipt: `f008ea77-272b-4554-b8a6-e1676ec6a088#2`.
Merchant truth: online-active `wild_fork` v1,
bundle `a9de285daf995ce20245bdb397f1a0c25a25ec5ab27587f723b0df5e6c7504cc`.

## Root cause

The red separator candidates were logo strokes, not receipt rules. On the
clean token-fix parent (`678e683a0`), the metric compared:

- real y=0.0157 (42 px);
- synth y=0.0150 (40 px);
- phantom synth y=0.0247 (66 px).

The independent logo metric measured the real and synth logo bounds at
`cy=160, h=321`, covering all three candidates, and passed the logo itself.
The two faded item-header dash rules visible lower in both panels were not the
candidates responsible for the gate failure.

The real-vs-itself control makes the classification error explicit: before
logo exclusion it reports `PASS`, count 1/1, with its sole matched
"separator" at y=0.0157 (the logo stroke) and `dy=0`. Thus the metric was
self-consistent but measuring the wrong visual object.

The separator inventory now excludes only the vertical extent of an expected
logo's measured dominant connected component. It does not loosen separator
thresholds or apply a fixed merchant/page cutoff. A regression fixture proves
that logo-like horizontal strokes are removed while a genuine rule outside the
measured graphic remains matched and gated.

## Metric proof

| Metric | Before | After |
|---|---|---|
| separators | FAIL: real 1, synth 2; phantom y=0.0247 | PASS: no logo strokes in either rule inventory |
| tokens | PASS: ink recall 0.9717 | PASS: ink recall 0.9717 |
| columns | PASS | PASS |
| graphics | PASS | PASS |
| logo | PASS | PASS |
| arithmetic | PASS | PASS |

The full golden eval is `PASS_WITH_GAPS` only because the policy style class
has insufficient coverage; every tested fidelity metric is green.

## Guards

- Full-fidelity metric/truth/gate tests: 56 passed.
- `synthesis_loop/corpus_regression_gate.py check --json`: `ok: true`,
  zero findings.
- `synthesis_loop/render_regression_guard.py check`: Costco golden, Costco
  new, Vons golden, and Sprouts are byte-identical to the committed baseline.
- Black and `git diff --check`: clean.

All live evaluation used read-only dev table `ReceiptsTable-dc5be22`; no gate
record or other DynamoDB row was written.
