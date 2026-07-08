# M1/M2 validation — render-free claim check on real merchant atlases

The calibrate measurer (`glyphstudio.calibrate`, M1/M2) rests on three
structural claims about how a real atlas responds to the render knobs.
`glyphstudio.validate` checks them on any `.glyphs.npz` **without a receipt
render**. This is the render-free half of the M1/M2 validation milestone; the
other half (absolute agreement between a cheap solve and a real render) needs
the render path and is measured against the scorecard — see the "Scope & units"
note in `calibrate.py`.

Run: `python -m glyphstudio.validate <atlas.npz> [--cap-px N]`.

## Results — the 6 M0 merchants (cap_px 34, full atlas as corpus)

| merchant | coverage | density 0→max thin | saturation thin | monotone (thin) | monotone (weight) | cap linear (34→68px) | OK |
|---|---|---|---|---|---|---|---|
| CVS | 1.000 | 0.450 → 0.348 | 0.40 | ✓ | ✓ | ✓ | ✓ |
| Vons | 1.000 | 0.481 → 0.378 | 0.40 | ✓ | ✓ | ✓ | ✓ |
| Trader Joe's | 1.000 | 0.428 → 0.324 | 0.40 | ✓ | ✓ | ✓ | ✓ |
| Costco | 1.000 | 0.367 → 0.249 | 0.40 | ✓ | ✓ | ✓ | ✓ |
| Sprouts | 1.000 | 0.371 → 0.269 | 0.40 | ✓ | ✓ | ✓ | ✓ |
| Wild Fork | 1.000 | 0.415 → 0.307 | 0.40 | ✓ | ✓ | ✓ | ✓ |

All three claims hold on every real atlas:

1. **Erosion saturates at thin 0.40 for all six** — density stops falling
   beyond it because `thin_ink_mask`'s drop-period floors at 2. The render-time
   bisection probes up to 0.6 blindly, so **every probe in (0.40, 0.60] is
   wasted** — the core M1 efficiency claim, now confirmed on real fonts, not
   just asserted.
2. **Monotone** in both thin (down) and weight (up) — the property the joint
   solver relies on.
3. **Cap height is exactly linear** — 34px → 68px doubles the cap glyph height
   on all six, validating M2's two-point-fit premise.

## Cross-check against the M0 pins

The saturation result independently corroborates the M0 boundary flags without
any render:

- **Wild Fork was pinned at the 0.6 ceiling.** But erosion saturates at 0.40, so
  0.6 does exactly what 0.4 does — the render-time bisection railed to the
  ceiling only because the atlas is still too dense at saturation and can't
  erode into range. The cheap curve would have flagged this before calibrating.
- **Costco was pinned at the 0.0 floor** (too dense even with zero erosion). Its
  un-eroded density (0.367) is on the lighter side here, so its floor-railing is
  a padded-crop/scorecard-units effect rather than raw tight-ink density — a
  reminder that the absolute-agreement half of validation still needs the real
  render, exactly as the scope note says.

Net: the pins that rail at a clamp (Wild Fork 0.6, Costco 0.0) are the atlases a
future font-quality pass should re-mint; the four interior pins (CVS 0.15, Vons
0.3, Trader Joe's 0.0, Sprouts 0.225) sit where erosion still has headroom.

## What this does and does not establish

- **Establishes (render-free):** the measurer's monotonicity, saturation, and
  cap-linearity assumptions hold on all shipped merchant atlases, so the solver
  is operating on the response shape it was designed for; and the render-time
  bisection's probe range can be capped at the saturation thin.
- **Established (with the render path), below:** the cheap density tracks the
  scorecard's by a stable per-merchant factor, so the cheap solve recovers the
  same density-match thin the scorecard would — validated on Sprouts.

## Absolute correlation — cheap vs. real render (Sprouts)

The render-free checks above confirm the *shape*; this confirms the cheap
density is a faithful *proxy* for the scorecard density the render-time solver
targets. Sprouts (the cleanest M0 merchant) was rendered at a forced
`bitmap_thin` sweep on its M0 review receipt (262 words, 251 scorecard-eligible,
coverage 1.0), and the scorecard's per-word synth density compared against the
cheap `median_word_density` at the same thins and cap_px (44). Sanity: thin
0.225 reproduced the M0 derive exactly (`density_ratio` 0.955).

| forced thin | scorecard synth density | cheap density | cheap / scorecard |
|---|---|---|---|
| 0.000 | 0.1950 | 0.3831 | 1.965 |
| 0.100 | 0.1870 | 0.3687 | 1.972 |
| 0.225 | 0.1760 | 0.3455 | 1.963 |
| 0.350 | 0.1680 | 0.3325 | 1.979 |
| 0.500 | 0.1570 | 0.3074 | 1.958 |

- **Stable factor:** cheap/scorecard = **1.967 ± 0.007 (CV 0.37 %)** — a
  near-constant per-merchant multiplier. The absolute-units gap (tight-ink vs
  padded-crop) is a *scale*, not a distortion.
- **Perfect rank agreement:** Spearman **1.000**, Pearson 0.999.
- **Recovers the density optimum:** the scorecard's real-density target (0.1839,
  padded units) maps through the 1.967 factor to a cheap target of 0.362, which
  the cheap curve hits at **thin ≈ 0.138**. The scorecard's own
  `density_ratio → 1.0` optimum is **thin ≈ 0.136** — the two independent
  measurers agree to **within 0.002**. (The render-time bisection landed on
  0.225 only because its blind grid stepped 0.0 → 0.3 → 0.15 → 0.225 and never
  probed ~0.1; the cheap full-curve measurer would have found the better point,
  so it is *more* faithful to the density optimum here, not less.)
- The cheap curve was independently reproduced on the Sprouts atlas from this
  worktree (end/start density ratio 0.79 vs the experiment's 0.80), confirming
  the cheap side.

**Verdict:** for Sprouts the render-free measurer is a faithful proxy — density
tracks the scorecard by a stable ~1.97× factor and recovers its density-match
thin to within 0.002.

## Still open (for M5)

- **Per-merchant factor spread + non-body regimes.** Only Sprouts (a
  regular-face-dominant merchant) was measured, and only its body density. The
  1.967 factor is per-merchant; whether it is stable enough *across* merchants
  to bridge without a per-merchant render, and how stylemap heavy/scaled rows
  shift it, is the remaining validation — it belongs to M5's retrofit, where
  each merchant gets a real render as the source of truth.
