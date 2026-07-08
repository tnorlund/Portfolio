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
- **Still open (needs the render path):** that a cheap-solved `bitmap_thin`
  lands the scorecard's `density_ratio` within tolerance of the render-time
  solve. That comparison is unit-bridged through the scorecard's padded-crop
  density and belongs to M5's regression lock, where a real render is the source
  of truth.
