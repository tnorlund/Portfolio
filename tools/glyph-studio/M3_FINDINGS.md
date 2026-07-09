# M3 findings — pixel-space family pooling refuted on both epic criteria

**Status:** measured result, 2026-07-09. Three independent measurements, all
local (dev Dynamo + S3 scans; no cloud compute). This amends the epic's M3
design *before* M4/M5 build on it.

## Setup

- Family under test: `{costco, innout, sprouts, wildfork}` — the M2 IoU
  cluster containing **both density-railed merchants** (Wild Fork
  ceiling-railed at effective thin 0.40; Costco likewise rail-bound).
- Pooled atlas: `build_merchant_glyphs.py "Costco Wholesale;Sprouts Farmers
  Market;In-N-Out Burger;Wild Fork;Wild Fork Foods"` over 140 receipts
  (native `;`-pooling, same mint pipeline as production).
- Solo baselines: the shipped v1-refined fonts (`fonts/wildfork`,
  `fonts/costco`) compiled via `glyphstudio.compile`.
- Instruments: v1's render-free `calibrate_merchant` (`py/m3_acceptance.py`)
  with real-side scalars measured from dev scans by the scorecard's own
  `_ink_metrics`; consensus decisiveness by `py/m3_crispness.py`.

## Finding 1 — acceptance test FAILS: pooled is *denser*, still railed

Same corpus, same real-side targets; only the font changes.

| merchant | target density | solo shipped | pooled family | verdict |
|---|--:|--:|--:|---|
| Wild Fork | 0.135 | 0.335 · thin railed | **0.404 · thin railed** | pooled **worse** |
| Costco | 0.187 | 0.308 · thin railed | **0.402 · thin railed** | pooled **worse** |
| scorecard glyph coverage | | 0.999 / 1.000 | **0.735 / 0.755** | pooled loses ¼ |

(The cheap measurer inflates absolute density by a stable per-merchant factor
— v1 measured Sprouts at 1.967 — which cancels in the solo-vs-pooled
comparison. The solo rows reproduce v1's railing, validating the harness.)

## Finding 2 — diagonals get *blurrier* when pooled (cvs+vons probe)

Consensus decisiveness (2·|consensus−0.5| over ink, centroid-aligned), solo vs
pooled at ~2× samples:

| glyph | best solo | pooled | Δ |
|---|--:|--:|--:|
| W | 0.565 (n=25) | 0.402 (n=69) | **−0.163** |
| A | 0.448 | 0.375 | −0.074 |
| K | 0.397 | 0.335 | −0.062 |
| X | 0.550 | 0.498 | −0.052 |
| M | 0.444 | 0.401 | −0.043 |

Every hard diagonal degrades despite 2–17× more samples. Within-merchant
accumulation *does* help (cvs `W` n=4 → 0.349 vs vons n=25 → 0.565): the
problem is letterform mixing, not evidence scarcity.

## Finding 3 — pooling *does* help coverage

cvs solo 46 glyphs / vons solo 46 → pooled 51–54 built, dropped 9→6. Pooling
recovers rare glyphs (`Z k z $ ( )`). This is real and survives.

## Root cause

The two same-family merchants overlap at IoU 0.62 **after** shape
normalization — the residual 38% is genuine letterform difference (the epic's
own `merchant_printer_offset`). Median-voting crops across that offset blurs
the consensus; blur at the vote threshold **adds ink at stroke edges** —
simultaneously fattening the atlas (Finding 1) and shredding diagonals
(Finding 2). The epic's premise that cross-merchant pooling buys "~10×
evidence" treated the offset as removable noise; it is signal.

## What this means for the epic

1. **Density railing → density-calibrated minting (per merchant).** The fix
   the data supports is deriving the mint's vote/erosion parameter against the
   measured real density using v1's cheap measurer (render-free loop) — the
   "re-mint lighter" v1 already prescribed, derived instead of eyeballed.
2. **Diagonals → family-level handcraft.** Averaging can't produce them in any
   space when letterforms differ; `handcraft.py` skeletons authored **once per
   family** and reused across members is the legitimate exploitation of
   cross-merchant similarity (amortizes ~20 hard glyphs × 5 families instead
   of × 9 merchants).
3. **Family pooling survives for coverage + cold start (M6).** Rare-glyph
   recovery and new-merchant bootstrap are where pooled evidence genuinely
   pays.
4. **M4 (multi-face rendering) is unaffected** — it consumes the
   `(merchant, section) → (family, face)` map (M2, shipped) and does not
   depend on pooled atlases.

## Repro

```
# pooled family mint (dev data)
python synthesis_loop/build_merchant_glyphs.py \
  "Costco Wholesale;Sprouts Farmers Market;In-N-Out Burger;Wild Fork;Wild Fork Foods" \
  /tmp/m3 railed_family 80

# acceptance: solo vs pooled on the railed merchants
python tools/glyph-studio/py/m3_acceptance.py /tmp/m3/railed_family.glyphs.npz

# diagonal crispness: solo vs pooled stacks
python tools/glyph-studio/py/m3_crispness.py /tmp/m3/cvs_solo.samples.npz \
  /tmp/m3/vons_solo.samples.npz /tmp/m3/cvs_vons_pooled.samples.npz
```
