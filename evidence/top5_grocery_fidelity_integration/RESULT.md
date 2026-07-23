# Top-five grocery fidelity integration

Integration branch: `codex/top5-grocery-fidelity-integration`

Code head before this evidence commit: `75bab2a58e28e6862abb7028c6a658f828c376ff`

All evaluations used `ReceiptsTable-dc5be22` in read-only mode. No run used
`--write-gate-record`.

## Integrated lanes

- `codex/gelsons-measured-flag-lanes` (integration base)
- `codex/vons-scannable-code128`
- `codex/storefront-logo-edge-filter`
- `codex/grocery-structural-gap-separators`
- `codex/scaled-row-amount-lanes`
- `codex/wildfork-source-geometry-proof`
- `codex/grocery-row-style-fidelity`
- `codex/costco-arithmetic-reconcile`
- `codex/wildfork-logo-separator-filter` at `00cacf745`

The structural-separator merge conflicted with the measured-separator parent
in `receipt_renderer.py`. The resolution keeps a measured separator inventory
authoritative and enables semantic source-gap recovery only when no measured
inventory exists. This prevents duplicate rules while retaining the generic
fallback.

## Exact final scoreboards

| Merchant / receipt | columns | style | tokens | separators | graphics | logo | arithmetic | overall |
|---|---|---|---|---|---|---|---|---|
| Vons `678a7c94…#2` | PASS | **FAIL** | PASS | PASS | PASS | PASS | PASS | FAIL |
| Trader Joe's `4c262079…#1` | PASS | PASS_WITH_GAPS | PASS | PASS | PASS | PASS | PASS | PASS_WITH_GAPS |
| Wild Fork `f008ea77…#2` | PASS | PASS_WITH_GAPS | PASS | PASS | PASS | PASS | PASS | PASS_WITH_GAPS |
| Gelson's Westlake Village `223c03e2…#1` | PASS | **FAIL** | PASS | PASS | PASS | PASS | PASS | FAIL |

Vons remains red only because its real address class is bold (`1/2`) while
the synth address class is not (`0/2`). Gelson's remains red only because its
real address (`3/3`) and footer (`2/2`) classes are bold while both synth
classes are `0` bold. Those are honest truth/style blockers, not integration
regressions.

## Trader Joe's compatibility proof

The initial combined run reported style FAIL: the footer class had real bold
`1/2` and synth bold `0/2`. Bisection found the first apparent change at
`819d03373`, specifically its render-true `box_sink` evaluator geometry.
Re-running main plus the style fix with only that evaluator change reproduced
the failure, proving the earlier style green sampled stale source boxes.

Render-true measurement showed:

- real `SALE TRANSACTION`: `1.46x` the real body stroke;
- synth heavy atlas: `1.28x` the synth body stroke;
- bold gate: `1.35x`.

A standalone transaction-heading rule now retains the compiled heavy face and
adds a two-dot thermal reinforcement. One dot was wholly inside this atlas's
existing heavy glyph envelope and produced byte-identical output. The final
metric reports footer bold real `1/2`, synth `1/2`, style PASS_WITH_GAPS; all
six other metrics pass. Item-count prose and payment narration containing the
word `transaction` remain untouched.

## Wild Fork separator proof

Before the final detector lane, the combined run treated logo strokes as a
second separator (`real=1`, `synth=2`) and failed on a phantom top-band rule.
The final evaluator excludes a truth-measured logo band from separator
inventory, with a real-vs-real control committed in the focused lane. Final
Wild Fork separator inventory is `real=0`, `synth=0`, PASS.

## Smith's diagnostic fixture

Smith's has no live sealed truth bundle, so this is diagnostic only. The exact
fixture `/tmp/smith_s_truth.XXXXXX.json` had SHA-256
`3f8e3efab5bc58c2c9a187ba077526238f03bdcd9574fccda2e82feab3d1b102`
and explicitly rendered without a verified logo asset.

On `55af0e9b…#1`: columns PASS, style PASS_WITH_GAPS, tokens PASS, logo PASS,
arithmetic PASS, separators FAIL, graphics FAIL. The remaining separator gap
is five missing real dash rows plus one phantom synth double row. Graphics is
missing the real Kroger careers QR. This fixture result cannot authorize a
seal.

## Verification

- Focused integration tests: `94 passed`.
- Corpus regression gate: PASS, zero findings.
- Render regression guard:
  - Sprouts: byte-identical.
  - Costco golden/new: changed as expected from the measured-layout parent.
  - Vons golden: changed as expected from the Vons graphics/column lanes.
- `git diff --check`: clean.

Final local artifacts:

- `/tmp/top5-integration-final-vons/vons_eval/`
- `/tmp/top5-integration-final-trader/trader_joe_s_eval/`
- `/tmp/top5-integration-final-wild/wild_fork_eval/`
- `/tmp/top5-integration-final-gelsons/gelson_s_westlake_village_eval/`
- `/tmp/top5-integration-final-smith/smith_s_eval/`
- `/tmp/top5-integration-final-render-guard/`
