# Typography Runs Pilot — Findings

**Per-line font attribution + within-merchant typeface discovery, Wild Fork &
Sprouts.** Measurement + discovery only: no renderer changes, no Dynamo
writes, no publishes.

Reproduce:

```bash
cd tools/glyph-studio/py
python typography_runs.py            # WF + Sprouts, cached per receipt
```

Corpus: OCR-vetted receipts only (`ocr_overlap_score <= 2`, the M3 rule) —
Wild Fork 10/12 receipts (367 measured lines), Sprouts 12 of the first ~20
vetted (606 measured lines). Sections are the QA'd **VALID** `ReceiptSection`
rows in dev.

## Method (what "attribution" means here)

For every visual line with >= 4 usable letter crops: clean each crop
(neighbor-bleed / speck / rule-fragment removal — raw crops score ~0.1 IoU
vs atlas from bbox contamination alone, cleaned ~0.55), shape-normalize
(M2's aspect-preserving 32x32), and take the median per-letter shifted-IoU
against the merchant's compiled body font (`fonts/<slug>` via
`glyphstudio.compile`).

Two calibrations were **measured as necessary**, not optional:

1. **Per-char norms.** The traced atlases have per-char weaknesses (WF `.`
   medians 0.21, `e` 0.41 vs digits 0.67). Raw line medians false-flag
   lowercase/punct-heavy lines: WF initially "discovered" a second face
   spanning `Tender:` / `101 S. Westlake Blvd.` / `Number of items
   purchased` — ground-truth crops show these are the same body face. Judging
   each letter against its own char's corpus median dissolves the artifact.
2. **Per-receipt centering.** Absolute IoU is resolution-sensitive: a 9px-cap
   Sprouts scan (08665671) medians ~0.42 where 28px scans median ~0.52. A
   global threshold either misses that receipt's true outliers or floods the
   candidate pool with its body lines.

A line is a different-typeface candidate at calibrated deviation < −0.12
below its receipt's median; candidates are clustered by shared-letterform
IoU (single-linkage, >= 3 shared chars, >= 0.45).

## Attribution confidence

| merchant | lines | p5 | p25 | p50 | p75 | p95 |
|---|---|---|---|---|---|---|
| Wild Fork | 367 | 0.457 | 0.554 | 0.620 | 0.671 | 0.708 |
| Sprouts | 606 | 0.299 | 0.435 | 0.504 | 0.571 | 0.670 |

Body lines sit ~0.45–0.7; genuine other-face lines sit 0.19–0.35 **after**
calibration (raw IoU alone does not separate them from degraded body lines).

## Discovered typeface sets

### Wild Fork — second body face REFUTED (k=1, weak)

- **T0 (body)**: 362/367 lines. One mono-ish condensed sans throughout.
- **T1**: `Tender:` only (2 lines, 2 receipts, dev −0.18). Ground-truth crops
  show the same letterforms as body, sometimes printed larger (cap 53 vs 33).
  Verdict: **size/tier variation of the body face, not a second typeface.**
- The `WF` wordmark (cap ~189px) never has enough letters to attribute (n=2)
  — it is a logo, out of scope for line attribution.
- WF's real within-receipt variation is **tier**: 25 `large` lines (city
  header, `Tender:`, the wordmark) + tiers, not typefaces. The expectation
  "WF >= 2 real faces" is **not confirmed** on vetted receipts; the earlier
  impression likely came from the unvetted double-strike receipt 058b662d
  (ocr_overlap 17, excluded) and from raw-IoU char bias.

### Sprouts — body + 3 real display faces (k=6 clusters, 4 survive scrutiny)

| face | lines | receipts | reading |
|---|---|---|---|
| T0 body | 533 | 12 | mono receipt face |
| T1 | 16 | 9 | `1012 WESTLAKE BLVD.` / `WESTLAKE, CA 91361` — bold display address face (visually: heavier cut of a sans; face-vs-weight not disentangled) |
| T2 | 4 | **1** | blurred footer block on one receipt — local anomaly, not a face |
| T3 | 3 | 3 | `SPROUTS HOT SAVINGS!` / `VITAMINS & BODY CARE` — **heavy italic promo display** (verified in pixels) |
| T4 | 3 | 3 | `FARMERS MARKET` — **serif display** (the wordmark's subtitle) |
| T5 | 2 | 2 | `PRODUCE` — underline ink fused into glyph crops; artifact of underlined section headers, not a face |
| T6 | 2 | 2 | `9899999 980376` coupon-barcode caption digits — plausibly a distinct small digit face |

Exemplar sheets (atlas row vs discovered rows): `.out/typography/{wildfork,sprouts}/typeface_exemplars.png`.

**Known-answer checks:**

- `FARMERS MARKET` isolates (T4) — but only in 3 of 13 instances (the small,
  cap-18px printings). At cap ~30px it lands at dev −0.03..−0.11, just above
  the cut. **Partial pass.**
- The big `SPROUTS` wordmark (cap ~65px) **never** flags: serif caps
  downsampled to a 32px grid overlap mono caps at ~0.5 IoU — body level.
  32px shape-IoU is serif-blind at display sizes. This is the pilot's main
  discriminator limit (see follow-ups).
- The wordmark double-strike hazard shows up as intended: 23 Sprouts lines
  (address/payment rows with overlap-stamped OCR) are flagged `X`
  (contaminated, `intra_line_overlap` > 0.2) and **excluded** from discovery
  instead of being misattributed.

## Italic probe

Per-line slant = median shear-search angle over the line's cleaned glyphs,
excluding diagonal letterforms (a card-mask line of `X`s "leans" 14–26° with
no italic intent — measured, then excluded by char class).

- **Wild Fork: no italics.** Slant p5–p95 = [−2°, 0°]; zero candidates.
- **Sprouts: true italics exist, only in promo display lines**: `SPROUTS HOT
  SAVINGS!` 9° (x2 receipts), `VITAMINS & BODY CARE` 11°, `$5 OFF $30` 9°
  (a fourth, `10% OFF$75` at 16.5°, is contamination-excluded). Verified in
  pixels: these are brush-style oblique banner faces. Body text is upright
  everywhere (p5–p95 = [−1.5°, 1.3°]).

So: thermal receipt *body* text shows no italics; italic display faces do
occur in printed promo banners.

## Style runs vs semantic sections (the headline)

Runs = maximal contiguous lines sharing `(typeface, tier, underline,
reverse_video)`; unattributable lines are transparent. Cross-tab against QA'd
VALID sections, over sections with >= 1 measured line:

| merchant | sections | multi-run | **multi-style** | multi-typeface |
|---|---|---|---|---|
| Wild Fork | 71 | 25 (35%) | **16 (23%)** | 3 (4%) |
| Sprouts | 103 | 60 (58%) | **46 (45%)** | 29 (28%) |
| combined | 174 | 85 (49%) | **62 (36%)** | 32 (18%) |

(`multi-run` counts sections split by an intervening differently-styled
block; `multi-style` requires the section itself to contain >1 distinct
style — the direct evidence that sections do not determine typography.)

By section type (multi-style / measured): Sprouts STOREFRONT **12/12** (the
serif-wordmark + mono-address mix, exactly the motivating case), ADDRESS
10/11, PAYMENT 7/12, SECTION_HEADER 4/9, FOOTER 6/12; WF ADDRESS 8/10,
PAYMENT 7/9. ITEMS is typographically uniform at both merchants (0/10, 1/11)
— sections and typography agree in the middle of the receipt and diverge at
its display-styled edges.

**Conclusion: a per-line typography layer is not derivable from sections** —
36% of QA'd sections contain more than one typographic style, and one style
(T0-normal) spans many sections.

## Hazards & limits (explicit)

- Overlap-contaminated lines are excluded, never attributed (23 Sprouts, 0 WF
  after vetting).
- 32px shape-IoU is serif-blind at large cap heights (SPROUTS wordmark miss).
- Underlines fuse into glyph crops on section headers (T5 artifact) — the
  underline flag is measured separately and is correct; the *shape* channel
  double-counts it.
- Sub-line mixing is invisible: `Tender: VISA 19.50` mixes label + body in
  one visual line; runs are line-granular in this pilot.
- Single-receipt clusters (T2) are local print anomalies; require
  `n_receipts >= 2` before believing a discovered face.

## Recommendation: persistence schema for the follow-up

**Per-line attribute, not a style-run entity.** Concretely: one compact item
per receipt (e.g. `RECEIPT#...#TYPOGRAPHY`, mirroring the run-map JSON):
`line_id -> {typeface, tier, underline, reverse_video, slant_deg,
attribution, contaminated}` plus a per-merchant typeface registry
(`T0=body atlas @ face, T1..Tk -> exemplar npz in S3`).

Reasons:

1. Runs are a pure derivation of per-line labels (10 lines of code) — storing
   both invites drift; storing only runs loses the per-line confidences and
   the contaminated/unmeasured distinctions QA will need.
2. Sections taught this lesson already: line_ids-list entities (ReceiptSection)
   needed QA statuses and repair passes when line grouping shifted; per-line
   facts are stable under regrouping.
3. Consumers differ in granularity: the renderer wants runs, LayoutLM features
   want per-line/per-word, QA wants per-line score + flag. Per-line is the
   common denominator.

Follow-ups in order of value: (a) serif-sensitive features (stroke-end
widening / 48–64px grid) to catch display wordmarks; (b) word-level runs for
sub-line mixing; (c) fold T1-style "bold display" into the face/tier axis
via stroke-width joint modeling instead of letting it surface as a
pseudo-typeface.

## Artifacts (not committed)

- Run maps: `.out/typography/<slug>/run_maps/<image>_<rid>.json`
- Summary: `.out/typography/summary.json`
- Overlays (lines color-coded by typeface): `.out/typography/wildfork/viz_{15bd1c14_4,19a032ac_2,758cbedf_1}.png`, `.out/typography/sprouts/viz_{00ded398_2,04ebdb8a_1,069e270a_1}.png`
- Exemplar sheets: `.out/typography/<slug>/typeface_exemplars.png`
- Extraction cache (resumable): `.out/typography/cache/`
