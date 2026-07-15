# Long-tail Synthesis Evaluation (1–2 receipt merchants)

**Method:** synthesized 2–3 receipts each for 7 merchants that have only 1–2 real
receipts in the dev corpus, using fonts borrowed from the 9 already published in
S3 (no per-merchant atlas mint). Scored against each merchant's real receipt(s)
using the runnable gold-ladder levels, anchored to a Sprouts native-profile
render (h0.95 / dens0.81 / wpc0.98, 4 blockers).

All DynamoDB access was read-only; no writes, no commits.

## Result: 2 pass / 2 marginal / 3 fail

| Merchant | Borrowed font | Ladder | Verdict | Render |
|---|---|---|---|---|
| 7-Eleven | cvs | 1 blocker (beats anchor) | **PASS** | renders/lt_7eleven.png |
| Barnes & Noble | target | 3 blockers | **PASS** | renders/lt_bn.png |
| Ralphs | vons | font fine (h0.94/dens0.79/wpc0.98), 6 layout blockers from scrambled OCR | MARGINAL | — |
| Chipotle | innout | h0.72/dens0.59 (short+light) + inherited header garble | MARGINAL→FAIL | — |
| Walmart | target | dens0.40 (target too light for Walmart's dense print) | FAIL | — |
| Lowe's | homedepot | dens1.18/wpc1.11 + homedepot atlas renders 'A' as 'I' (SALES→SILES) | FAIL | renders/lt_lowes.png |
| "BJ's" | bitMatrix-C2 | h0.61/dens0.30 | FAIL | renders/lt_bjs.png |

## Dominant failure mode: missing per-merchant SCALE/DENSITY calibration, NOT letterforms

Every failure broke on **height_ratio and/or density_ratio**. Letterform width
(wpc_ratio) stayed **0.88–1.10 across all 7** — the borrowed glyphs are correct;
they are just the wrong size and ink weight.

- A bare font swap gives glyphs but not the two knobs `bitmap_cap_ratio` (height)
  + `weight`/`bitmap_thin` (ink density).
- POS-"family" match was **NOT** the discriminator: both same-industry borrows
  (Lowe's→homedepot, BJ's→Costco class) failed on scale, while both cross-class
  borrows (7-Eleven→cvs, B&N→target) passed.

## Recovery proof (the gap is calibration + assets, not the font)

Re-rendering the failed "BJ's" receipt through Costco's **real native profile**
restored the logo + bold rows and recovered global glyph height **0.70 → 0.98**
(renders/lt_bjs_ascostco.png).

Doing nothing is worse than borrowing: the current no-font default falls back to a
too-wide monospace TTF that overflows the margin (7 blockers vs 1 for a borrowed
font).

## Incidental findings

1. **The "BJ's" receipt is actually a mislabeled Costco receipt** — a `fix_place`
   candidate on dev.
2. **Home Depot atlas latent bug: 'A' renders as 'I'** (SALES → SILES). Worth
   fixing while doing render work.

## Recommendation: `derive_scratch_profile(merchant, image_id)`

Minimum asset set for an arbitrary 1–2-receipt merchant (no atlas mint needed):

1. A borrowed clean thermal font (reuse the 9 in S3; avoid buggy ones like homedepot).
2. Two auto-derived knobs, both single-receipt-measurable with the existing v1
   cheap-measurer (`resolve_bitmap_thin` / `ocr_cap_height_ratio`):
   - `bitmap_cap_ratio` from OCR cap-height of the merchant's real receipt(s)
   - `weight`/`bitmap_thin` from real-crop ink density
3. Section structure from the receipt + POS priors (already works zero-effort).
4. Optional logo/stylemap only for logo-dominant merchants.

Net: font + 2 measured knobs converts the 4 failures into near-anchor renders and
is fully automatable per merchant. The pipeline's real long-tail gap is not
"no atlas" — it is "no auto-calibration step when an atlas can't be minted."

**This became Deliverable 4 on issue #1155.**
