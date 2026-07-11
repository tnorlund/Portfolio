# M6 findings: cold-start pilot — a novel Smith's receipt from scratch

**Question.** Can the fleet's tooling bootstrap a merchant it has never minted
— no atlas, no typography profile, no stylemap — from its own dev scans plus
fleet knowledge, and synthesize an in-gate, visually plausible NOVEL receipt
with zero hand-drawn glyphs and zero hand-tuned constants?

**Answer: YES, with one qualified ingredient.** The cold-start pipeline
(vet → mint → gates → knobs → face priors → compose → render) produced a
novel three-item Smith's receipt whose ink metrics sit inside the real
corpus's distribution and whose layout/section rhythm matches the real scans
(`M6_COMPARISON.webp`). The qualification: on a corpus this thin (10 vetted
receipts, ~8k letter crops) the **native mint covers only 27/94 glyphs and 8
of those misrender** — the readable render needs labeled family borrowing
(75 glyphs from Vons, the top-affinity fleet member). Harness:
`py/m6_cold_start.py` (all numbers below reproduce from it).

Smith's *does* have a legacy `merchant_profiles.json` entry (VT323, condense
0.92, stroke 1 — receiptfont.com guesswork). It is exactly the hand-tuned
constants class this epic bans; the pilot ignored it and injected a scratch
profile at runtime (committed nowhere).

## 1. Evidence budget (dev, `ReceiptsTable-dc5be22`)

- 13 receipt-places across 4 name variants ("Smith's" 7, "Smith's
  Marketplace" 4, "Smith's Fresh for Everyone" 1, "Smiths" 1); 12 loadable
  (one stale place row: 6539deb9…#1 has letters but no receipt).
- **10/12 pass vetting** (`ocr_overlap_score <= 2`); the two rejects score
  3 and 7 (the 7 also reads density 0.34 vs corpus ~0.26 — the Wild Fork
  contamination class, correctly fenced out).
- **7,970 letter crops** on the vetted corpus (fleet mints used 3–6× that);
  70 distinct chars observed, only 42 with ≥ MIN_SAMPLES(10).
- QA'd sections: every vetted receipt carries 5–7 VALID ReceiptSection rows;
  **45% of OCR lines covered** (309/685) — matches the "weakest corpus"
  billing. All core sections present (STOREFRONT/ADDRESS/ITEMS/PAYMENT/
  FOOTER; SUMMARY/TOTAL_LINE on about half).

## 2. Mint + gates (atlas: **marginal→fail as solo, pass as hybrid**)

`build_merchant_glyphs.py` (unforked, `GLYPH_KEYS_JSON` = the 10 vetted
receipts): **27 native glyphs** (`-.012456789ADEFILNRTUVenrsu`), 21
forced-fallback (noisy consensus incl. S with 36 samples — quality gates
doing their job), rest below sample floor.

- **Anti-copy: PASS** (bitmap-level byte-identity vs all 9 fleet atlases; 0
  matches — trivially true for a crop mint, run because the epic makes it
  mandatory).
- **Fleet identity gate: 8/27 MISRENDER** (`.` reads as L, N as f, 9 as 1,
  U as b, r as F, T as i, 7 as Z, 0 as L), 51 MISSING of the gate's chars.
  The misrenders are real (visible in the verify sheet and the native
  render), not the gate's known FP classes.
- **Coverage: 27/94.** Native-only rendering is legible but visibly damaged
  (digits especially — prices garble).

**Family affinity is weak and flat**: mean IoU vs fleet tops out at **vons
0.449 / innout 0.447 / traderjoes 0.445** (n=27), far below the family
threshold 0.60 (cvs~vons, the known family, sits at 0.62). With 8/27 of the
probe glyphs misrendered this is a lower bound, but as measured **no fleet
member qualifies as Smith's family** — borrowing is "nearest neighbor",
not "family member". The render borrow (below) is labeled accordingly.

## 3. Derived knobs (v1, no eyeballs) — **pass, with a cheap-vs-render split**

Real side (n=10 vetted): cap 23.0 px, OCR word_h 18.0 px, target density
0.2558. `calibrate_merchant` on the minted atlas: `ocr_cap_height_ratio =
0.95`, `weight_iters = 0`, cheap-space `bitmap_thin = 0.5` **CEILING-RAILED**
(projected density 0.278 vs 0.256 — the M3 mint-heaviness, mild at 1.09×),
coverage 0.525. Render-space derivation (`ink_calibration.derive_bitmap_thin`
over 3 vetted receipts, RECEIPT_PAPER_STRENGTH=0.3) lands **thin = 0.0** with
synth/real ratios 0.30–0.75 — i.e. in the real render the hybrid is too
*light*, because 48% of chars fall through to TTF fallback. Cheap-space and
render-space disagree in *direction* when coverage is this low: the cheap
model only scores atlas-covered chars. Lesson for the epic: **at coverage
< ~0.9 the cheap calibrate's density solve is not trustworthy; derive thin in
render space** (both are derivations; the pilot used the render-space one).

## 4. Face priors from QA'd sections — **pass**

stylescan measurement primitives per OCR line, joined to sections by QA'd
line_ids (no rule tables), normalized per receipt against its own ITEMS body;
8 cells, 7 with n≥3:

| section | n | scale | stroke | face |
|---|---|---|---|---|
| STOREFRONT | 26/10rcpt | **1.265** | **1.358** | **bold** |
| FOOTER | 35/8 | 1.105 | 1.181 | semibold |
| PAYMENT | 76/10 | 1.027 | 0.955 | normal |
| ADDRESS | 18/10 | 1.000 | 0.992 | normal |
| ITEMS | 136/10 | 1.000 | 1.000 | body |
| SUMMARY | 5/4 | 0.919 | 0.935 | normal |
| TOTAL_LINE | 6/5 | 1.000 | 1.037 | normal |
| SURVEY | 1/1 | — | — | LOW-CONF, unused |

Underlines ≈ 0 everywhere (Smith's prints none — like Vons, unlike Sprouts).
Encoded as `fonts/smiths/stylemap.json`; `receipt_stylemap.py` gained a
minimal measured `smiths` rule table (storefront + footer only; body
sections deliberately fall through since they measured body-normal).

## 5. Compose + render — **layout pass**

Composer capacity on 12 exported receipts: 4 scaffolds, 10-item non-taxable
catalog, 55% layout pass. 10 candidates generated; 6/10 passed the objective
scan (sum(LINE_TOTAL) == GRAND_TOTAL, no consecutive dup tokens, no header
dup). Chosen: 3 items (SB SPONGE 6.99 / SNLK SSM OIL 4.99 / HFNG CHILI SAUCE
3.29 = 15.27), section order inherited from the real scaffold: storefront →
address → cashier → items → tax/balance → payment (AID/TC/PIN) → items-sold →
date → barcode → fuel points → footer/recall. Two renders (760×2049,
true-aspect from the scaffold's scan, RECEIPT_PAPER_STRENGTH=0.3):

- **native+TTF**: honest but rough — the 8 misrenders garble digits/totals.
- **borrowed**: 19 native + **75 borrowed(vons, labeled per glyph in
  `borrow_labels.json`)** + 0 TTF. Reads correctly end to end; residual
  garbles are the *native* e/u/E/V glyphs.

## 6. Scorecard placement — **in-gate**

Real vetted distribution: density 0.230–0.276 (med 0.2558), h/W med 0.0615.

| render | density | ratio | h/W ratio |
|---|---|---|---|
| synth borrowed | 0.2708 | **1.059** | 1.048 |
| synth native | 0.2324 | **0.909** | 1.048 |

Both land **inside** the real corpus's receipt-to-receipt density range and
within the scorecard's 0.85–1.15 density gate; height ratio 1.05.

## Verdict (exit question)

| ingredient | grade | note |
|---|---|---|
| atlas (native) | **fail as solo / marginal as ingredient** | 27/94, 8 misrenders; corpus floor: ~8k crops ⇒ ~40 mintable chars. Usable only as the native core of a labeled hybrid. |
| knobs | **pass** | all derived; use render-space thin when coverage < 0.9 |
| face priors | **pass** | 7 confident cells; storefront-bold clearly measured and rendered |
| layout/compose | **pass** | 6/10 clean candidates from 4 scaffolds; section order faithful |
| **novel receipt** | **pass (with labeled borrowing)** | in-gate on ink metrics, visually plausible next to real scans |

**Cold start works as a *hybrid* recipe**: the thin corpus caps native glyph
yield (evidence floor ≈ 300–600 crops per char class for a full solo mint —
Smith's has 91 for its best char), but vetting + gates + derivation + QA'd
sections + fleet borrowing turn 10 receipts into an in-gate novel synth with
no hand-drawn glyphs and no hand-tuned constants. The failing ingredient is
precisely identified: **native mint coverage/quality below ~30 receipts**,
and the honest mitigation (labeled nearest-neighbor borrowing) is already
in-gate.

Repro: `py/m6_cold_start.py` subcommands (vet/gates/knobs/faces/borrow/score);
mint via `synthesis_loop/build_merchant_glyphs.py` with GLYPH_KEYS_JSON;
compose via `scripts/compose_synthetic_receipts.py` (feat/grid-discipline)
on the exported vetted receipts; render via `_render_cached_hybrid` with the
scratch profile in the harness docstring. Artifacts (atlases, renders,
per-receipt caches): `/private/tmp/m6_work/`.
