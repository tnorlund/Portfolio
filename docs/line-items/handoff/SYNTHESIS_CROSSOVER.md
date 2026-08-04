# Synthesis ↔ line-item extraction: what's worth building

Queued design note (not part of the current handoff work items). Written
2026-08-04 after the glyph/font synthesis sprint and the line-item validation
campaign converged enough to see the overlap.

## The asymmetry

**Synthesis does NOT improve the decoder.** The band-block decoder is
deterministic geometry — it has no parameters to fit, so synthetic volume
teaches it nothing. And synthetic receipts render *clean*, while every failure
mode still costing us is a damage mode: reverse-video totals (Costco),
fragmented digits (Target), ~14° tilt, pen strokes crossing decimals, thermal
fade. A synthetic golden set would pass ~100% while real receipts keep failing.

**Rule: synthetic receipts never enter `line_items_golden*.json`.** The golden
set's value is that it is real paper with real OCR noise. Synthetic cases live
in their own suite with their own floors.

**Extraction DOES improve synthesis**, and that's the stronger direction: the
validation campaign produced the per-merchant structure and the validator that
a receipt generator normally lacks.

## Three things worth building, in order

### 1. Degradation harness (highest value — unblocks a real fix)
Render a receipt with known ground truth, apply a *measured* degradation, assert
the pipeline still recovers the known items. Degradations come from the failure
taxonomy we catalogued with vision agents (`../agentic-review/`, the A–J modes
and the dossier `visual_evidence` notes), not invented:
- reverse-video (white-on-black) total rows — Costco
- digit fragmentation / dropped trailing minus — Target, Costco
- rotation at a known angle
- thermal fade / low contrast; occlusion strokes across a decimal

**Immediate payoff: it gives the tilt fix a ground truth.** The
`VisionOCREngine` hardcoded-`angleDegrees: 0.0` bug (see
`SWIFT_AND_GEOMETRY.md` §3) currently has nothing to verify against — no real
receipt has a trustworthy recorded angle, because the angle was never measured.
Synthesize at known angles → assert recovery within tolerance. Same trick
validates each re-OCR strategy: synth reverse-video → assert `invert` beats
`plain`; synth small print → assert `upscale2x` wins. That turns the strategy
ladder's ordering from a guess into a measurement before real receipts ever
touch it.

### 2. Priors → generator (make per-merchant synthesis faithful)
`receipt_upload/receipt_upload/line_items/assets/block_role_priors_v2.json` is
already a per-merchant template library: digit-collapsed line shapes with role
labels (PRICE / MEMBER / OUTSIDE), purity and support, harvested from 401 real
receipts. Feed it to the generator so synthetic layouts reproduce the shapes
the corpus actually contains. Pair it with the merchant quirk catalogue the
campaign produced, e.g.:
- Sprouts prints promo echo lines (`1 @ 2 FOR 14.00`) and BOGO `Sale Price`
  annotations that are NOT purchases
- In-N-Out and Trader Joe's print totals inside the items block
- Target prints `Regular Price` comparison rows
- Gelson's/Sprouts print CRV and bag fees as line entries (policy: they ARE
  items)
Those details are the difference between "a thermal receipt" and "a Sprouts
receipt". Existing surface to build on: `synthesis_loop/build_merchant_glyphs.py`,
`scripts/render_synthetic_receipts.py`, `tools/glyph-studio/`.

### 3. LayoutLM training data (the half that actually learns)
The neural side has parameters, so synthetic per-merchant receipts with perfect
labels are conventional training data — and the label set can be generated
exactly (no evaluator, no QA pass). Most valuable for label classes that are
rare or systematically mis-labeled in the real corpus (the campaign found
UNIT_PRICE ~82% INVALID, QUANTITY ~69% INVALID, plus a legacy vocab of ~2,700
rows). Synthesis can supply clean examples where the real corpus is polluted.

## Free QA the campaign already gives synthesis

A synthetic receipt whose items don't sum to its own printed subtotal is
malformed *by construction*. Run `reconcile()` /
`receipt_upload.line_items.geometry.is_proven` over generated receipts as a
generator self-test — the same arithmetic that judges real receipts.
`synthesis_loop/corpus_regression_gate.py` already establishes the
gate-the-corpus pattern to hang this on.

## Explicit non-goals

- No synthetic entries in the real golden set or the corpus sweep population.
- Don't use synthesis to chase decoder accuracy numbers; it measures the
  clean-input path, which is not where the remaining failures live.
- Don't build a general receipt generator "for its own sake" — each of the
  three items above pays for itself with a specific test or dataset.
