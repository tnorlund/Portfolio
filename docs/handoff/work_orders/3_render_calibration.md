# Work Order 3 — Render-quality calibration + long-tail auto-calibration

**Tracks GitHub issue #1155.** This is the synthesis-quality half of the epic:
receipts are already *structurally* correct for every corpus merchant, but
*photorealistically* calibrated only for merchants that got dedicated ladder
work. Two parts: fix three known scale/degrade defects, then build the
auto-calibration step that generalizes synthesis to any thin merchant.

**Prerequisite:** #1152 (gold baselines, dated 2026-07-14) must be on `main` —
the ladder tests against those baseline records.

## Harness (use it, don't eyeball)

- Gold ladder L0–L4 + `m3_acceptance.py` in `tools/glyph-studio/py/`.
- Baselines: `tools/glyph-studio/py/baselines/*.2026-07-14.json` (#1152).
  Re-recorded on the healed post-re-OCR corpus, so attribution is clean: refpack
  changes isolate to L1, fitted degradation transfers (L3 stays green).
- GlyphScore refpacks are local assets at `~/glyphscore_refpacks/` on the dev Mac
  (face-conditioned per merchant: `.body`, `.bold`, `.face_T*`, `.large`). Not in
  the repo — do not commit them.
- Known cause of the drift: re-OCR produces taller glyph boxes than the original
  OCR, so vertical-scale constants fit pre-migration are biased high.

## Part A — three known defects

1. **Vons body scale (~60% too tall).** Body renders at h≈1.6 vs the small-print
   real receipt. Sizing-knob defect (min_pitch/condense/vscale interaction), not a
   font problem. Fix the knob; verify body-line height ratio within ladder
   tolerance.
2. **Costco vscale refit.** Current 0.857 was fit pre-re-OCR; the post-re-OCR
   cap_h regression points to ~0.827. **Derive vscale from measured cap heights on
   the healed corpus** (do not hand-tune to the ladder); confirm L1–L3 green
   against the Costco baselines (default + i1i2).
3. **Sprouts degrade + vscale fit.** Sprouts currently borrows Costco's degrade
   params. Fit Sprouts its own Kanungo/degrade parameters and its own vscale from
   its face-conditioned refpacks (body dominates census ~86%; serif T1 faces are
   separate, IoU 0.39 vs 0.89 within-face — fit per face where sample size
   allows). Run P1–P6 parity + the ladder against the sprouts baselines (current,
   modern, modern_degrade).

## Part B — `derive_scratch_profile(merchant, image_id)` (the generalizer)

**Evidence:** [evidence/longtail_synth_report.md](../evidence/longtail_synth_report.md).
Synthesizing for 7 merchants with only 1–2 receipts using borrowed fonts gave
2 pass / 2 marginal / 3 fail. **Every failure broke on height_ratio and/or
density_ratio; letterform width (wpc) stayed 0.88–1.10 across all 7.** Proof it's
calibration not font: re-rendering the failed "BJ's" receipt through Costco's
real profile recovered glyph height 0.70→0.98. POS-family font matching was NOT
the discriminator.

Build `derive_scratch_profile(merchant, image_id)` — for any merchant without a
minted atlas, auto-derive two knobs before render:

1. `bitmap_cap_ratio` from OCR cap-height of the merchant's real receipt(s).
2. `weight`/`bitmap_thin` from real-crop ink density.

Both are single-receipt-measurable with the existing v1 cheap-measurer
(`resolve_bitmap_thin` / `ocr_cap_height_ratio`). Combine with a borrowed clean
thermal font (reuse the 9 published in S3; **avoid buggy ones — see below**) and
section structure from the receipt + POS priors (already zero-effort). Same
acceptance rule: **knobs derived from measurement, not tuned to the metric.**

## Incidental fixes found during the eval

- **Home Depot atlas renders 'A' as 'I'** (SALES → SILES) — latent atlas bug,
  fix while doing render work. Until fixed, exclude homedepot from font-borrow.
- **The "BJ's" receipt is a mislabeled Costco receipt** — a `fix_place` candidate
  on dev (data cleanup, not code).

## Definition of done

- Vons, Costco, Sprouts all ladder-green against the 2026-07-14 baselines.
- All scale/degrade constants derived from corpus measurements with the
  derivation recorded (script/notebook in the PR), not fit to the metric.
- `derive_scratch_profile()` converts the 4 long-tail failures to near-anchor
  renders, fully automatable per merchant.
- Home Depot 'A' glyph fixed. No `.npz` committed.
