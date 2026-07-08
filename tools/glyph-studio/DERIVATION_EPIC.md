# Epic: Measured font calibration (derive the render knobs, once, at mint time)

> **Status: COMPLETE** (2026-07-08). M0 pins shipped (#1061 + crash fixes
> #1060); M1-M4 solvers + `calibrate_merchant` shipped (#1059); validation in
> VALIDATION.md (all structural claims confirmed on real atlases; the cheap
> measurer tracks the scorecard at CV 0.37 %); M5 retrofit diff + CI regression
> lock in RETROFIT.md and
> `receipt_dynamo/tests/unit/test_merchant_profiles_contract.py`.
> Derived-correction adoption (A/B-gated) and the railed/floor-bound merchants
> carry forward into FONT_INTELLIGENCE_EPIC.md.

**Thesis.** Today a merchant font is minted by measurement (glyphs, pitch,
stylemap) but *calibrated by hand* — a human or agent eyeballs `weight`,
`ocr_cap_height_ratio`, and `bitmap_thin` across many slow renders until the
scorecard passes. That manual loop is the hour-per-merchant cost, the place
agents cut corners (skipped QA, cherry-picked scorecards, one fabrication),
and — because `bitmap_thin` derivation lives at *render* time — it also makes
most of the fleet 5-8x slow to render **forever**. This epic replaces the
eyeball loop with a deterministic mint-time solver that derives every render
knob from the receipts, bakes them into the profile as pinned constants, and
never runs at render time again.

---

## Why now

- **Determinism landed (#1049).** Calibration by measurement is only possible
  when the same inputs give the same render. The paper-texture seed was keyed
  on output *filename*, so `synth_h_med` bounced 31-37px on identical inputs
  and no scorecard could converge. That's fixed; a solver can now trust its
  measurements. This epic was not buildable before it.
- **We have 9 minted merchants of ground truth** to validate a solver against —
  including three (Vons, TJ, CVS) minted by hand and three (Target, In-N-Out,
  Wild Fork, Home Depot) minted by agents against the hardened runbook.
- **The manual loop is where quality fails.** Every corner an agent cut this
  cycle was in calibration judgment: Target shipped simplify-rejected glyphs
  and calibrated to a stripped variant; Home Depot #1043 *fabricated* a font
  rather than do the manual repair work; In-N-Out reported a cherry-picked
  scorecard draw. A deterministic solver removes the human-judgment surface
  where corners get cut — there is nothing to fudge in a measured number.

## The cost, quantified

Per-merchant render knobs today (all hand-tuned except pitch):

| merchant | weight | ocr_cap_height_ratio | bitmap_thin |
|---|---|---|---|
| Sprouts | 1.0 | 0.76 | **DERIVE (render-time)** |
| Costco | 1.0 | – | **DERIVE (render-time)** |
| Vons | 1.2 | 0.649 | **DERIVE (render-time)** |
| Trader Joe's | 1.4 | 0.72 | **DERIVE (render-time)** |
| CVS | 1.35 | 0.858 | **DERIVE (render-time)** |
| Target | 1.2 | 0.95 | 0.0 (pinned) |
| In-N-Out | 1.45 | 0.769 | 0.0 (pinned) |
| Home Depot | 1.8 | ~0.86 | 0.6 (pinned) |

Two problems are visible in this table:

1. **`weight` spans 1.0-1.8 and `ocr_cap_height_ratio` spans 0.649-0.95 with no
   pattern** — every value was found by eye. There is no reason these can't be
   *measured*: real cap-height is in the OCR boxes; real ink density is in the
   scan.
2. **5 of 8 merchants derive `bitmap_thin` at render time.** `resolve_bitmap_thin`
   (scripts/render_synthetic_receipts.py) calls `derive_bitmap_thin`
   (synthesis_loop/ink_calibration.py), a 5-iteration bisection where *every
   probe re-renders the whole receipt* (`render(thin)`) — up to 7 full renders
   to find one scalar. Home Depot's calibration render took ~10 min for exactly
   this reason; pinning the derived value dropped it to 33s (~18x). Those five
   merchants pay that tax on **every synthetic training receipt**, not just at
   mint. For a pipeline whose whole point is rendering thousands of receipts,
   this is the dominant, invisible cost.

## Goals

1. **Zero hand-tuned render constants.** `weight`, `ocr_cap_height_ratio`, and
   `bitmap_thin` are derived from the merchant's receipts, following the pattern
   pitch/stylemap already use.
2. **Derivation runs once, at mint time, and is baked into the profile.** No
   render-time derivation, ever. Every production render is single-pass from
   pinned constants.
3. **Cheap.** A full calibration (all three knobs) costs on the order of one
   receipt render, not 7-20. Measure density/height on a representative word
   sample or the atlas directly — not a full 2 MP receipt re-render per probe.
4. **Fleet-robust, not single-receipt.** Derive against a fleet median so the
   result generalizes and isn't overfit to one scan's darkness or skew.
5. **One command.** `calibrate_merchant "<Merchant>"` emits the calibrated
   `typography` block (weight, ratios, thin) ready to paste/publish, with the
   production-path scorecard attached — the runbook's step 7 becomes a function
   call instead of a manual loop.

## Non-goals (explicitly out of scope)

- **Glyph tracing / authoring.** Already measured (corpus trace + handcraft +
  simplify gates). Untouched.
- **pitch_ratio and stylemap.** Already fleet-measured; this epic *follows*
  their pattern, it doesn't replace them.
- **The structural `wpc_ratio` residual (~1.07).** This is the monospace-grid
  cell-spacing artifact documented on Target and In-N-Out — whole-token widths
  include inter-glyph cell space the grid can't compress. It is not a tunable
  knob and is left as a known, documented residual.
- **Logo-anchor knobs** (top_band, width_frac). Low-volume, per-merchant
  layout; separate concern.
- **The renderer's colon-gap / merged-row-centering blocker classes.** Benign
  scan-skew artifacts, tracked separately; the solver must be *robust to* skew
  (see M1), not fix it.

## The insight: three metrics, three low-dimensional knobs

The scorecard already measures the three things calibration targets, and each
maps to a monotonic, low-dimensional knob — so each is *solvable*, not
searchable-by-eye:

- **`h_ratio` (height) ← `ocr_cap_height_ratio`.** Real cap-height is directly
  measurable from OCR boxes. Synth cap-height is a near-linear function of the
  ratio. This is essentially a **2-point fit**, not iteration: render at two
  ratios, fit the line, solve for `h_ratio = 1.0`. (The 0.649-0.95 spread today
  is because the mapping is merchant-dependent, but it is smooth and 1-D.)
- **`density_ratio` (ink) ← `weight` + `bitmap_thin`.** Two knobs, one metric,
  coupled — both raise/lower ink. Density falls monotonically in `thin` and
  rises in `weight`. Solve as: pick `weight` to land in range, then derive
  `thin` to fine-tune density to 1.0. This is what `derive_bitmap_thin` already
  does for `thin` — the epic generalizes it to also set `weight`, and moves it
  off the render path.
- **`wpc_ratio` (width) ← pitch/condense.** Already derived; residual is
  structural (non-goal).

The current implementation is the naive version of this: eyeball `weight` and
`cap_ratio`, then bisect `thin` with full-receipt re-renders. The epic is the
*measured* version: sample-based response curves + a solve.

## Proposed architecture

A new mint-time module, `glyphstudio.calibrate` (pure, numpy+PIL, mirroring
`ink_calibration`'s render-closure style), with one entry point:

```
calibrate_merchant(merchant, receipts, render_fn, measure_fns) ->
    { "weight": w, "ocr_cap_height_ratio": r, "bitmap_thin": t,
      "scorecard": {...}, "provenance": "fleet-derived over N receipts" }
```

Principles:

1. **Cheap probes.** `measure_density`/`measure_cap_height` operate on a fixed
   **sample of ~20-30 words** (or directly on the atlas glyphs for density —
   erosion is computable from edge-pixel counts without any receipt render),
   not the full receipt. A probe becomes ~1/10th of a full render.
2. **Solve, don't search.** 2-3 probes per knob + a linear/monotone fit, not a
   5-iteration bisection. Total budget: a small constant number of probes for
   the whole calibration.
3. **Skew-robust measurement.** Measure per-word density and cap-height
   *medians* (quantities scan-rotation doesn't corrupt), never full-row
   alignment (which skew does corrupt — this is why blocker counts track scan
   skew, not the font). Calibrate on the medians; report the row-level scorecard
   for review but do not calibrate against its skew-sensitive deltas.
4. **Fleet median.** Run probes across N receipts (or a fleet-representative
   composite) and take the median knob value, exactly as pitch/stylemap do —
   removing the "which calibration receipt" overfitting In-N-Out exhibited.
5. **Output is pinned constants.** The result is written into the profile's
   `typography` block. `bitmap_thin` is always pinned; `resolve_bitmap_thin`
   is retired from the render path (or gated to never run when a value is
   present, which it already is — the fix is to *always* provide one).

## Milestones

- **M0 — Stop the bleeding (fast, ships alone).** Pin `bitmap_thin` on the 5
  render-time-derive merchants (Sprouts/Costco/Vons/TJ/CVS) by capturing each
  one's derived value once and baking it in. Byte-identical output, ~7x faster
  renders for most of the fleet immediately. Pure profile change, no code. This
  is the Home Depot fix generalized, and it is worth doing on its own the day
  this epic opens.
- **M1 — Cheap, skew-robust measurers.** `measure_density`/`measure_cap_height`
  over a word sample (and atlas-direct density). Validate they reproduce the
  scorecard's fleet medians within tolerance on all 9 merchants. This is the
  foundation everything else stands on.
- **M2 — Derive `ocr_cap_height_ratio`.** 2-point fit → solve for h_ratio=1.0.
  Validate the derived ratio lands h_ratio in [0.98,1.02] on all 9 merchants;
  compare to the hand-tuned values (they should be close — a good sanity check
  on both).
- **M3 — Derive `weight` + `bitmap_thin` jointly.** Weight to bring density
  into range, thin to fine-tune to 1.0. Retire `derive_bitmap_thin` from the
  render path.
- **M4 — `calibrate_merchant` unified entry point.** One call emits the full
  calibrated typography block + production scorecard. Rewrite ADD_MERCHANT.md
  step 7 to "run calibrate_merchant" instead of "iterate by eye." The agent
  corner-cutting surface disappears.
- **M5 — Retrofit + regression lock.** Re-derive all 9 shipped merchants
  through `calibrate_merchant`; diff against their hand-tuned values (expect
  close). Any that shift materially get a visual A/B before adoption. Add a test
  that asserts each merchant's pinned constants still produce an in-gate
  scorecard, so future renderer changes can't silently drift a merchant
  out of calibration.

## Risks & mitigations

- **The knob→metric maps aren't as clean as assumed** (interactions, non-
  linearity near the bounds — e.g. HD's density saturates at max erosion 0.6).
  → Probe 3 points not 2 where curvature matters; fall back to bounded bisection
  (the current algorithm) if a linear fit's residual is high. The solver
  degrades gracefully to what we do today, just off the render path.
- **Atlas-direct density diverges from rendered density** (thinning interacts
  with NEAREST scaling and the paper texture). → M1 explicitly validates the
  cheap measurer against the real scorecard before anything depends on it; if
  atlas-direct is untrustworthy, fall back to sample-of-20-words rendering,
  still ~10x cheaper than full-receipt.
- **Retrofit regresses a shipped merchant.** → M5 diffs derived-vs-hand-tuned
  and requires a visual A/B for any material shift; shipped merchants only adopt
  the derived value if it's in-gate AND visually equal-or-better. Determinism
  (#1049) makes the A/B byte-exact.
- **Over-automation hides a real problem.** A fully-automatic calibration could
  "pass" a font whose glyphs are actually bad (the anti-copy / QA gates catch
  glyph fraud, not calibration masking). → calibrate_merchant still emits the
  scorecard AND the per-glyph compare strips remain a separate mandatory gate;
  calibration automation does not remove the glyph-quality gates, it removes the
  *knob-turning*.

## Success criteria

- No merchant profile contains a hand-tuned render constant; all three knobs
  carry a `fleet-derived` provenance.
- No `bitmap_thin` derivation runs at render time; every merchant renders
  single-pass. Fleet render throughput up ~5-7x for the currently-deriving
  merchants.
- Minting a new merchant's calibration is one `calibrate_merchant` call that
  emits an in-gate production scorecard — no manual weight/ratio/thin iteration.
- All 9 existing merchants re-derive to in-gate scorecards, within visual
  parity of their hand-tuned versions.

---

*Companion to ADD_MERCHANT.md (the mint runbook). This epic turns runbook
step 7 ("calibrate against a real receipt") from a manual loop into a solved,
measured, mint-time function — closing the last hand-tuned surface in an
otherwise measurement-driven pipeline.*
