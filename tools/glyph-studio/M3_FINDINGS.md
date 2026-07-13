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

## Addendum (same day) — the replacement mechanism VALIDATED: acceptance PASS

Following the refutation, the density mechanism was isolated and the fix
passes the standing harness on **both** railed merchants.

### Diagnosis: two root causes, one cure

| merchant | real stroke/cap | shipped atlas stroke/cap | root cause |
|---|--:|--:|---|
| Wild Fork | **0.044** (4.1px @ 92px caps) | 0.100 | strokes **2.25× too wide** — mint-pipeline fattening (±3px jitter + VOTE 0.45 on the soft consensus skirts every edge; the crops themselves carry the true width) |
| Costco | **0.106** | 0.100 (already correct) | shape/coverage-driven excess (blocky chart-derived letterforms; renders tall) — **not** stroke width |

The chessboard effect explains why nothing else worked: hard-threshold
thinning (the VOTE sweep) fragments strokes before reaching target
(coverage 0.999→0.597), and render-time `bitmap_thin` saturates. The fix
thins in **distance space**: peel boundary pixels but never remove the
`zhang_suen` skeleton — strokes thin without ever fragmenting.

### Result — skeleton-protected stroke normalization, scored by the harness

| merchant | candidate | thin | verdict | density vs target | coverage |
|---|---|--:|---|---|--:|
| Wild Fork | solo shipped | 0.5 | CEILING-RAILED | 0.335 / 0.135 | 0.999 |
| Wild Fork | **erode2 (2px)** | **0.333** | **interior** | **0.137 / 0.135** | **0.999** |
| Costco | solo shipped | 0.5 | CEILING-RAILED | 0.308 / 0.187 | 1.000 |
| Costco | **erode1 (4px)** | **0.333** | **interior** | **0.200 / 0.187** | **1.000** |

Coarse mint-side normalization brings density in range; v1's `bitmap_thin`
then fine-tunes from the interior — the compose-with-v1 design working as
intended. **This is the epic's acceptance criterion, met** (pending the
epic-mandated visual A/B before any profile adoption).

### Production form

The shipped fonts are parametric stroke skeletons whose raster ink is exactly
`stroke_px ≈ dot.size × weight × cap/1000` — Wild Fork's hand-set
`weight: 1.4` reproduces the measured 6px atlas stroke, and the derived value
is `weight = stroke_ratio × 1000 / dot.size ≈ 0.60`. **Validated end-to-end:** compiling
`fonts/wildfork` at the derived `weight 0.60` lands projected density
**0.134 vs target 0.135** (0.99×, from 2.5× over — one closed-form step) at
erosion saturation, and one bisection step lower (`weight 0.50`) gives
**thin 0.333 interior, density 0.135 — exactly on target — coverage 0.999**.
The production fix for Wild Fork is a one-line `font.json` change, fully
derived from measurements. Because Costco shows the excess can be
shape-driven rather than stroke-driven, the **general derivation targets
density, not stroke**: bisect the mint-side ink parameter (`weight`, or
skeleton-protected erosion for crop-minted atlases) against the measured
per-word density target using v1's render-free cheap measurer. Stroke/cap
(stylescan `stroke_med`) is the *diagnostic* that says which root cause you
have.

### SDF-consensus mint (continuous-space idea): promising, not yet fairly tested

A first prototype (chamfer SDFs, sub-pixel alignment, derived threshold)
scored poorly (density 0.842) — but it averaged **raw, unfiltered** sample
stacks, skipping the production mint's inlier selection (IoU-ranked top-32).
That confounds the comparison; the concept (fixes fattening at the source,
plausibly recovers diagonals and crisp cross-merchant intermediates) needs a
re-run behind the mint's inlier filter before any verdict.

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

# validated fix (addendum): derived parametric weight, scored per merchant
#   weight = (stylescan stroke_med / cap_px) * 1000 / font.json dot.size
#   e.g. Wild Fork: 0.044 * 1000 / 73.5 ≈ 0.60  (hand value was 1.4)
# edit a copy of fonts/wildfork/font.json to the derived weight, then:
python -m glyphstudio.compile /tmp/wf_font_copy /tmp/wf_derived.glyphs.npz
python tools/glyph-studio/py/m3_acceptance.py /tmp/wf_derived.glyphs.npz \
  --merchant "Wild Fork:wildfork"
```

## Correction (2026-07-10) — the Wild Fork "railing" was a measurement artifact

Tyler's visual review of the render A/B caught broken layout in the synth;
tracing it invalidated the addendum's ship recommendation.

**Root cause:** receipt `058b662d#1` — used by the render A/B, by this
harness's first-N `gather()`, and plausibly by v1's original calibration —
has **44 same-row x-overlapping OCR word pairs** (doubled/fragmented OCR) and
~2× the corpus's cap scale. The renderer stamps every OCR word faithfully, so
duplicates overprint and inflate measured synth density. Clean receipts score
0–2 on this metric.

**Vetted 6-receipt distribution (dup-free, render scorecard):**

| | shipped font | derived w=0.60 candidate |
|---|--:|--:|
| range | 0.831 – 0.991 | 0.616 – 0.788 |
| median | **0.87 — in/near gate** | **0.66 — regression** |

**Conclusions:**
1. **The shipped Wild Fork font was never density-broken** on real receipts
   (slightly *light* if anything). No weight change ships. The derived-weight
   candidate is permanently withdrawn.
2. The "rails at bitmap_thin 0.6, ~48% too dense" narrative — the epic's
   flagship motivation — was the pathological receipt, not the font. v1's
   RETROFIT figures for Wild Fork (and possibly Costco, whose acceptance
   `gather()` shared the contaminated sampling) should be re-derived on a
   vetted corpus.
3. **What stands:** the pooling refutation (relative, same corpus both
   sides), the diagnostic toolchain (stroke/cap measurement, skeleton-
   protected erosion, the closed-form weight↔stroke mapping), and this
   harness — now with OCR vetting (`ocr_overlap_score`, receipts above
   `--max-overlaps` excluded) so first-N sampling can't poison future
   measurements.
4. Optional derived win, properly measured this time: the shipped font's
   vetted median 0.87 suggests reducing the `bitmap_thin` pin (0.6 → ~0.3)
   would center density at ~1.0 — needs a vetted-corpus derivation + A/B.

*Lesson for every measurement in this epic: vet the corpus before trusting
first-N samples; one bad receipt at the top of an index shaped two epics.*

## Fleet-wide vetted pin audit (2026-07-10) — no pin needs changing

Current shipped fonts + pins, vetted receipts only (OCR x-overlap ≤ 2),
render scorecard:

| merchant (pin) | vetted density_ratios | median | verdict |
|---|---|--:|---|
| Wild Fork (thin 0.6) | 0.92 / 0.94 / 0.94 / 0.99 | **0.94** | fine — tightest in the fleet |
| Sprouts (0.225) | 0.75 / 0.97 / 0.97 / 1.09 | **0.97** | fine |
| CVS (0.15) | 0.75 / 0.81 / 1.00 / 1.02 | **0.91** | fine |
| Home Depot (0.6) | 0.98 (only one vetted receipt exists) | **0.98** | fine (n=1) |
| Costco (0.0) | 0.53 / 0.74 / 0.76 / 1.12 | **0.75** | runs LIGHT + high variance — monitor, no change |

Every "railed merchant" narrative dissolves on vetted data. Costco — which
the epic doc says "rails at the 0.0 floor" (too dense) — actually runs
*light*, the opposite direction, with too much variance for a confident
correction. **The epic's M3/M5 acceptance criterion ("the railed merchants
come off the rails") was chasing measurement artifacts.** The epic's real
remaining objectives are diagonals (family-level handcraft), multi-face
rendering (M4, consumes the M2 map), and new-merchant cold start (M6).
Density calibration is CLOSED: current pins stand.

(Note: Home Depot has exactly one vetted receipt in dev — 17 of its 18
receipts flag the x-overlap detector. Whether that is genuinely pathological
OCR corpus-wide or partly detector over-flagging on HD's dense column layout
deserves a look before trusting any HD-specific conclusion.)
