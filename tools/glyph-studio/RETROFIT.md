# M5 retrofit — derived knobs vs. hand-tuned, all bitmap-font merchants

Closes the calibration-derivation epic (DERIVATION_EPIC.md M5): every
hand-tuned render knob diffed against what the v1 solvers derive, using the M0
production scorecard measurements (plus the Home Depot re-mint verify run) as
the real-side inputs. **Report-only:** derived corrections are NOT adopted
here — a profile change alters production renders, so each shift goes through
a visual A/B first (the epic's own adoption rule). The regression lock that
ships with this report is `receipt_dynamo/tests/unit/
test_merchant_profiles_contract.py` (CI-enforced: pins present and in range).

## Cap-height ratio (`ocr_cap_height_ratio`)

Cap height is linear in the ratio (validated on all six atlases,
VALIDATION.md), so the correction is algebraic:
`derived = hand_ratio / measured_h_ratio`, clamped to the renderer's
[0.65, 0.95] band.

| merchant | hand ratio | measured h_ratio | derived | clamped | verdict |
|---|---|---|---|---|---|
| CVS | 0.858 | 1.000 | 0.858 | 0.858 | **already perfect** — hand value = derived value |
| Home Depot | 0.860 | 0.976 | 0.881 | 0.881 | minor +0.021 available |
| Sprouts | 0.760 | 1.100 | 0.691 | 0.691 | −0.069 correction available (renders 10 % tall) |
| Wild Fork | 0.790 | 1.074 | 0.736 | 0.736 | −0.054 correction available |
| Vons | 0.649 | 1.296 | 0.501 | **0.650 (floor)** | **floor-bound** — see below |
| Trader Joe's | 0.720 | 1.565 | 0.460 | **0.650 (floor)** | **floor-bound** — see below |
| Costco | (none pinned) | 1.174 | n/a | — | renders 17 % tall with no ratio knob set; needs a pinned ratio first |

**Floor-bound merchants (Vons, Trader Joe's).** Their derived ratios fall below
the renderer's 0.65 clamp, meaning the ratio knob *cannot* fix their height —
even at the floor they still render ~30 % / ~57 % tall. The binding constraint
is the base-cap floor (`round(font_px x bitmap_cap_ratio x 0.9)`), exactly the
clamp `solve_cap_ratio(font_px=..., bitmap_cap_ratio=...)` models. Fixing them
means revisiting `bitmap_cap_ratio` / pitch-derived sizing, not the ratio —
flagged as follow-up work, out of scope for a report.

This confirms the epic's hypothesis about the hand-tuned spread: CVS's
eyeballed value was already optimal; four others carry derivable corrections;
two were hand-tuned against a clamp that made the knob inert (which eyeballing
could never reveal).

## bitmap_thin pins vs. erosion saturation

All real atlases saturate erosion at thin **0.40** (VALIDATION.md): every
distinct render behavior lives in [0, 0.40].

| merchant | pinned thin | effective thin | note |
|---|---|---|---|
| CVS | 0.15 | 0.15 | interior — headroom both ways |
| Vons | 0.30 | 0.30 | interior |
| Trader Joe's | 0.0 | 0.0 | floor — synth already sparser than real (density_ratio 1.24 is a height artifact, see above) |
| Costco | 0.0 | 0.0 | floor-railed: too dense at zero erosion |
| Sprouts | 0.225 | 0.225 | interior; cheap-solve cross-check says ~0.14 is the true density optimum (VALIDATION.md) |
| Wild Fork | 0.6 | **0.40** | ceiling-railed: 0.6 ≡ 0.4 (saturated); atlas too heavy — re-mint, don't erode |
| Target | 0.0 | 0.0 | hand-pinned previously |
| In-N-Out | 0.0 | 0.0 | hand-pinned previously |
| Home Depot | 0.6 | **0.40** | same saturation note as Wild Fork |

## Adoption policy

1. **Nothing in this report changes a profile.** Each derived correction
   (Sprouts, Wild Fork, Home Depot ratios; the Sprouts thin refinement) gets a
   render A/B against its review receipt before adoption.
2. **Floor-bound and railed merchants are font/sizing work, not knob work:**
   Vons + Trader Joe's height (base-cap floor), Wild Fork + Costco density
   (atlas weight). These are inputs to the font-intelligence epic
   (FONT_INTELLIGENCE_EPIC.md), whose acceptance test is that the railed pins
   disappear on family-fitted fonts.
3. The CI contract test keeps the epic's M0 win locked: no bitmap-font
   merchant can silently return to render-time derivation.
